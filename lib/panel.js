/**
 * dsh-mihome — Host 侧的 Web 面板路由。
 *
 * 侧栏工作台（client.js）不能直接调 Python：浏览器里没有 child_process。
 * 所以这一层把桥接进程的 op 原样暴露成同源 HTTP 接口，前端只做取数和渲染。
 *
 * 设计约束（都是踩过的坑）：
 * - 路由必须挂在 `ctx.inject(['webServer'], ...)` 里。webServer 服务可能根本
 *   不存在（非 Web 组合），那时整个面板静默不出现，工具层照常工作。
 * - 登录长轮询（最长约 2 分钟）会独占桥接进程。所以**登录用独立第二个桥接
 *   进程**，否则用户在面板上点「扫码登录」的那两分钟里，设备列表、开关全部
 *   排队超时。两个进程共享同一个 MIHOME_DATA_DIR，凭据由 mijiaAPI 写进
 *   ~/.config/mijia-api/auth.json，数据桥接按 mtime 变化自动重建服务。
 * - 所有路由都做同源校验：HOST 必须指向本机监听地址，Origin 若存在也必须
 *   同源。否则任何网页都能通过浏览器驱动用户家里的真实设备。
 *
 * @module dsh-mihome/lib/panel
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";

/** 前端取数的固定前缀。 */
const API = "/dsh-mihome/api";

/** 请求体上限：面板只发小 JSON，64KB 足够，防止内存被灌。 */
const BODY_LIMIT = 64 * 1024;

/* ------------------------------------------------------------------ plumbing */

function json(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "content-length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function hostnameOfHost(host) {
  try {
    return new URL(`http://${host}`).hostname;
  } catch {
    return "";
  }
}

function isLocalHostname(value) {
  const v = String(value).toLowerCase().replace(/^\[|\]$/g, "");
  return v === "localhost" || v === "127.0.0.1" || v === "::1" || v.startsWith("127.");
}

function hostMatchesListener(host, localAddress) {
  if (!host || !String(host).trim()) return false;
  const hostname = hostnameOfHost(host);
  if (isLocalHostname(hostname)) return true;
  if (hostname === "0.0.0.0" || hostname === "[::]") return false;
  if (!localAddress) return false;
  const bare = (x) => (x.startsWith("[") && x.endsWith("]") ? x.slice(1, -1) : x);
  return bare(String(hostname).toLowerCase()) === bare(String(localAddress).toLowerCase());
}

function originTrusted(origin, localAddress) {
  if (!origin || !String(origin).trim()) return true;
  try {
    return hostMatchesListener(new URL(origin).host, localAddress);
  } catch {
    return false;
  }
}

function trusted(req, res) {
  if (!hostMatchesListener(req.headers.host, req.socket?.localAddress)) {
    json(res, 403, { error: "host-not-trusted" });
    return false;
  }
  if (!originTrusted(req.headers.origin, req.socket?.localAddress)) {
    json(res, 403, { error: "origin-not-trusted" });
    return false;
  }
  return true;
}

function readJsonBody(req, maxBytes = BODY_LIMIT) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    let done = false;
    req.on("data", (chunk) => {
      if (done) return;
      size += chunk.length;
      if (size > maxBytes) {
        done = true;
        reject(new Error("request body too large"));
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (done) return;
      done = true;
      const text = Buffer.concat(chunks).toString("utf8");
      if (!text.trim()) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(text));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", (error) => {
      if (done) return;
      done = true;
      reject(error);
    });
  });
}

function queryOf(req) {
  try {
    return new URL(req.url ?? "/", "http://localhost").searchParams;
  } catch {
    return new URLSearchParams();
  }
}

/**
 * 把桥接异常压成前端能直接展示的一段话。
 *
 * need_login 单独成码，前端据此切到登录卡；其余保留原文（Python 侧的报错
 * 已经带了足够上下文，比如上游 code/message）。
 */
function describe(error) {
  const code = error?.code ?? "error";
  const message = error?.message ?? String(error);
  if (code === "need_login") {
    return { code, error: message, hint: "米家账号未登录，请先在面板里扫码登录。" };
  }
  if (code === "dependency_missing") {
    return {
      code,
      error: message,
      hint: "插件缺少 Python 依赖，请在插件目录执行 .venv\\Scripts\\python.exe -m pip install \"mijiaAPI>=5,<6\"",
    };
  }
  return { code, error: message };
}

/* -------------------------------------------------------------------- routes */

/**
 * 挂载全部面板路由。
 *
 * @param {import("@deepseek-ai/cordis").Context} ctx 插件上下文（已含 webServer）
 * @param {{ bridge: any, loginBridge: any, qrDir: string,
 *           requestTimeoutMs: number, loginTimeoutMs: number }} opts
 */
export function mountPanelRoutes(ctx, opts) {
  const { bridge, loginBridge, qrDir, dataDir, requestTimeoutMs, loginTimeoutMs } = opts;
  // 产品图标落在 <data>/icons（与 Python 侧 icon_store 同一个目录）
  const iconDir = join(dataDir ?? join(qrDir, ".."), "icons");

  ctx.effect(() => {
    const disposers = [];
    const route = (entry) => {
      disposers.push(ctx.webServer.register(entry));
    };
    const wrap = (spec, run) =>
      route({
        kind: "exact",
        path: spec.path,
        handler: async (req, res) => {
          if (req.method !== spec.method) {
            res.writeHead(405, { allow: spec.method });
            res.end();
            return;
          }
          if (!trusted(req, res)) return;
          try {
            await run(req, res);
          } catch (error) {
            json(res, 500, describe(error));
          }
        },
      });

    // ---- 状态 -------------------------------------------------------
    wrap({ path: `${API}/status`, method: "GET" }, async (_req, res) => {
      let status = { logged_in: false, api_version: "", data_dir: "" };
      let problem = null;
      try {
        status = await bridge.request("status", {}, requestTimeoutMs);
      } catch (error) {
        problem = describe(error);
      }
      json(res, 200, {
        ok: true,
        logged_in: Boolean(status.logged_in),
        api_version: status.api_version ?? "",
        data_dir: status.data_dir ?? "",
        python: bridge.check(),
        problem,
      });
    });

    // ---- 设备 -------------------------------------------------------
    wrap({ path: `${API}/devices`, method: "GET" }, async (_req, res) => {
      const result = await bridge.request("devices", {}, requestTimeoutMs);
      json(res, 200, { ok: true, devices: Array.isArray(result.devices) ? result.devices : [] });
    });

    // 电源状态单独一条：它要逐台过云端，慢且可能个别设备超时，不能拖累列表首屏
    wrap({ path: `${API}/power`, method: "GET" }, async (req, res) => {
      const dids = (queryOf(req).get("dids") ?? "").split(",").map((x) => x.trim()).filter(Boolean);
      if (!dids.length) {
        json(res, 200, { ok: true, states: {} });
        return;
      }
      try {
        const result = await bridge.request("power_states", { dids }, requestTimeoutMs);
        json(res, 200, { ok: true, states: result.states ?? {} });
      } catch (error) {
        json(res, 200, { ok: false, states: {}, ...describe(error) });
      }
    });

    wrap({ path: `${API}/device`, method: "GET" }, async (req, res) => {
      const did = queryOf(req).get("did") ?? "";
      if (!did) {
        json(res, 400, { error: "需要 did" });
        return;
      }
      const detail = await bridge.request("device_detail", { did }, requestTimeoutMs);
      json(res, 200, { ok: true, detail });
    });

    // 展开设备时一次性读出全部可读属性，省得前端逐个读
    wrap({ path: `${API}/values`, method: "GET" }, async (req, res) => {
      const did = queryOf(req).get("did") ?? "";
      if (!did) {
        json(res, 400, { error: "需要 did" });
        return;
      }
      const detail = await bridge.request("device_detail", { did }, requestTimeoutMs);
      const names = (detail?.props ?? []).filter((p) => p?.readable).map((p) => p.name);
      let values = {};
      if (names.length) {
        try {
          const result = await bridge.request("read_props", { did, names }, requestTimeoutMs);
          values = result.values ?? {};
        } catch (error) {
          json(res, 200, { ok: false, values: {}, ...describe(error) });
          return;
        }
      }
      json(res, 200, { ok: true, values });
    });

    // ---- 场景 / 耗材 -------------------------------------------------
    wrap({ path: `${API}/scenes`, method: "GET" }, async (_req, res) => {
      const result = await bridge.request("scenes", {}, requestTimeoutMs);
      json(res, 200, { ok: true, scenes: Array.isArray(result.scenes) ? result.scenes : [] });
    });

    wrap({ path: `${API}/consumables`, method: "GET" }, async (_req, res) => {
      const result = await bridge.request("consumables", {}, requestTimeoutMs);
      json(res, 200, {
        ok: true,
        items: Array.isArray(result.items) ? result.items : [],
        total: Number(result.total ?? 0),
        low_count: Number(result.low_count ?? 0),
      });
    });

    // ---- 写操作（会真的改设备状态）-------------------------------------
    wrap({ path: `${API}/control`, method: "POST" }, async (req, res) => {
      const body = await readJsonBody(req);
      const did = String(body.did ?? "");
      const mode = String(body.mode ?? "");
      if (!did) {
        json(res, 400, { error: "需要 did" });
        return;
      }
      if (mode === "power") {
        const payload = { did };
        if (typeof body.state === "boolean") payload.state = body.state;
        const result = await bridge.request("power", payload, requestTimeoutMs);
        json(res, 200, { ok: true, mode, did, on: Boolean(result.on) });
        return;
      }
      if (mode === "write") {
        if (!body.name) {
          json(res, 400, { error: "需要 name" });
          return;
        }
        if (body.value === undefined) {
          json(res, 400, { error: "需要 value" });
          return;
        }
        await bridge.request(
          "write_prop",
          { did, name: String(body.name), value: body.value },
          requestTimeoutMs,
        );
        json(res, 200, { ok: true, mode, did, name: String(body.name), value: body.value });
        return;
      }
      if (mode === "action") {
        if (!body.name) {
          json(res, 400, { error: "需要 name" });
          return;
        }
        const payload = { did, name: String(body.name) };
        if (body.params && typeof body.params === "object") payload.params = body.params;
        await bridge.request("action", payload, requestTimeoutMs);
        json(res, 200, { ok: true, mode, did, name: String(body.name) });
        return;
      }
      json(res, 400, { error: `未知 mode：${mode}（可选 power / write / action）` });
    });

    wrap({ path: `${API}/scene`, method: "POST" }, async (req, res) => {
      const body = await readJsonBody(req);
      const sceneId = String(body.scene_id ?? "");
      const homeId = String(body.home_id ?? "");
      if (!sceneId || !homeId) {
        json(res, 400, { error: "需要 scene_id 与 home_id" });
        return;
      }
      const payload = { scene_id: sceneId, home_id: homeId };
      if (body.name) payload.name = String(body.name);
      await bridge.request("run_scene", payload, requestTimeoutMs);
      json(res, 200, { ok: true, scene_id: sceneId });
    });

    // ---- 登录 -------------------------------------------------------
    wrap({ path: `${API}/login/start`, method: "POST" }, async (_req, res) => {
      const result = await loginBridge.request("login_begin", {}, loginTimeoutMs);
      if (result.need_scan === false) {
        json(res, 200, { ok: true, need_scan: false, logged_in: Boolean(result.logged_in) });
        return;
      }
      const png = String(result.qr_png ?? "");
      const qrUrl = png ? `${API}/qr?name=${encodeURIComponent(basename(png))}` : "";
      json(res, 200, {
        ok: true,
        need_scan: true,
        login_url: result.login_url ?? "",
        qr_url: qrUrl,
        expires_in: result.expires_in ?? null,
        login_data: result.login_data ?? null,
      });
    });

    wrap({ path: `${API}/login/poll`, method: "POST" }, async (req, res) => {
      const body = await readJsonBody(req);
      if (!body.login_data) {
        json(res, 400, { error: "需要 login_data" });
        return;
      }
      try {
        const result = await loginBridge.request(
          "login_wait",
          { login_data: body.login_data },
          loginTimeoutMs + 5_000,
        );
        json(res, 200, { ok: true, logged_in: Boolean(result.logged_in) });
      } catch (error) {
        json(res, 200, { ok: false, logged_in: false, ...describe(error) });
      }
    });

    // 二维码图片本身。只允许 qr 目录下的 mihome-login-*.png，
    // 文件名先经 basename 去掉任何路径成分，再白名单校验后缀。
    wrap({ path: `${API}/qr`, method: "GET" }, async (req, res) => {
      const name = basename(queryOf(req).get("name") ?? "");
      if (!/^mihome-login-[A-Za-z0-9_-]+\.png$/.test(name)) {
        json(res, 400, { error: "bad-name" });
        return;
      }
      const file = join(qrDir, name);
      if (!existsSync(file)) {
        json(res, 404, { error: "not-found" });
        return;
      }
      const bytes = readFileSync(file);
      res.writeHead(200, {
        "content-type": "image/png",
        "cache-control": "no-store",
        "content-length": bytes.length,
      });
      res.end(bytes);
    });

    // ---- 产品图 / 家庭 / 产品名 ---------------------------------------
    // 米家 APP 的首页靠产品图区分设备，纯文字列表看不出是什么东西。
    wrap({ path: `${API}/icons`, method: "GET" }, async (req, res) => {
      const models = (queryOf(req).get("models") ?? "").split(",").map((x) => x.trim()).filter(Boolean);
      if (!models.length) {
        json(res, 400, { error: "需要 models" });
        return;
      }
      // 下载可能要做网络往返，失败只是没图，不该让整页报错
      let icons = {};
      try {
        const result = await bridge.request("icons", { models }, requestTimeoutMs);
        icons = result.icons ?? {};
      } catch (error) {
        json(res, 200, { ok: false, icons: {}, ...describe(error) });
        return;
      }
      const out = {};
      const byModel = (m) => `/dsh-mihome/api/icon?name=${encodeURIComponent(icons[m] ?? "")}`;
      for (const model of models) {
        if (icons[model]) out[model] = byModel(model);
      }
      json(res, 200, { ok: true, icons: out });
    });

    // 图标二进制。文件名白名单：只允许 <model>.png，且 model 里不允许路径成分。
    wrap({ path: `${API}/icon`, method: "GET" }, async (req, res) => {
      const name = basename(queryOf(req).get("name") ?? "");
      if (!/^[A-Za-z0-9._-]+\.png$/.test(name)) {
        json(res, 400, { error: "bad-name" });
        return;
      }
      const file = join(iconDir, name);
      if (!existsSync(file)) {
        json(res, 404, { error: "not-found" });
        return;
      }
      const bytes = readFileSync(file);
      res.writeHead(200, {
        "content-type": "image/png",
        "cache-control": "public, max-age=86400",
        "content-length": bytes.length,
      });
      res.end(bytes);
    });

    wrap({ path: `${API}/homes`, method: "GET" }, async (_req, res) => {
      try {
        const result = await bridge.request("homes", {}, requestTimeoutMs);
        json(res, 200, { ok: true, homes: Array.isArray(result.homes) ? result.homes : [] });
      } catch (error) {
        json(res, 200, { ok: false, homes: [], ...describe(error) });
      }
    });

    return () => {
      for (const off of disposers) {
        if (typeof off === "function") {
          try {
            off();
          } catch {
            /* 已卸载 */
          }
        }
      }
    };
  }, "dsh-mihome: panel routes");
}
