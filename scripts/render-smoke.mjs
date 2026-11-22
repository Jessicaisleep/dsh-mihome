/**
 * 模拟渲染契约冒烟：fake React/hooks + fake HTTP，检查六条页面路径。
 * 输出仅供离线诊断，不证明真实 React、宿主挂载、布局或辅助技术行为。
 * 用法：node scripts/render-smoke.mjs client.js
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

/* 全屋智能：局域网发现的设备（不需要米家登录） */
const SMART_DEVICES = [
  {
    id: "shelly:192.168.0.21",
    name: "走廊灯",
    model: "SHELLY2",
    category: "light",
    category_zh: "灯具",
    protocol: "shelly",
    ip: "192.168.0.21",
    online: true,
    source: "lan",
  },
  {
    id: "router:192.168.0.1",
    name: "主路由",
    model: "TL-7DR7230",
    category: "router",
    category_zh: "路由器",
    protocol: "router",
    ip: "192.168.0.1",
    online: true,
    source: "lan",
  },
  {
    id: "mihome:917079910",
    name: "空调插座",
    model: "lumi.acpartner.mcn02",
    category: "plug",
    category_zh: "插座",
    protocol: "mihome",
    ip: "",
    online: true,
    source: "cloud",
  },
];

const SMART_DETAIL = {
  id: "shelly:192.168.0.21",
  name: "走廊灯",
  category_zh: "灯具",
  protocol: "shelly",
  ip: "192.168.0.21",
  controls: [
    { key: "power", name: "power", zh: "开关", en: "Power", kind: "bool", writable: true },
    {
      key: "brightness",
      name: "brightness",
      zh: "亮度",
      en: "Brightness",
      kind: "range",
      writable: true,
      min: 0,
      max: 100,
      step: 1,
      unit: "%",
    },
    {
      key: "mode",
      name: "mode",
      zh: "模式",
      en: "Mode",
      kind: "enum",
      writable: true,
      values: [
        { value: "auto", zh: "自动" },
        { value: "cool", zh: "制冷" },
        { value: "heat", zh: "制热" },
      ],
    },
  ],
  values: { power: true, brightness: 60, mode: "auto" },
};

const SMART_SCENES = [
  {
    id: "sc1",
    name: "回家模式",
    enabled: true,
    actions: [
      { device: "shelly:192.168.0.21", mode: "power", state: true },
      { device: "shelly:192.168.0.21", mode: "write", name: "brightness", value: 80 },
    ],
  },
];

const ROUTES = (url) => {
  const u = String(url);
  // 全屋智能路由必须排在米家前面："/smart-home/devices" 也包含 "/devices"，
  // 顺序反了会被米家的设备分支抢先命中。
  if (u.includes("/smart-home/scan")) return { ok: true, devices: SMART_DEVICES };
  if (u.includes("/smart-home/devices")) return { ok: true, devices: SMART_DEVICES };
  if (u.includes("/smart-home/scenes")) return { ok: true, scenes: SMART_SCENES };
  if (u.includes("/smart-home/device?")) return { ok: true, detail: SMART_DETAIL };
  if (u.includes("/smart-home/control")) return { ok: true, result: { ok: true } };
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
// 全屋现在是默认入口；先验证默认页，再显式导航到米家跑原有四条路径。
const defaultSmartMissing = ["全屋智能", "走廊灯", "主路由"].filter((x) => !html.includes(x));
const mihomeTab = rendered.find((n) => n.props?.role === "tab" && n.children?.[0] === ZH.shTabMihome);
if (!mihomeTab) throw new Error("米家外壳页签未渲染");
await mihomeTab.props.onClick();
html = await renderTree(slots.mains[0].component({ t }), "root");
const need = ["我的家", "卧室", "客厅", "空调插座", "小米智能门铃3", "离线", "已开启"];
const missing = need.filter((x) => !html.includes(x));
fs.writeFileSync("scripts/.out/out1.html", html);
console.log("pass1 len:", html.length, "缺失:", JSON.stringify(missing));

// 首页的页签节点要在点进设备前抓住：点进去之后 openDevice 有值，重渲染就是详情页了。
// 外壳（Shell）现在在最外层放了「米家 / 全屋」两个页签，它们也是 role="tab"，
// 这里按标签文案把它们排除掉，剩下的才是米家面板自己的「设备 / 场景 / 耗材」。
const shellLabels = new Set([ZH.shTabMihome ?? "米家", ZH.shTabSmart ?? "全屋"]);
const allTabs = rendered.filter((n) => n.props?.role === "tab" && typeof n.props.onClick === "function");
const tabLabel = (n) => String(n.children?.[0] ?? "");
const shellTabs = allTabs.filter((n) => shellLabels.has(tabLabel(n)));
const homeTabs = allTabs.filter((n) => !shellLabels.has(tabLabel(n)));
console.log("首页页签数:", homeTabs.length, "(外壳页签:", shellTabs.length, ")");

const tiles = rendered.filter((n) => n.props?.role === "button" && typeof n.props.onClick === "function");
console.log("可点击磁贴数:", tiles.length);

let detailHtml = "";
let missing2 = ["米家设备详情未打开"];
if (tiles.length) {
  // 点第一台（空调插座）
  await tiles[0].props.onClick();
  detailHtml = await renderTree(slots.mains[0].component({ t }), "root");
  fs.writeFileSync("scripts/.out/out2.html", detailHtml);
  const need2 = ["电源", "常用", "模式", "制冷", "目标温度", "风速", "当前状态", "电功率", "更多操作", "设置空调型号", "高级设置", "返回"];
  missing2 = need2.filter((x) => !detailHtml.includes(x));
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

/* ------------------------------------------- 外壳：切到「全屋」智能面板 */

let smartHtml = "";
let smartDetailHtml = "";
let smartMissing = ["外壳页签未渲染"];

const smartTab = shellTabs.find((n) => tabLabel(n) === (ZH.shTabSmart ?? "全屋"));
if (smartTab) {
  await smartTab.props.onClick();
  smartHtml = await renderTree(slots.mains[0].component({ t }), "root");
  fs.writeFileSync("scripts/.out/out5.html", smartHtml);

  // 全屋设备按类别分组：灯具 / 路由器 / 插座（来自云端米家设备）
  const need5 = ["走廊灯", "主路由", "空调插座", "灯具", "路由器", "插座", "shelly", "192.168.0.21"];
  smartMissing = need5.filter((x) => !smartHtml.includes(x));

  // 点开一台局域网设备看详情（能力项带中文名 + 当前值）
  // 磁贴是 h('button', {...}, h('div', {...}, name), ...)，名字在嵌套子节点里，
  // 这里递归取文本再匹配，别只看第一层。
  const textOf = (node) => {
    if (node === null || node === undefined || typeof node === "boolean") return "";
    if (typeof node === "string" || typeof node === "number") return String(node);
    if (Array.isArray(node)) return node.map(textOf).join("");
    const kids = node.children ?? [];
    return kids.map(textOf).join("");
  };
  const smartTiles = rendered.filter(
    (n) =>
      n.tag === "button" &&
      typeof n.props?.onClick === "function" &&
      textOf(n).includes("走廊灯"),
  );
  const tile = smartTiles[0];
  if (tile) {
    await tile.props.onClick();
    smartDetailHtml = await renderTree(slots.mains[0].component({ t }), "root");
    fs.writeFileSync("scripts/.out/out6.html", smartDetailHtml);
    const need6 = ["走廊灯", "开关", "亮度", "模式", "自动", "制冷", "id=shelly:192.168.0.21", "key=power", "writable=true"];
    smartMissing = smartMissing.concat(need6.filter((x) => !smartDetailHtml.includes(x)));
  } else {
    smartMissing.push("全屋设备磁贴不可点击");
  }
}
console.log(
  "pass5/6 全屋页 len:",
  smartHtml.length,
  "/",
  smartDetailHtml.length,
  "缺失:",
  JSON.stringify(smartMissing),
);

const bad3 = /undefined|NaN|\[object Object\]/.test(smartHtml + smartDetailHtml);
console.log("全屋页含 undefined/NaN/[object Object]:", bad3);

const missing34 = globalThis.__missing34 ?? ["页签未渲染"];
const ok =
  !bad && !bad2 && !bad3 && !defaultSmartMissing.length && !missing.length &&
  !missing2.length && !missing34.length && !smartMissing.length && detailHtml.length > 0;
console.log(ok ? "SMOKE OK" : "SMOKE FAIL");
process.exit(ok ? 0 : 1);
