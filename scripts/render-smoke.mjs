/**
 * 前端冒烟：用最小 React 替身把 client.js 真正渲染成 HTML 字符串。
 *
 * 为什么需要它：DSH 的插件前端 bundle 走 dsh-app:// 协议，HTTP 探测恒 404，
 * 也没法在宿主窗口里点按钮。所以这里自己实现 createElement + hooks，
 * 把面板渲染两遍（首页 → 点设备进详情页），把 undefined 变量、错误的 hook
 * 调用、NaN 样式之类的问题在 Node 里就暴露出来。
 */
import { mkdirSync, readFileSync } from "node:fs";
import vm from "node:vm";

const file = process.argv[2];
mkdirSync('scripts/.out', { recursive: true });
const src = readFileSync(file, "utf8");

/* ----------------------------------------------------------- 假 React */

const stateStore = new Map();
const effectStore = new Map();
let effectQueue = [];
let path = "";
let hookIndex = 0;
let dirty = false;

function withPath(next, fn) {
  const savedPath = path;
  const savedIndex = hookIndex;
  path = next;
  hookIndex = 0;
  try {
    return fn();
  } finally {
    path = savedPath;
    hookIndex = savedIndex;
  }
}

const key = () => `${path}#${hookIndex++}`;

function useState(init) {
  const k = key();
  if (!stateStore.has(k)) stateStore.set(k, typeof init === "function" ? init() : init);
  const set = (value) => {
    const prev = stateStore.get(k);
    const next = typeof value === "function" ? value(prev) : value;
    if (next !== prev) {
      stateStore.set(k, next);
      dirty = true;
    }
  };
  return [stateStore.get(k), set];
}

function useEffect(fn, deps) {
  const k = key();
  const prev = effectStore.get(k);
  const changed =
    !prev || !deps || !prev.deps || deps.length !== prev.deps.length ||
    deps.some((d, i) => d !== prev.deps[i]);
  if (changed) {
    effectStore.set(k, { deps, cleanup: prev?.cleanup });
    effectQueue.push({ k, fn });
  }
}

function useCallback(fn, deps) {
  const k = key();
  const prev = effectStore.get(`cb:${k}`);
  if (prev && deps && prev.deps && deps.every((d, i) => d === prev.deps[i])) return prev.fn;
  effectStore.set(`cb:${k}`, { deps, fn });
  return fn;
}

function useRef(init) {
  const k = key();
  if (!stateStore.has(`ref:${k}`)) stateStore.set(`ref:${k}`, { current: init });
  return stateStore.get(`ref:${k}`);
}

const React = {
  createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
  useState,
  useEffect,
  useCallback,
  useRef,
  Fragment: "fragment",
};

/* --------------------------------------------------------------- 渲染 */

const VOID_TAGS = new Set(["img", "input", "br", "hr"]);

const css = (style) => {
  if (!style) return "";
  const parts = [];
  for (const [k, v] of Object.entries(style)) {
    if (v === undefined || v === null || v === false) continue;
    if (typeof v === "number" && !/^(opacity|zIndex|flexGrow|flexShrink|lineHeight)$/.test(k)) {
      parts.push(`${k}:${v}px`);
    } else {
      parts.push(`${k}:${v}`);
    }
  }
  return parts.join(";");
};

const esc = (s) =>
  String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const rendered = [];

function render(node, pathSoFar) {
  if (node === null || node === undefined || node === false || node === true) return "";
  if (Array.isArray(node)) return node.map((n, i) => render(n, `${pathSoFar}[${i}]`)).join("");
  if (typeof node === "string" || typeof node === "number") return esc(node);
  if (typeof node === "function") {
    const name = node.name || "anon";
    return withPath(`${pathSoFar}/${name}`, () => render(node(node.__props ?? {}), `${pathSoFar}/${name}`));
  }
  const { type, props, children } = node;
  if (typeof type === "function") {
    const name = type.name || "anon";
    return withPath(`${pathSoFar}/${name}`, () => render(type(props), `${pathSoFar}/${name}`));
  }
  if (type === "fragment" || type === undefined) {
    return (children ?? []).map((c, i) => render(c, `${pathSoFar}#${i}`)).join("");
  }
  const attrs = [];
  for (const [k, v] of Object.entries(props ?? {})) {
    if (k === "children" || k === "key" || typeof v === "function" || v === undefined) continue;
    if (k === "style") {
      const s = css(v);
      if (s) attrs.push(`style="${esc(s)}"`);
      continue;
    }
    if (k === "className") {
      attrs.push(`class="${esc(v)}"`);
      continue;
    }
    if (v === true) {
      attrs.push(k);
      continue;
    }
    if (v === false || v === null) continue;
    attrs.push(`${k}="${esc(v)}"`);
  }
  const inner = (children ?? []).map((c, i) => render(c, `${pathSoFar}>${type}#${i}`)).join("");
  rendered.push({ tag: type, props: props ?? {}, children, path: pathSoFar });
  const open = `<${type}${attrs.length ? " " + attrs.join(" ") : ""}>`;
  if (VOID_TAGS.has(type)) return open;
  return `${open}${inner}</${type}>`;
}

async function renderTree(vnode, label, passes = 12) {
  let html = "";
  for (let i = 0; i < passes; i += 1) {
    // 让上一轮 effect 里起的 fetch 承诺先落地，再重新渲染
    await new Promise((resolve) => setTimeout(resolve, 0));
    dirty = false;
    effectQueue = [];
    rendered.length = 0;
    html = render(vnode, label);
    const queue = effectQueue;
    effectQueue = [];
    for (const item of queue) {
      try {
        item.fn();
      } catch (error) {
        console.error(`effect failed at ${item.k}: ${error.message}`);
      }
    }
    // 等 fetch 承诺落地（fake fetch 是同步 resolve 的微任务）
    if (!dirty && !queue.length) break;
  }
  return html;
}

/* ------------------------------------------------------------ 假网络 */

let fetchCalls = [];

const DEVICES = [
  {
    did: "917079910",
    name: "空调插座",
    model: "lumi.acpartner.mcn02",
    home_name: "我的家",
    room_name: "卧室",
    online: true,
  },
  {
    did: "1052370002",
    name: "小米智能门铃3",
    model: "madv.cateye.miowl3",
    home_name: "我的家",
    room_name: "客厅",
    online: true,
  },
  {
    did: "blt.3.1fonis0j4kk00",
    name: "米家智能激光测距仪",
    model: "hoto.etool.mf01",
    home_name: "我的家",
    room_name: "客厅",
    online: false,
  },
];

const AC_PROPS = [
  { name: "on", desc: "开关", type: "bool", readable: true, writable: true, range: null, value_list: null, unit: "", advanced: false },
  {
    name: "mode",
    desc: "模式",
    type: "uint",
    readable: true,
    writable: true,
    range: null,
    value_list: [
      { value: 0, description: "自动" },
      { value: 1, description: "制冷" },
      { value: 2, description: "除湿" },
      { value: 3, description: "制热" },
      { value: 4, description: "送风" },
    ],
    unit: "",
    advanced: false,
  },
  { name: "target-temperature", desc: "目标温度", type: "float", readable: true, writable: true, range: [16, 30, 1], value_list: null, unit: "°C", advanced: false },
  { name: "fan-level", desc: "风速", type: "uint", readable: true, writable: true, range: null, value_list: [{ value: 0, description: "自动" }, { value: 1, description: "低" }, { value: 2, description: "中" }, { value: 3, description: "高" }], unit: "", advanced: false },
  { name: "vertical-swing", desc: "上下扫风", type: "bool", readable: true, writable: true, range: null, value_list: null, unit: "", advanced: false },
  { name: "electric-power", desc: "电功率", type: "float", readable: true, writable: false, range: [0, 10000, 0.01], value_list: null, unit: "W", advanced: false },
  { name: "ac-state", desc: "空调状态", type: "string", readable: true, writable: false, range: null, value_list: null, unit: "", advanced: true },
];

const ROUTES = (url) => {
  const u = String(url);
  if (u.includes("/status")) return { ok: true, logged_in: true, api_version: "5.0.0" };
  if (u.includes("/devices")) return { ok: true, devices: DEVICES };
  if (u.includes("/homes"))
    return {
      ok: true,
      homes: [
        {
          id: "569001052736",
          name: "我的家",
          dids: [],
          rooms: [
            { id: "r1", name: "卧室", dids: ["917079910"] },
            { id: "r2", name: "客厅", dids: ["1052370002", "blt.3.1fonis0j4kk00"] },
          ],
        },
      ],
    };
  if (u.includes("/icons")) return { ok: true, icons: { "lumi.acpartner.mcn02": "/dsh-mihome/api/icon?name=lumi.acpartner.mcn02.png" } };
  if (u.includes("/power?")) return { ok: true, states: { "917079910": true } };
  if (u.includes("/scenes"))
    return {
      ok: true,
      scenes: [
        { scene_id: "s1", name: "回家", home_id: "569001052736", home_name: "我的家" },
        { scene_id: "s2", name: "晚安", home_id: "569001052736", home_name: "我的家" },
      ],
    };
  if (u.includes("/consumables"))
    return {
      ok: true,
      total: 3,
      low_count: 1,
      items: [
        { did: "917079910", name: "空调插座", description: "滤芯", type: "filter", value: 12, threshold: 20, low: true, updated: 1795100000 },
        { did: "917079910", name: "空调插座", description: "边刷", type: "brush", value: 68, threshold: 20, low: false, updated: 1795000000 },
        { did: "1052370002", name: "小米智能门铃3", description: "电池", type: "battery", value: 0.42, threshold: 15, low: false, updated: 1794900000 },
      ],
    };
  if (u.includes("/device?")) return { ok: true, detail: { did: "917079910", name: "空调插座", props: AC_PROPS, actions: [{ name: "set-ac-model", desc: "设置空调型号" }] } };
  if (u.includes("/values?")) return { ok: true, values: { on: true, mode: 1, "target-temperature": 26, "fan-level": 2, "vertical-swing": false, "electric-power": 1.91, "ac-state": "P1_M4_S1_D1" } };
  return { ok: true };
};

globalThis.fetch = async (url) => {
  fetchCalls.push(String(url));
  const body = ROUTES(url);
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
};

/* -------------------------------------------------------------- 加载 */

let registered = null;

const sandbox = {
  React,
  require: (id) => {
    if (id === "react") return React;
    throw new Error(`unexpected require: ${id}`);
  },
  console,
  fetch: globalThis.fetch,
  navigator: { language: "zh-CN" },
  document: undefined,
  setTimeout,
  clearTimeout,
  URL,
  Promise,
  Error,
  Math,
  JSON,
  String,
  Number,
  Boolean,
  Array,
  Object,
  Map,
  Set,
};

sandbox.window = {
  __ModuleLoader__: {
    load: (mod) => {
      registered = mod;
    },
  },
};
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: file });

if (!registered) {
  console.error("FAIL: client.js 没有调用 window.__ModuleLoader__.load");
  process.exit(1);
}

const mod = registered.factory(sandbox.require);
const page = mod.apply.length >= 0 ? mod : null;

/* --------------------------------------------------------- 假 ctx 注册 */

const slots = { rows: [], mains: [] };

const ctx = {
  locale: {
    // 真实宿主里 ctx.locale.bind(ns) 返回一个做键查找的 t()，
    // 这里必须照做——否则拿到的是恒等函数，中文全变成键名。
    register: () => () => {},
    bind: () => (k) => (Object.prototype.hasOwnProperty.call(ZH, k) ? ZH[k] : k),
  },
  effect: (fn) => {
    const off = fn();
    return () => off?.();
  },
  slots: {
    inject: (name, factory) => factory(),
    register: (options, component) => {
      if (options.name === "sidebar.panellist") slots.rows.push({ options, component });
      if (options.name === "main") slots.mains.push({ options, component });
      return () => {};
    },
  },
};

if (typeof mod.apply !== "function") {
  console.error("FAIL: 模块没有 apply()");
  process.exit(1);
}
mod.apply(ctx);

if (!slots.rows.length) {
  console.error("FAIL: 没有注册 sidebar.panellist 行");
  process.exit(1);
}
if (!slots.mains.length) {
  console.error("FAIL: 没有注册 main 面板");
  process.exit(1);
}

/* ---------------------------------------------------------- 渲染首页 */

/* 从源码里抠出 DICT.zh，让 t() 返回真实中文，否则只能匹配键名 */
function extractDict(source) {
  const start = source.indexOf("const DICT = {");
  if (start < 0) throw new Error("找不到 DICT");
  let i = source.indexOf("{", start);
  let depth = 0;
  let inStr = null;
  for (; i < source.length; i += 1) {
    const ch = source[i];
    if (inStr) {
      if (ch === "\\") i += 1;
      else if (ch === inStr) inStr = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") inStr = ch;
    else if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return vm.runInNewContext(`(${source.slice(start + 13, i + 1)})`);
    }
  }
  throw new Error("DICT 没有闭合");
}

const DICT = extractDict(src);
const ZH = DICT.zh ?? {};
const t = (k) => (Object.prototype.hasOwnProperty.call(ZH, k) ? ZH[k] : k);

const fs = await import("node:fs");

let html = await renderTree(slots.mains[0].component({ t }), "root");
const need = ["我的家", "卧室", "客厅", "空调插座", "小米智能门铃3", "离线", "已开启"];
const missing = need.filter((x) => !html.includes(x));
fs.writeFileSync("scripts/.out/out1.html", html);
console.log("pass1 len:", html.length, "缺失:", JSON.stringify(missing));

// 首页的页签节点要在点进设备前抓住：点进去之后 openDevice 有值，重渲染就是详情页了。
const homeTabs = rendered.filter((n) => n.props?.role === "tab" && typeof n.props.onClick === "function");
console.log("首页页签数:", homeTabs.length);

const tiles = rendered.filter((n) => n.props?.role === "button" && typeof n.props.onClick === "function");
console.log("可点击磁贴数:", tiles.length);

let detailHtml = "";
if (tiles.length) {
  // 点第一台（空调插座）
  await tiles[0].props.onClick();
  detailHtml = await renderTree(slots.mains[0].component({ t }), "root");
  fs.writeFileSync("scripts/.out/out2.html", detailHtml);
  const need2 = ["电源", "常用", "模式", "制冷", "目标温度", "风速", "当前状态", "电功率", "更多操作", "设置空调型号", "高级设置", "返回"];
  const missing2 = need2.filter((x) => !detailHtml.includes(x));
  console.log("pass2 len:", detailHtml.length, "缺失:", JSON.stringify(missing2));
}

const bad = /undefined|NaN|\[object Object\]/.test(html + detailHtml);
console.log("含 undefined/NaN/[object Object]:", bad);
console.log("fetch 次数:", fetchCalls.length);

/* ------------------------------------------------- 页签：场景 / 耗材 */

// 首页渲染完时 collected 里的 role="tab" 按钮就是三个页签
let sceneHtml = "";
let consHtml = "";
const tabs = homeTabs;
console.log("页签数:", tabs.length);
if (tabs.length === 3) {
  // 上面点进了设备详情，先点「‹ 返回」回到首页，否则重渲染仍是详情页。
  // 注意：createElement 把子节点放在 node.children，不在 props.children。
  const backBtn = rendered.find(
    (n) =>
      n.tag === "button" &&
      typeof n.props?.onClick === "function" &&
      String(n.children?.[0] ?? "").includes("返回"),
  );
  if (backBtn) {
    await backBtn.props.onClick();
    await renderTree(slots.mains[0].component({ t }), "root");
  }
  const sceneTab = tabs[1];
  const consTab = tabs[2];
  await sceneTab.props.onClick();
  sceneHtml = await renderTree(slots.mains[0].component({ t }), "root");
  fs.writeFileSync("scripts/.out/out3.html", sceneHtml);
  const need3 = ["场景", "回家", "晚安", "执行", "米家里怎么加场景", "手动执行"];
  const missing3 = need3.filter((x) => !sceneHtml.includes(x));
  console.log("pass3 场景页 len:", sceneHtml.length, "缺失:", JSON.stringify(missing3));

  await consTab.props.onClick();
  consHtml = await renderTree(slots.mains[0].component({ t }), "root");
  fs.writeFileSync("scripts/.out/out4.html", consHtml);
  const need4 = ["滤芯", "边刷", "电池", "12%", "68%", "42%", "需要更换", "建议更换线", "耗材管理"];
  const missing4 = need4.filter((x) => !consHtml.includes(x));
  console.log("pass4 耗材页 len:", consHtml.length, "缺失:", JSON.stringify(missing4));
  globalThis.__missing34 = [...missing3, ...missing4];
}

const bad2 = /undefined|NaN|\[object Object\]/.test(sceneHtml + consHtml);
console.log("页签含 undefined/NaN/[object Object]:", bad2);

const missing34 = globalThis.__missing34 ?? ["页签未渲染"];
const ok = !bad && !bad2 && !missing.length && !missing34.length && detailHtml.length > 0;
console.log(ok ? "SMOKE OK" : "SMOKE FAIL");
process.exit(ok ? 0 : 1);
