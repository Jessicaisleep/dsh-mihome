/**
 * 把桥接返回的米家数据渲染成模型可读的文本。
 *
 * 渲染放在这里而不是工具里，是为了让「取数」和「怎么说」分开：
 * 工具只负责调桥接、判错，格式统一在这里维护，改措辞不用碰协议。
 *
 * @module dsh-mihome/lib/format
 */

/** 设备列表：按家庭 + 房间分组，同组内保持桥接给的顺序（已按 home/room/name 排过）。 */
export function formatDeviceList(value) {
  const v = value ?? {};
  const devices = Array.isArray(v.devices) ? v.devices : [];
  if (!devices.length) {
    return "米家账号下没有设备（或都为共享设备且无权限）。请确认账号是否已登录：mihome_login（action=status）。";
  }

  const groups = new Map();
  for (const device of devices) {
    const home = device?.home_name || "（未分组）";
    const room = device?.room_name || "（无房间）";
    const key = `${home} / ${room}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(device);
  }

  const lines = [`共 ${devices.length} 台设备：`, ""];
  for (const [key, items] of groups) {
    lines.push(`【${key}】`);
    for (const device of items) {
      const status = device?.online === false ? "离线" : "在线";
      const model = device?.model ? ` · ${device.model}` : "";
      lines.push(`- ${device?.name ?? "未命名"}（${status}${model}）did=${device?.did ?? ""}`);
    }
    lines.push("");
  }
  return lines.join("\n").trimEnd();
}

/**
 * 把米家的枚举可选值渲染成 "值=说明/值=说明"。
 *
 * 上游给的是 [{value, description}, ...]（见 mijiaAPI/devices.py 的
 * prop.value_list），不展开就会显示成 [object Object]——空调模式、风速
 * 这些关键属性全靠它，必须可读。
 */
function formatEnumList(list) {
  if (!Array.isArray(list)) return String(list);
  return list
    .map((item) => {
      if (item && typeof item === "object") {
        const v = item.value ?? item.val;
        const d = item.description ?? item.desc ?? item.name;
        return d === undefined || d === "" ? String(v) : `${v}=${d}`;
      }
      return String(item);
    })
    .join("/");
}

/** 设备详情：属性表 + 动作表。属性区分读写，取值范围/可选值一并列出。 */
export function formatDeviceDetail(value) {  const d = value ?? {};
  const lines = [`${d.name ?? "未命名"}（${d.model ?? "未知型号"}）`, `did=${d.did ?? ""}`];
  if (d.home_name || d.room_name) lines.push(`位置：${d.home_name ?? ""} ${d.room_name ?? ""}`.trim());
  lines.push("");

  const props = Array.isArray(d.props) ? d.props : [];
  if (props.length) {
    lines.push(`属性（${props.length}）：`);
    for (const prop of props) {
      const rw = `${prop?.readable ? "读" : ""}${prop?.writable ? "写" : ""}` || "—";
      const bits = [`- ${prop?.name ?? "?"}`, `[${rw}]`];
      if (prop?.desc) bits.push(String(prop.desc));
      if (prop?.type) bits.push(`类型=${prop.type}`);
      if (Array.isArray(prop?.value_list) && prop.value_list.length) {
        // 上游 value-list 的每一项是 {value, description} 对象，直接 join
        // 会渲染成 [object Object]；这里展开成 值=说明。
        bits.push(`可选值=${formatEnumList(prop.value_list)}`);
      } else if (Array.isArray(prop?.range)) {
        const [min, max, step] = prop.range;
        bits.push(`范围=${min}~${max}${step && step !== 1 ? ` 步进${step}` : ""}`);
      } else if (prop?.range && typeof prop.range === "object") {
        const { min, max, step } = prop.range;
        if (min !== undefined || max !== undefined) {
          bits.push(`范围=${min}~${max}${step && step !== 1 ? ` 步进${step}` : ""}`);
        }
      }
      lines.push(bits.join(" "));
    }
    lines.push("");
  } else {
    lines.push("这台设备没有返回可读属性。", "");
  }

  const actions = Array.isArray(d.actions) ? d.actions : [];
  if (actions.length) {
    lines.push(`可执行动作（${actions.length}）：`);
    for (const action of actions) {
      lines.push(`- ${action?.name ?? "?"}${action?.desc ? ` · ${action.desc}` : ""}`);
    }
  } else {
    lines.push("没有可执行动作。");
  }
  return lines.join("\n");
}

/** 场景列表：按家庭分组，给出 run 模式需要的 scene_id + home_id。 */
export function formatScenes(value) {
  const v = value ?? {};
  const scenes = Array.isArray(v.scenes) ? v.scenes : [];
  if (!scenes.length) return "没有可用的米家场景。";

  const groups = new Map();
  for (const scene of scenes) {
    const home = scene?.home_name || "（未分组）";
    if (!groups.has(home)) groups.set(home, []);
    groups.get(home).push(scene);
  }

  const lines = [`共 ${scenes.length} 个场景：`, ""];
  for (const [home, items] of groups) {
    lines.push(`【${home}】`);
    for (const scene of items) {
      lines.push(`- ${scene?.name ?? "未命名"}  scene_id=${scene?.scene_id ?? ""} home_id=${scene?.home_id ?? ""}`);
    }
    lines.push("");
  }
  lines.push("执行：mihome_scenes（mode=run，scene_id=…，home_id=…）");
  return lines.join("\n");
}

/**
 * 全屋设备列表：按类别分组，标出来源（局域网/云端）与地址。
 *
 * 来源与地址按扫描结果展示；只有适配器明确确认的能力才可用于控制。
 */
export function formatSmartHomeList(value) {
  const v = value ?? {};
  const devices = Array.isArray(v.devices) ? v.devices : [];
  if (!devices.length) {
    return "没有发现任何智能家居设备。可以先调用 smart_home_scan 重新扫描，" +
      "或检查子网参数（subnets / prefix）。";
  }

  const groups = new Map();
  for (const device of devices) {
    const category = device?.category_zh || device?.category || "其他设备";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(device);
  }

  const lines = [`共发现 ${devices.length} 台设备：`, ""];
  for (const [category, items] of groups) {
    lines.push(`【${category}】`);
    for (const device of items) {
      const source = device?.source === "cloud" ? "云端" :
        device?.source === "configured" ? "已配置" :
          device?.source === "ble" ? "蓝牙" : device?.source === "home_assistant" ? "Home Assistant" :
            device?.source === "mihome" ? "米家" : "局域网";
      const addr = device?.ip || device?.address ? ` ${device.ip ?? device.address}` : "";
      const model = device?.model ? ` · ${device.model}` : "";
      lines.push(`- ${device?.name ?? "未命名"}（${source}${addr}）id=${device?.id ?? ""}${model}`);
    }
    lines.push("");
  }
  lines.push("设备 id 是后续 smart_home_device / smart_home_control 的键。");
  return lines.join("\n").trimEnd();
}

/**
 * 全屋设备详情：能力项（已翻译）+ 当前值。
 *
 * 能力项来自统一目录，属性名已带中文；值是「能力项 -> 值」的字典，
 * 读不到的键不显示，避免把空值当成有效状态。
 */
export function formatSmartHomeDetail(value) {
  const d = value ?? {};
  const lines = [`${d.name ?? "未命名"}（${d.category_zh ?? d.category ?? "未知类别"}）`, `id=${d.id ?? ""}`];
  if (d.protocol) lines.push(`协议=${d.protocol}${d.ip ? ` · 地址=${d.ip}` : ""}`);
  if (d.source) {
    const source = d.source === "cloud" ? "米家云端" : d.source === "configured" ? "已配置" :
      d.source === "ble" ? "蓝牙" : d.source === "home_assistant" ? "Home Assistant" :
        d.source === "mihome" ? "米家" : d.source;
    lines.push(`来源=${source}`);
  }
  lines.push("");

  const controls = Array.isArray(d.controls) ? d.controls : [];
  if (controls.length) {
    lines.push(`可控制（${controls.length}）：`);
    for (const control of controls) {
      const bits = [`- ${(control?.zh || control?.name || control?.key) ?? "?"}`,
        `key=${control?.key ?? ""}`, `name=${control?.name ?? ""}`, `kind=${control?.kind ?? "unknown"}`,
        `readable=${control?.readable ?? "unknown"}`, `writable=${control?.writable ?? "unknown"}`];
      const range = control?.range ?? (control?.min !== undefined || control?.max !== undefined
        ? { min: control.min, max: control.max, step: control.step } : undefined);
      if (range !== undefined) bits.push(`range=${JSON.stringify(range)}`);
      const enums = control?.value_list?.length ? control.value_list : control?.values;
      if (Array.isArray(enums) && enums.length) {
        bits.push(`enums=${enums.map((item) => {
          const option = item && typeof item === "object" ? item : { value: item };
          const label = option.zh ?? option.description ?? option.desc ?? option.name;
          return `${JSON.stringify(option.value)}${label === undefined ? "" : `=${label}`}`;
        }).join(" / ")}`);
      }
      const unit = control?.unit ?? control?.units;
      if (unit !== undefined) bits.push(`units=${JSON.stringify(unit)}`);
      if (control?.kind === "action") {
        if (control.params_schema !== undefined) bits.push(`params_schema=${JSON.stringify(control.params_schema)}`);
        const inputs = control.inputs ?? control.in ?? control.params;
        if (inputs !== undefined) bits.push(`inputs=${JSON.stringify(inputs)}`);
      }
      lines.push(bits.join(" "));
    }
    lines.push("");
  } else if (!d.capability_error && !d.read_error) {
    lines.push("这台设备没有返回可控制项。", "");
  }
  if (d.capability_error) lines.push(`能力读取失败：${d.capability_error}`, "");
  if (d.read_error) lines.push(`状态读取失败：${d.read_error}`, "");

  const values = d.values && typeof d.values === "object" ? d.values : {};
  const entries = Object.entries(values);
  if (entries.length) {
    lines.push(`当前值（${entries.length}）：`);
    for (const [key, val] of entries) {
      lines.push(`- ${key} = ${JSON.stringify(val)}`);
    }
  } else if (!d.read_error) {
    lines.push("当前没有读到任何数值（设备可能离线或不支持状态读取）。");
  }
  return lines.join("\n");
}

/** 自定义场景：按请求模式显示保存、删除、执行结果或列表。 */
export function formatSmartHomeScenes(value, mode = "list") {
  const v = value ?? {};
  mode = v.mode ?? mode;
  if (mode === "save") {
    const saved = v.saved ?? v.scene;
    if (v.ok === false || !saved || typeof saved !== "object") return `场景保存失败：${v.error ?? "未返回已保存场景"}`;
    return `已保存场景：${saved.name ?? "未命名"} id=${saved.id ?? ""}`;
  }
  if (mode === "delete") {
    return v.ok !== false && v.deleted === true ? "场景已删除。" : `场景删除失败：${v.error ?? "场景不存在或未删除"}`;
  }
  if (mode === "run") {
    const steps = Array.isArray(v.steps) ? v.steps : Array.isArray(v.results) ? v.results : [];
    const success = Number.isInteger(v.success) ? v.success : steps.filter((step) => step?.ok === true).length;
    const total = Number.isInteger(v.total) ? v.total : steps.length;
    const failed = Number.isInteger(v.failed) ? v.failed : Math.max(0, total - success);
    const lines = [`场景${v.scene ? `「${typeof v.scene === "object" ? v.scene.name : v.scene}」` : ""}执行${v.ok === true ? "成功" : "失败"}：成功 ${success}，失败 ${failed}，共 ${total} 步。`];
    if (v.error) lines.push(`错误：${v.error}`);
    for (const [index, step] of steps.entries()) {
      const error = step?.error ?? step?.result?.error;
      lines.push(`${index + 1}. ${step?.device ?? step?.device_id ?? "?"} ${step?.mode ?? ""}：${step?.ok === true ? "成功" : "失败"}${error ? ` · ${error}` : ""}`);
    }
    return lines.join("\n");
  }
  if (v.ok === false) return `读取场景失败：${v.error ?? "未知错误"}`;
  const scenes = Array.isArray(v.scenes) ? v.scenes : [];
  if (!scenes.length) return "还没有保存任何自定义场景。用 smart_home_scenes（mode=save）创建，如「回家时开灯、关窗帘」。";
  const lines = [`共 ${scenes.length} 个自定义场景：`, ""];
  for (const scene of scenes) {
    const actions = Array.isArray(scene?.actions) ? scene.actions : [];
    lines.push(`- ${scene?.name ?? "未命名"}（${actions.length} 个动作）id=${scene?.id ?? ""}`);
    for (const action of actions) {
      const target = action?.device ?? "?";
      const label = action?.name || action?.mode || "";
      const args = ["value", "params", "state"].filter((key) => Object.hasOwn(action ?? {}, key))
        .map((key) => `${key}=${JSON.stringify(action[key])}`).join(" ");
      lines.push(`    · ${target} ${action?.mode ?? ""}${label ? ` ${label}` : ""}${args ? ` ${args}` : ""}`);
    }
  }
  lines.push("");
  lines.push("执行：smart_home_scenes（mode=run，scene_id=…）；删除：mode=delete。");
  return lines.join("\n").trimEnd();
}
