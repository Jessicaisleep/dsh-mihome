/**
 * dsh-mihome — DeepSeek Harness 米家（Mi Home）插件。
 *
 * 提供一组面向模型的工具：看设备、读属性、开关、执行场景、看温湿度等
 * 传感器读数、扫码登录。真正的米家协议由 mijiaAPI（Python）负责，
 * 这里通过常驻 JSON-Lines 子进程桥接（见 lib/bridge.js 与
 * python/mihome_bridge.py）。
 *
 * 为什么分成 Python 桥接 + Node 插件：米家登录（小米账号扫码、token 刷新、
 * 设备 spec 解析）已经有成熟实现在 mijiaAPI 里；在 Node 里重写一遍等于
 * 重做加密与协议细节。插件只做工具层与进程编排。
 *
 * @module dsh-mihome
 */

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import z from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { Bridge } from "./bridge.js";
import { credentialKey } from "@deepseek-ai/dsh-credentials";
import { createConnections } from "./connections.js";
import { formatDeviceList, formatDeviceDetail, formatScenes, formatSmartHomeList, formatSmartHomeDetail, formatSmartHomeScenes } from "./format.js";
import { mountPanelRoutes } from "./panel.js";

/** Cordis 插件名，loader 诊断用。 */
const name = "mihome";

/** 只依赖工具服务与系统提示词——没有它们插件没什么可做的。 */
const inject = ["tools", "systemPrompt", "credentials"];

const PLUGIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const DEFAULT_CONFIG = {
  pythonPath: "",
  dataDir: "",
  requestTimeoutMs: 30_000,
  loginTimeoutMs: 150_000,
};

/** 由 profile 的 patch 行填充的可调配置。 */
const Config = z.object({
  pythonPath: z.string().default(""),
  dataDir: z.string().default(""),
  requestTimeoutMs: z.number().default(30_000),
  loginTimeoutMs: z.number().default(150_000),
});

/** 默认解释器：插件自带 venv；不存在时回退到系统 python，让报错更容易看懂。 */
function resolvePython(config) {
  if (config.pythonPath) return config.pythonPath;
  const inVenv = process.platform === "win32"
    ? join(PLUGIN_ROOT, ".venv", "Scripts", "python.exe")
    : join(PLUGIN_ROOT, ".venv", "bin", "python");
  return inVenv;
}

/** 未登录是常态（新装/换账号），要把「去登录」这条路径明确交给模型。 */
function describeError(error) {
  if (error?.code === "need_login") {
    return `${error.message}\n\n米家账号未登录。请调用 mihome_login（action=start）获取二维码，` +
      `用户用米家 APP 扫码后再调用 mihome_login（action=poll）。`;
  }
  if (error?.code === "dependency_missing") {
    return `${error.message}\n\n插件缺少 Python 依赖。请在插件目录执行：\n` +
      `.venv\\Scripts\\python.exe -m pip install "mijiaAPI>=5,<6" "qrcode>=8"`;
  }
  return error?.message ?? String(error);
}

const STATUS_ACTION = {
  type: "string",
  required: true,
  description: "start (生成登录二维码) | poll (等扫码结果，需要 login_data) | status (当前是否已登录)",
};

/**
 * 注册全部工具。
 *
 * 每个工具注册都单独 try/catch：一个工具名冲突不该把整个插件加载搞失败，
 * 其余工具仍然可用。
 */
function apply(ctx, config) {
  const resolved = { ...DEFAULT_CONFIG, ...(config ?? {}) };
  const requestTimeoutMs = Number.isFinite(resolved.requestTimeoutMs) && resolved.requestTimeoutMs > 0
    ? resolved.requestTimeoutMs
    : DEFAULT_CONFIG.requestTimeoutMs;
  const loginTimeoutMs = Number.isFinite(resolved.loginTimeoutMs) && resolved.loginTimeoutMs > 0
    ? resolved.loginTimeoutMs
    : DEFAULT_CONFIG.loginTimeoutMs;

  const dataDir = resolved.dataDir || join(PLUGIN_ROOT, "data");
  const pythonExe = resolvePython(resolved);
  const script = join(PLUGIN_ROOT, "python", "mihome_bridge.py");
  const cwd = join(PLUGIN_ROOT, "python");

  const connections = createConnections(ctx.credentials, credentialKey("mihome", "devices"));
  const bridge = connections.wrap(new Bridge({ pythonExe, script, dataDir, cwd }));

  // 登录专用桥接：长轮询会独占进程最长约 2 分钟，不能和数据通道共用，
  // 否则扫码期间面板上的设备列表/开关全部排队超时。
  const loginBridge = new Bridge({ pythonExe, script, dataDir, cwd });

  ctx.effect(() => () => {
    bridge.dispose();
    loginBridge.dispose();
  });

  ctx.systemPrompt.section({
    name: "tool:mihome",
    order: 165,
    interpolate: false,
    text: "你有一组智能家居工具，用于发现设备线索、查看适配器已确认的能力、执行受支持的操作和管理手动场景。设备类别或协议识别不代表可控制；没有适配器确认的设备只能识别或读取，不得猜协议或写入。\n\n" +
      "两条路径：\n" +
      "- 全屋智能（smart_home_*）：smart_home_scan 通过 ARP/ping/端口/SSDP/mDNS/涂鸦/miIO/BLE 等线索扫描本网段，" +
      "识别常见设备类别和协议线索；仅对适配器确认的能力提供控制；" +
      "smart_home_devices 列出已发现/配置的设备与米家云端设备；smart_home_device 看单台的已确认能力与当前值；" +
      "smart_home_control 统一控制（power/write/action/scrape）；smart_home_scrape 读取路由器/光猫/NAS 的公开管理页，不等于已登录或能修改设置；" +
      "smart_home_scenes 管理自定义场景并执行；smart_home_catalog 查能力项的中英对照。\n" +
      "- 米家云端（mihome_*）：mihome_devices / mihome_device / mihome_control / mihome_scenes / mihome_sensors，" +
      "面向米家账号下的设备。\n\n" +
      "典型流程：\n" +
      "1. smart_home_scan 扫描全屋，或直接 smart_home_devices 拿设备清单。设备 id 是后续所有操作的键。\n" +
      "2. smart_home_device 看某台设备能控制什么——能力项已翻译成中文，别猜属性名。\n" +
      "3. smart_home_control 开关、写值、执行动作；要看管理页设置时用 scrape 模式。\n" +
      "4. smart_home_scenes 列出 / 保存 / 执行场景（如「回家时开灯、关窗帘」）。\n\n" +
      "注意：\n" +
      "- 未登录米家时，云端操作返回 need_login，此时用 mihome_login（action=start）取二维码给用户扫。\n" +
      "- 局域网扫描到的第三方设备（涂鸦 / Shelly / TP-Link 等）不需要米家登录，但可能需要本地密钥，" +
      "控制失败时如实说明，不要假装成功。\n" +
      "- 修改设备状态是对用户家里真实设备的写操作：用户没说清楚哪台设备、改成什么值时先问，不要自行猜测后执行。\n" +
      "- 设备名可能有重名，跨房间时用设备 id 或「房间+名称」指明。\n" +
      "- 读取类操作（扫描、设备列表、读数、状态）可以直接执行，不需要额外确认。",
  });

  const register = (tool) => {
    try {
      ctx.tools.register(tool);
      return true;
    } catch {
      return false;
    }
  };

  register(defineTool({
    name: "mihome_login",
    description:
      "管理米家账号登录状态。action=start 生成扫码登录二维码（返回字符二维码与 PNG 路径，请把二维码原样给用户扫）；" +
      "action=poll 等待扫码结果（会阻塞直到用户确认或超时，需要用上次 start 返回的 login_data）；" +
      "action=status 查询是否已登录。登录成功后凭据存在本地，后续 mihome_* 工具直接可用。",
    parameters: {
      action: STATUS_ACTION,
      login_data: {
        type: "object",
        additionalProperties: true,
        description: "action=poll 时必填：上一次 mihome_login（action=start）返回的 login_data 原样回传",
      },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: renderLogin(value) }],
    },
    timeoutMs: loginTimeoutMs + 15_000,
    async execute(args, exec) {
      const action = String(args.action ?? "");
      if (action === "status") {
        const result = await loginBridge.request("status", {}, requestTimeoutMs, exec?.signal);
        return {
          status: "status",
          logged_in: Boolean(result.logged_in),
          api_version: result.api_version ?? "",
          data_dir: result.data_dir ?? "",
        };
      }
      if (action === "start") {
        const result = await loginBridge.request("login_begin", {}, loginTimeoutMs, exec?.signal);
        if (result.need_scan === false) {
          return { status: "already_logged_in", logged_in: Boolean(result.logged_in) };
        }
        return {
          status: "waiting_scan",
          login_url: result.login_url ?? "",
          qr_ascii: result.qr_ascii ?? "",
          qr_png: result.qr_png ?? "",
          login_data: result.login_data ?? null,
        };
      }
      if (action === "poll") {
        if (!args.login_data) throw new Error("action=poll 需要 login_data（先调用 action=start）");
        const result = await loginBridge.request("login_wait", { login_data: args.login_data }, loginTimeoutMs, exec?.signal);
        return { status: result.logged_in ? "logged_in" : "pending", logged_in: Boolean(result.logged_in) };
      }
      throw new Error(`未知 action：${action}（可选 start / poll / status）`);
    },
    presentCall: (args) => ({
      card: "generic",
      title: `米家登录 ${String(args.action ?? "")}`,
      kind: "execute",
      rawInput: String(args.action ?? ""),
    }),
  }));

  register(defineTool({
    name: "mihome_devices",
    description:
      "列出米家账号下的全部设备（自有 + 共享），返回 did、名称、型号、所在家庭与房间、在线状态。" +
      "did 是后续 mihome_device / mihome_control / mihome_sensors 的键。",
    parameters: {},
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: formatDeviceList(value) }],
    },
    timeoutMs: requestTimeoutMs + 5_000,
    async execute(args, exec) {
      try {
        const result = await bridge.request("devices", {}, requestTimeoutMs, exec?.signal);
        const devices = Array.isArray(result.devices) ? result.devices : [];
        return { count: devices.length, devices };
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: () => ({ card: "generic", title: "米家设备列表", kind: "fetch", rawInput: "" }),
  }));

  register(defineTool({
    name: "mihome_device",
    description:
      "看一台设备的详细规格：可读属性（名称/描述/类型/取值范围/可选值）、可执行动作、当前电源状态。" +
      "在写属性或执行动作之前先看这个——属性名按型号不同，猜错会直接失败。",
    parameters: {
      did: { type: "string", required: true, description: "设备 did（从 mihome_devices 获取）" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: formatDeviceDetail(value) }],
    },
    timeoutMs: requestTimeoutMs + 5_000,
    async execute(args, exec) {
      try {
        const detail = await bridge.request("device_detail", { did: String(args.did) }, requestTimeoutMs, exec?.signal);
        if (!detail || !detail.did) throw Object.assign(new Error("设备不存在或未返回规格"), { code: "not_found" });
        return detail;
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: (args) => ({ card: "generic", title: String(args.did), kind: "fetch", rawInput: String(args.did) }),
  }));

  register(defineTool({
    name: "mihome_control",
    description:
      "控制一台米家设备。mode=power 开关电源；mode=write 写一个属性；mode=read 读属性；mode=action 执行设备动作（如开盖、开始清扫）。" +
      "写属性和执行动作属于对真实设备的写操作，属性名与取值请先用 mihome_device 确认。",
    parameters: {
      did: { type: "string", required: true, description: "设备 did" },
      mode: { type: "string", required: true, description: "read | write | action | power" },
      name: { type: "string", description: "属性名（write/read）或动作名（action），从 mihome_device 获取" },
      value: { type: "json", description: "write 时要写入的值（数字/字符串/布尔/数组等，按属性类型填）" },
      names: { type: "array", items: { type: "string" }, description: "read 时可一次读多个属性名" },
      params: {
        type: "object",
        additionalProperties: true,
        description: "action 的附加参数（可选，多数动作不需要）",
      },
      state: { type: "boolean", description: "power 模式：true=开、false=关；省略则按当前状态取反" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: renderControl(value) }],
    },
    timeoutMs: requestTimeoutMs + 5_000,
    async execute(args, exec) {
      const did = String(args.did);
      const mode = String(args.mode ?? "");
      try {
        if (mode === "power") {
          const payload = { did };
          if (typeof args.state === "boolean") payload.state = args.state;
          const result = await bridge.request("power", payload, requestTimeoutMs, exec?.signal);
          return { mode, did, on: Boolean(result.on) };
        }
        if (mode === "read") {
          const names = Array.isArray(args.names) ? args.names.map(String) : [];
          if (!names.length && args.name) names.push(String(args.name));
          if (!names.length) throw new Error("mode=read 需要 name 或 names");
          const result = await bridge.request("read_props", { did, names }, requestTimeoutMs, exec?.signal);
          return { mode, did, values: result.values ?? {} };
        }
        if (mode === "write") {
          if (!args.name) throw new Error("mode=write 需要 name");
          if (args.value === undefined) throw new Error("mode=write 需要 value");
          await bridge.request("write_prop", { did, name: String(args.name), value: args.value }, requestTimeoutMs, exec?.signal);
          return { mode, did, name: String(args.name), value: args.value, ok: true };
        }
        if (mode === "action") {
          if (!args.name) throw new Error("mode=action 需要 name");
          const payload = { did, name: String(args.name) };
          if (args.params && typeof args.params === "object") payload.params = args.params;
          await bridge.request("action", payload, requestTimeoutMs, exec?.signal);
          return { mode, did, name: String(args.name), ok: true };
        }
        throw new Error(`未知 mode：${mode}（可选 read / write / action / power）`);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: (args) => ({
      card: "generic",
      title: `${String(args.did)} ${String(args.mode ?? "")}`,
      kind: "execute",
      rawInput: JSON.stringify(args),
    }),
  }));

  // ---- 全屋智能（不限米家，覆盖局域网设备）----

  register(defineTool({
    name: "smart_home_scan",
    description:
      "通过 ARP/ping/端口、SSDP/mDNS、Tuya/miIO UDP 与 BLE 等线索扫描本网段。" +
      "输出设备身份、类别和协议线索；只有适配器确认的能力才提供控制，未支持的设备仅识别或读取，不猜写入命令。" +
      "需要全屋清单时先调用这个，再对单台用 smart_home_device。",
    parameters: {
      subnets: { type: "string", description: "要扫描的子网，可逗号分隔，如 192.168.0.0/24。缺省用本机所在网段" },
      prefix: { type: "number", description: "自动选择本机子网时的 IPv4 前缀长度（0–32），默认 24" },
      extra_subnets: { type: "string", description: "额外补充的子网，可逗号分隔" },
      ports: { type: "string", description: "额外探测的端口，逗号分隔，如 80,443,55443" },
      scan_ping: { type: "boolean", description: "是否用 ping 兜底扫描，默认 true" },
      force: { type: "boolean", description: "是否忽略缓存强制重新扫描，默认 false" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: formatSmartHomeList(value) }],
    },
    timeoutMs: 200_000,
    async execute(args, exec) {
      try {
        return await bridge.request(
          "smart_home_scan",
          {
            subnets: args.subnets,
            prefix: args.prefix,
            extra_subnets: args.extra_subnets,
            ports: args.ports,
            scan_ping: args.scan_ping,
            force: args.force,
          },
          Math.max(requestTimeoutMs, 195_000),
          exec?.signal,
        );
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: () => ({ card: "generic", title: "全屋扫描", kind: "execute", rawInput: "" }),
  }));

  register(defineTool({
    name: "smart_home_devices",
    description:
      "列出全屋已发现/已连接的智能家居设备：局域网扫描结果 + 米家云端设备。" +
      "返回的 id 是 smart_home_device / smart_home_control 的键。",
    parameters: {
      refresh: { type: "boolean", description: "是否重新扫描刷新，默认 false（用最近缓存）" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: formatSmartHomeList(value) }],
    },
    timeoutMs: 200_000,
    async execute(args, exec) {
      try {
        return await bridge.request("smart_home_devices", { refresh: args.refresh }, Math.max(requestTimeoutMs, 195_000), exec?.signal);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: () => ({ card: "generic", title: "全屋设备列表", kind: "fetch", rawInput: "" }),
  }));

  register(defineTool({
    name: "smart_home_device",
    description:
      "查看某台全屋智能设备的详情：按统一目录翻译后的能力项（电源/亮度/色温…）与当前值。设备 id 来自 smart_home_devices。",
    parameters: {
      id: { type: "string", required: true, description: "设备 id，形如 mihome:123 或 shelly:192.168.0.5" },
      refresh: { type: "boolean", description: "是否重新读取状态，默认 false" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: formatSmartHomeDetail(value) }],
    },
    timeoutMs: 200_000,
    async execute(args, exec) {
      try {
        return await bridge.request("smart_home_device", { id: String(args.id), refresh: args.refresh }, Math.max(requestTimeoutMs, 195_000), exec?.signal);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: (args) => ({ card: "generic", title: String(args.id), kind: "fetch", rawInput: String(args.id) }),
  }));

  register(defineTool({
    name: "smart_home_control",
    description:
      "控制一台全屋智能设备。mode=power 开关（state 缺省取反）；mode=write 写属性（name/value）；" +
      "mode=action 执行动作（name，可带 params）；mode=scrape 扒设置页（路由器/光猫/NAS）。" +
      "对真实设备写操作前先确认用户意图。",
    parameters: {
      device_id: { type: "string", required: true, description: "目标设备 id" },
      mode: { type: "string", description: "控制方式，默认 power。power | write | action | scrape" },
      state: { type: "boolean", description: "mode=power 的目标状态，缺省取反" },
      name: { type: "string", description: "mode=write/action 的属性名或动作名" },
      value: { type: "json", description: "mode=write 要写入的值" },
      params: { type: "json", description: "mode=action 的参数；按设备详情的 params_schema/inputs 填写对象或数组，保持原始值类型" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: JSON.stringify(value ?? {}) }],
    },
    timeoutMs: 200_000,
    async execute(args, exec) {
      try {
        return await bridge.request(
          "smart_home_control",
          {
            device_id: String(args.device_id),
            mode: args.mode ?? "power",
            state: args.state,
            name: args.name,
            value: args.value,
            params: args.params,
          },
          Math.max(requestTimeoutMs, 195_000),
          exec?.signal,
        );
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: (args) => ({
      card: "generic",
      title: `${String(args.device_id)} ${String(args.mode ?? "power")}`,
      kind: "execute",
      rawInput: JSON.stringify(args),
    }),
  }));

  register(defineTool({
    name: "smart_home_scrape",
    description:
      "读取设备（路由器 / 光猫 / NAS 等）公开管理页的标题、链接、表单字段和可见文本。" +
      "不会通用登录、提权或写入设置；结果可能仅为登录页，不代表已获取设备配置。",
    parameters: {
      device_id: { type: "string", required: true, description: "目标设备 id" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: JSON.stringify(value ?? {}) }],
    },
    timeoutMs: 200_000,
    async execute(args, exec) {
      try {
        return await bridge.request("smart_home_scrape", { device_id: String(args.device_id) }, Math.max(requestTimeoutMs, 195_000), exec?.signal);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: (args) => ({ card: "generic", title: `扒设置页 ${String(args.device_id)}`, kind: "execute", rawInput: String(args.device_id) }),
  }));

  register(defineTool({
    name: "smart_home_scenes",
    description:
      "管理自定义场景：mode=list 列出已保存场景（默认）；mode=save 保存场景（scene.name + scene.actions，" +
      "如「回家时开灯、关窗帘」）；mode=run 执行；mode=delete 删除。",
    parameters: {
      mode: { type: "string", description: "操作类型，默认 list。list | save | run | delete" },
      scene: {
        type: "object",
        additionalProperties: true,
        description: "mode=save 时的完整场景：{ name, actions: [{ device, mode, name, value }] }",
      },
      scene_id: { type: "string", description: "mode=run/delete 时的场景 id" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: formatSmartHomeScenes(value) }],
    },
    timeoutMs: 200_000,
    async execute(args, exec) {
      try {
        return await bridge.request(
          "smart_home_scenes",
          { mode: args.mode ?? "list", scene: args.scene, scene_id: args.scene_id },
          Math.max(requestTimeoutMs, 195_000),
          exec?.signal,
        );
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: (args) => ({
      card: "generic",
      title: `全屋场景 ${String(args.mode ?? "list")}`,
      kind: "execute",
      rawInput: JSON.stringify(args),
    }),
  }));

  register(defineTool({
    name: "smart_home_catalog",
    description:
      "返回能力项目录：设备类别（灯具/开关/插座/扫地机/冰箱/电视/洗衣机/烘干机…）" +
      "与统一控制项（电源/亮度/色温/模式/风速/目标温度…）的中英对照。实际控制 key 以 smart_home_device 返回为准。",
    parameters: {},
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: JSON.stringify(value ?? {}) }],
    },
    timeoutMs: 15_000,
    async execute(args, exec) {
      try {
        return await bridge.request("smart_home_catalog", {}, 15_000, exec?.signal);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: () => ({ card: "generic", title: "能力项目录", kind: "fetch", rawInput: "" }),
  }));

  register(defineTool({
    name: "mihome_scenes",
    description: "列出米家场景（如「回家模式」「睡眠模式」），或用 run 模式执行指定场景。执行场景会改动真实设备状态。",
    parameters: {
      mode: { type: "string", required: true, description: "list | run" },
      scene_id: { type: "string", description: "run 模式必填：场景 id（从 list 获取）" },
      home_id: { type: "string", description: "run 模式必填：场景所属家庭 id（从 list 获取）" },
      name: { type: "string", description: "run 模式可选：场景名，仅用于回显" },
      home_name: { type: "string", description: "list 模式可选：只看某个家庭的场景" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: value.mode === "run" ? renderSceneRun(value) : formatScenes(value) }],
    },
    timeoutMs: requestTimeoutMs + 5_000,
    async execute(args, exec) {
      const mode = String(args.mode ?? "");
      try {
        if (mode === "list") {
          const payload = {};
          if (args.home_name) payload.home_name = String(args.home_name);
          const result = await bridge.request("scenes", payload, requestTimeoutMs, exec?.signal);
          const scenes = Array.isArray(result.scenes) ? result.scenes : [];
          return { mode, count: scenes.length, scenes };
        }
        if (mode === "run") {
          if (!args.scene_id) throw new Error("mode=run 需要 scene_id");
          if (!args.home_id) throw new Error("mode=run 需要 home_id");
          const payload = { scene_id: String(args.scene_id), home_id: String(args.home_id) };
          if (args.name) payload.name = String(args.name);
          await bridge.request("run_scene", payload, requestTimeoutMs, exec?.signal);
          return { mode, ok: true, scene_id: payload.scene_id, name: payload.name ?? "" };
        }
        throw new Error(`未知 mode：${mode}（可选 list / run）`);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: (args) => ({
      card: "generic",
      title: `米家场景 ${String(args.mode ?? "")}`,
      kind: "execute",
      rawInput: String(args.mode ?? ""),
    }),
  }));

  register(defineTool({
    name: "mihome_sensors",
    description:
      "读传感器与耗材：mode=metrics 读指定设备的传感器数值（温度/湿度/PM2.5/电量等）；" +
      "mode=consumables 汇总所有耗材余量（滤芯、拖布等）并标出偏低的项。",
    parameters: {
      mode: { type: "string", required: true, description: "metrics | consumables" },
      dids: { type: "array", items: { type: "string" }, description: "metrics 模式必填：设备 did 数组" },
    },
    output: {
      schema: { type: "json" },
      render: (_args, value) => [{ type: "text", text: renderSensors(value) }],
    },
    timeoutMs: requestTimeoutMs + 5_000,
    async execute(args, exec) {
      const mode = String(args.mode ?? "");
      try {
        if (mode === "metrics") {
          const dids = Array.isArray(args.dids) ? args.dids.map(String) : [];
          if (!dids.length) throw new Error("mode=metrics 需要 dids");
          const result = await bridge.request("metrics", { dids }, requestTimeoutMs, exec?.signal);
          return { mode, dids, values: result.values ?? {}, text: result.text ?? "" };
        }
        if (mode === "consumables") {
          const result = await bridge.request("consumables", {}, requestTimeoutMs, exec?.signal);
          return {
            mode,
            items: Array.isArray(result.items) ? result.items : [],
            low_count: Number(result.low_count ?? 0),
          };
        }
        throw new Error(`未知 mode：${mode}（可选 metrics / consumables）`);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new Error(describeError(error));
      }
    },
    presentCall: (args) => ({
      card: "generic",
      title: `米家传感器 ${String(args.mode ?? "")}`,
      kind: "fetch",
      rawInput: String(args.mode ?? ""),
    }),
  }));

  // ---- 侧栏工作台（可选）-------------------------------------------------
  // webServer 只在 Web 组合里存在；没有它时面板静默不出现，工具层照常工作。
  // tips: 用 ctx.inject 而不是顶部 inject 数组，缺服务时不阻塞插件加载。
  try {
    ctx.inject(["webServer"], (webCtx) => {
      try {
        mountPanelRoutes(webCtx, {
          bridge,
          loginBridge,
          connections,
          qrDir: join(dataDir, "qr"),
          dataDir,
          requestTimeoutMs,
          loginTimeoutMs,
        });
      } catch (error) {
        console.warn(`[dsh-mihome] 工作台面板挂载失败：${error?.message ?? error}`);
      }
    });
  } catch (error) {
    console.warn(`[dsh-mihome] 无法注入 webServer：${error?.message ?? error}`);
  }
}

// ------------------------------------------------------------------ render

function renderLogin(value) {
  const v = value ?? {};
  if (v.status === "already_logged_in") return "米家账号已登录（本地凭据仍有效，无需扫码）。";
  if (v.status === "status") {
    return v.logged_in
      ? `米家账号已登录（mijiaAPI ${v.api_version}）。数据目录：${v.data_dir}`
      : "米家账号未登录。调用 mihome_login（action=start）获取二维码。";
  }
  if (v.status === "logged_in") return "扫码成功，米家账号已登录。现在可以调用 mihome_devices 了。";
  if (v.status === "pending") return "尚未确认登录（二维码可能已过期）。可重新 action=start 生成新的二维码。";
  if (v.status === "waiting_scan") {
    const lines = ["请用手机**米家 APP** 扫描下面的二维码登录：", ""];
    // 路径每次都带新的 ticket+时间戳，前端不会命中旧图缓存——之前固定文件名
    // 导致用户反复扫到上一张已过期的码。
    if (v.qr_png) lines.push(`![米家登录二维码](${v.qr_png})`, "", `二维码图片：${v.qr_png}`);
    if (v.expires_in) lines.push("", `有效期约 ${Math.round(v.expires_in / 60)} 分钟；过期就再调一次 action=start 换新的。`);
    lines.push("", "扫完并确认后，把下面的 login_data 原样传回 mihome_login（action=poll，login_data=…）等待结果：", "", "```json");
    lines.push(JSON.stringify(v.login_data ?? null));
    lines.push("```", "", "纯文本会话可直接扫这段字符二维码：", "```", String(v.qr_ascii ?? "").trimEnd(), "```");
    return lines.join("\n");
  }
  return JSON.stringify(v, null, 2);
}

function renderControl(value) {
  const v = value ?? {};
  if (v.mode === "power") return `电源已${v.on ? "打开" : "关闭"}（${v.did}）。`;
  if (v.mode === "read") {
    const entries = Object.entries(v.values ?? {});
    if (!entries.length) return "设备没有返回任何属性值。";
    return entries.map(([k, val]) => `${k} = ${JSON.stringify(val)}`).join("\n");
  }
  if (v.mode === "write") return `已写入 ${v.name} = ${JSON.stringify(v.value)}（${v.did}）。`;
  if (v.mode === "action") return `已执行动作 ${v.name}（${v.did}）。`;
  return JSON.stringify(v, null, 2);
}

function renderSceneRun(value) {
  const v = value ?? {};
  return `已执行场景${v.name ? `「${v.name}」` : ""}（${v.scene_id}）。`;
}

function renderSensors(value) {
  const v = value ?? {};
  if (v.mode === "metrics") {
    // values 是 did -> 已格式化文案（读不到的设备为 null），量纲换算在
    // Python 侧的 format_metric 里做过了，这里不再加工
    const entries = Object.entries(v.values ?? {});
    if (!entries.length) return "没有读到传感器数值。";
    return entries
      .map(([did, text]) => `${did}: ${text || "（该设备无温湿度等外显属性）"}`)
      .join("\n");
  }
  if (v.mode === "consumables") {
    const items = Array.isArray(v.items) ? v.items : [];
    if (!items.length) return "没有耗材信息。";
    const lines = items.map((item) => {
      const label = [item?.name, item?.description].filter(Boolean).join(" · ") || item?.did || "未知";
      const threshold = item?.threshold != null ? `（低于 ${item.threshold} 需更换）` : "";
      return `- ${label}：${item?.value ?? "?"}${item?.low ? " ← 偏低" : ""}${threshold}`;
    });
    return lines.join("\n") + (v.low_count ? `\n\n共 ${v.total ?? items.length} 项耗材，其中 ${v.low_count} 项余量偏低。` : "");
  }
  return JSON.stringify(v, null, 2);
}

export { Config, apply, inject, name };
