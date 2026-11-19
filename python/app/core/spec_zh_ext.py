# SPDX-License-Identifier: GPL-3.0-or-later
"""MIoT 属性/动作/可选值的纯中文扩展表（不改上游 spec_zh.py）。

上游 `spec_zh.py` 做两件事：给缺中文的描述拼成「英文 / 中文」混排，
且完全不处理 `value-list`。米家 APP 里看到的是**纯中文**，所以这里
再补一层：

1. `PROPERTY_ZH` / `ACTION_ZH` 的增量表（空调、门铃、路由器等型号
   专有项，上游表没覆盖）。
2. `VALUE_ZH`：可选值的英文 → 中文（Auto→自动、Cool→制冷 …… ）。
3. `to_zh()`：把「英文 / 中文」这类混排压成纯中文；查不到才原样返回，
   避免把没翻译过的技术字段显示成中文假象。

只在桥接输出给界面前调用，不写入上游缓存，保持上游文件不动。
"""

from __future__ import annotations

import re

# --------------------------------------------------------------- 属性中文

# 键是 MIoT 属性短名（siid 层的 name 字段），与设备型号无关地共用。
PROPERTY_ZH: dict[str, str] = {
    # —— 空调 / 空调伴侣 ——
    "on": "开关",
    "mode": "模式",
    "target-temperature": "设定温度",
    "fan-level": "风速",
    "vertical-swing": "上下摆风",
    "horizontal-swing": "左右摆风",
    "match-state": "匹配状态",
    "ac-work-mode": "空调工作模式",
    "ac-state": "空调状态",
    "brand-id": "品牌信息",
    "remote-id": "遥控器 ID",
    "ac-type": "空调类型",
    "ac-ctrl-library": "码库控制信息",
    "ac-ctrl-library-crc32": "码库校验码",
    "electric-power": "电功率",
    "power-consumption": "耗电量",
    "temperature": "温度",
    "relative-humidity": "湿度",
    # —— 门铃 / 摄像头 ——
    "status": "工作状态",
    "night-shot": "夜视功能",
    "eco": "ECO 节能模式",
    "ringtone": "铃声",
    "ringer-music": "门铃音乐",
    "cloud-video-id": "云存视频编号",
    "alarm-interval": "报警时间间隔",
    "detection-sensitivity": "侦测灵敏度",
    "motion-detection": "移动侦测开关",
    "motion-detection-4": "移动侦测开关",
    "motion-detection-10": "移动侦测",
    "alarm": "提示音",
    "battery-level": "电池电量",
    "volume": "音量",
    "motionpush-pushtype": "推送类型",
    "motionpush": "移动侦测推送开关",
    "vistpush": "来访推送开关",
    "videodelay": "延迟录像时间",
    "videolength": "录像最大时长",
    "autoreply": "自动回复开关",
    "autoreply-week": "自动回复生效星期",
    "autoreply-time": "自动回复生效时间",
    "autoreply-item": "自动回复内容",
    "ringer-time": "勿扰模式时间段",
    "fw-autoupgrade": "固件自动升级开关",
    "eco-code": "ECO 节能状态",
    "motion-time": "移动侦测时间段",
    "test-spec": "开始测试",
    "settingsync-time": "设置同步时间",
    "battypedate": "电池装入日期",
    "workinglength": "累计工作时长",
    "startuptimes": "累计开机次数",
    "threeday-work": "近三天工作时长",
    "syswork": "系统运行统计",
    "pir": "触发次数统计",
    "videonum": "录像上传统计",
    "fourbat-work": "电量分档统计",
    "connected": "联网失败次数",
    "pwrdate": "开关机统计",
    "mcu-pir": "主控触发统计",
    # —— 路由器 ——
    "download-speed": "下载速度",
    "upload-speed": "上传速度",
    "connected-device-number": "连接设备数",
    "network-connection-type": "上网方式",
    "wifi-ssid": "WiFi 名称",
    "rssi": "信号强度",
    "online-time": "在线时长",
    # —— 通用 ——
    "fault": "故障",
    "toggle": "开关",
    "delay-time": "延时时间",
}

# --------------------------------------------------------------- 动作中文

ACTION_ZH: dict[str, str] = {
    "set-ac-model": "匹配空调品牌",
    "set-ac-ctrl-info": "同步空调码库",
    "turn-on": "开机",
    "turn-off": "关机",
    "toggle": "切换",
    "reboot": "重启",
    "set-default": "恢复默认",
    "apply-scene": "应用场景",
    "fan-speed-up": "风速调高",
    "fan-speed-down": "风速调低",
    "temperature-up": "温度调高",
    "temperature-down": "温度调低",
}

# --------------------------------------------------------- 可选值中文

VALUE_ZH: dict[str, str] = {
    # 空调模式
    "auto": "自动",
    "cool": "制冷",
    "dry": "除湿",
    "heat": "制热",
    "fan": "送风",
    # 风速 / 灵敏度档位
    "low": "低",
    "medium": "中",
    "middle": "中",
    "high": "高",
    # 开关类
    "off": "关闭",
    "on": "打开",
    "idle": "空闲",
    "busy": "忙碌",
    "stateless": "无状态",
    "stateful": "有状态",
    "matching": "已匹配",
    "out of matching": "未匹配",
    "ac mode with outlet": "插座带空调",
    "ac mode without outlet": "插座不带空调",
    "not push": "不推送",
    "only pass": "仅路过",
    "only stay": "仅停留",
    "enable": "开启",
    "disable": "关闭",
    "normal": "正常",
    "error": "异常",
}

# 有些属性的可选值必须按「属性名 + 值」查，光看 description 不够
# （门铃 eco-code 上游只给 0/1/2 三个裸数字）。
VALUE_ZH_BY_PROP: dict[str, dict[str, str]] = {
    "eco-code": {
        "0": "正常",
        "1": "误触发偏多",
        "2": "已自动关闭",
    },
    "match-state": {"0": "未匹配", "1": "匹配中"},
    "ac-work-mode": {"0": "空调插在插座上", "1": "空调没插在插座上"},
    "ac-type": {"0": "无状态空调", "1": "有状态空调"},
    "status": {"1": "空闲", "2": "忙碌"},
    # 上游只给 3/4/5/6 四个裸数字，米家 APP 里是四首内置铃音
    "ringer-music": {"3": "内置铃音 1", "4": "内置铃音 2", "5": "内置铃音 3", "6": "内置铃音 4"},
    "test-spec": {"1": "开始自检"},
}

_NUM_SUFFIX = re.compile(r"-\d+$")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_TAIL_NUM = re.compile(r"^(.*?)\s*(\d+)$")
# 给桥接/界面判断「这个名字是不是已经是中文」用
CJK = _CJK


def _has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def _cjk_segment(text: str) -> str | None:
    """从「英文 / 中文」这类混排里挑出含中文的那一段。"""
    if not text:
        return None
    for part in re.split(r"\s*/\s*|\n|\t", text):
        if _has_cjk(part):
            return part.strip()
    return None


def lookup(name: str, table: dict[str, str]) -> str | None:
    """先精确匹配，未命中再去掉尾部数字后缀重试（与上游同规则）。"""
    if name in table:
        return table[name]
    stripped = _NUM_SUFFIX.sub("", name)
    return table.get(stripped) if stripped != name else None


def to_zh(name: str, desc: str, table: dict[str, str]) -> str:
    """把一条属性/动作描述压成纯中文。

    优先级：本地增量表 → 描述里已有的中文段 → 原描述。
    描述里既有英文又有中文时取中文段，避免界面出现「Fan Level / 风速」
    这种米家 APP 里看不到的混排。
    """
    desc = (desc or "").strip()
    hit = lookup(name, table)
    if hit:
        return hit
    segment = _cjk_segment(desc)
    if segment:
        return segment
    return desc or name


def prop_zh(name: str, desc: str) -> str:
    return to_zh(name, desc, PROPERTY_ZH)


def action_zh(name: str, desc: str) -> str:
    return to_zh(name, desc, ACTION_ZH)


def value_zh(description: str, desc_zh_cn: str | None = None, prop: str = "", value=None) -> str:
    """单个可选值 → 中文。

    上游有的型号自带 `desc_zh_cn`，但它有时只是英文的复读
    （门铃 `Videolength 10`），所以只有含中文时才采信。
    `prop` 用于查按属性区分的表：那类表按**数值**索引（`eco-code`、
    `ringer-music` 上游只给裸数字，甚至 `6` 配的 description 是 `"7"`）。
    """
    for candidate in (desc_zh_cn, description):
        if candidate and _has_cjk(str(candidate)):
            return str(candidate).strip()
    by_prop = VALUE_ZH_BY_PROP.get(prop or "")
    if by_prop and value is not None:
        raw = str(value).strip()
        hit = by_prop.get(raw) or by_prop.get(raw.rstrip(".0") or raw)
        if hit:
            return hit
    key = str(description or "").strip().lower()
    if by_prop:
        hit = by_prop.get(key)
        if hit:
            return hit
    if key in VALUE_ZH:
        return VALUE_ZH[key]
    # 「Videolength 10」这类：取尾部数字补单位，交由调用方决定单位
    matched = _TAIL_NUM.match(str(description or "").strip())
    if matched and not _has_cjk(matched.group(1)):
        return matched.group(2)
    return str(description or "").strip()


def localize_values(items, unit: str = "", prop: str = "") -> list[dict] | None:
    """可选值列表整体本地化，保持 [{value, description}] 结构。"""
    if not isinstance(items, list):
        return None
    result = []
    for item in items:
        if isinstance(item, dict):
            value = item.get("value")
            label = value_zh(item.get("description"), item.get("desc_zh_cn"), prop, value)
            if unit and label.isdigit():
                label = f"{label}{unit}"
            elif unit == "秒" and label.isdigit() is False and label.endswith("秒") is False and str(value) == label:
                label = f"{label} 秒"
            result.append({"value": value, "description": label})
        else:
            label = value_zh(str(item), None, prop, item)
            result.append({"value": item, "description": f"{label}{unit}" if unit and label.isdigit() else label})
    return result


# 数值型属性在界面上要带单位，否则小白看不懂 26 是什么
UNITS: dict[str, str] = {
    "target-temperature": "°C",
    "temperature": "°C",
    "relative-humidity": "%",
    "battery-level": "%",
    "volume": "%",
    "electric-power": "W",
    "power-consumption": "kWh",
    "download-speed": "B/s",
    "upload-speed": "B/s",
    "alarm-interval": "秒",
    "videodelay": "秒",
    "videolength": "秒",
    "online-time": "秒",
    "workinglength": "秒",
    "detection-sensitivity": "",
    "connected-device-number": "台",
}

# 这两个字段是给高级用户看的原始配置，默认折叠
ADVANCED_PROPS = frozenset({
    "brand-id",
    "remote-id",
    "ac-type",
    "ac-ctrl-library",
    "ac-ctrl-library-crc32",
    "match-state",
    "ac-work-mode",
    "ac-state",
    "test-spec",
    "settingsync-time",
    "battypedate",
    "syswork",
    "pir",
    "videonum",
    "fourbat-work",
    "pwrdate",
    "mcu-pir",
    "threeday-work",
    "workinglength",
    "startuptimes",
    "connected",
    "cloud-video-id",
    "autoreply-week",
    "autoreply-time",
    "autoreply-item",
    "motion-time",
    "ringer-time",
    "eco-code",
})
