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
