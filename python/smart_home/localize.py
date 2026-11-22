# SPDX-License-Identifier: GPL-3.0-or-later
"""分层翻译：把设备原始的属性名 / 选项 / 动作翻译成中文。

翻译优先级（用户明确要求的口径）：

1. **官网资料**：米家设备的 ``urn:miot-spec`` 属性由 ``app.core.spec_zh_ext``
   提供，这是小米官方规格，最权威。
2. **社区资料**：主流协议（涂鸦 / Shelly / Tasmota / Kasa / Yeelight / LIFX /
   Hue）的常见字段，集中放在 ``PROTOCOL_ZH`` 里，来源是各协议社区文档。
3. **通用翻译**：以上都没有的，按属性名里的英文单词查 ``GENERIC_WORDS``
   逐词翻译（snake_case / camelCase 都会拆）。
4. **兜底**：实在翻不出就保留原名——宁可让模型看到英文，也不给一个
   会误导的中文。

所有翻译都是**纯本地、无网络**的：扫描和控制要能离线工作，不能因为查不到
某个字段就卡住。
"""

from __future__ import annotations

import copy
import re
from functools import lru_cache

from . import catalog

# ------------------------------------------------------------------ 通用词表

# 常见英文词 -> 中文。按「单词」匹配，属性名先拆词再逐词翻译。
GENERIC_WORDS: dict[str, str] = {
    "power": "电源",
    "on": "开",
    "off": "关",
    "switch": "开关",
    "toggle": "切换",
    "state": "状态",
    "status": "状态",
    "brightness": "亮度",
    "bri": "亮度",
    "color": "颜色",
    "colour": "颜色",
    "color_temp": "色温",
    "temp": "温度",
    "temperature": "温度",
    "target_temp": "目标温度",
    "humidity": "湿度",
    "target_humidity": "目标湿度",
    "fan": "风速",
    "speed": "风速",
    "mode": "模式",
    "timer": "定时",
    "schedule": "定时",
    "delay": "延时",
    "lock": "锁",
    "unlock": "解锁",
    "volume": "音量",
    "mute": "静音",
    "channel": "频道",
    "input": "输入",
    "source": "来源",
    "battery": "电量",
    "filter": "滤芯",
    "filter_life": "滤芯寿命",
    "clean": "清扫",
    "charge": "回充",
    "locate": "定位",
    "reboot": "重启",
    "reset": "重置",
    "wifi": "无线",
    "signal": "信号",
    "name": "名称",
    "model": "型号",
    "device": "设备",
    "light": "灯",
    "bulb": "灯泡",
    "lamp": "灯",
    "plug": "插座",
    "socket": "插座",
    "outlet": "插座",
    "relay": "继电器",
    "curtain": "窗帘",
    "position": "开合度",
    "open": "开启",
    "close": "关闭",
    "swing": "摆风",
    "anion": "负离子",
    "uv": "紫外线",
    "sleep": "睡眠",
    "quiet": "静音",
    "silent": "静音",
    "turbo": "强力",
    "strong": "强力",
    "boost": "强力",
    "auto": "自动",
    "manual": "手动",
    "cool": "制冷",
    "heat": "制热",
    "dry": "除湿",
    "fan_mode": "送风",
    "air": "空气",
    "purifier": "净化器",
    "humidifier": "加湿器",
    "dehumidifier": "除湿机",
    "fan_speed": "风速",
    "level": "档位",
    "duration": "时长",
    "time": "时间",
    "date": "日期",
    "default": "默认",
    "enable": "启用",
    "disable": "禁用",
    "supported": "支持",
    "current": "当前",
    "target": "目标",
    "min": "最小",
    "max": "最大",
    "step": "步进",
    "range": "范围",
    "unit": "单位",
    "description": "描述",
    "readable": "可读",
    "writable": "可写",
    "action": "动作",
    "execute": "执行",
    "run": "运行",
    "start": "开始",
    "stop": "停止",
    "pause": "暂停",
    "resume": "继续",
    "home": "家庭",
    "room": "房间",
    "online": "在线",
    "offline": "离线",
    "protocol": "协议",
    "category": "类别",
    "ip": "地址",
    "mac": "物理地址",
    "did": "设备标识",
    "key": "密钥",
    "token": "令牌",
    "local": "本地",
    "cloud": "云端",
    "miio": "米家",
    "mihome": "米家",
    "tuya": "涂鸦",
    "shelly": "Shelly",
    "tasmota": "Tasmota",
    "wled": "WLED",
    "kasa": "Kasa",
    "yeelight": "Yeelight",
    "hue": "Hue",
    "lifx": "LIFX",
    "esphome": "ESPHome",
    "sonoff": "Sonoff",
    "esp": "芯片",
}

# 需要整体优先匹配、不拆词的短语（避免 color_temp 被拆成 颜色 + 温度 但顺序错）
PHRASES: dict[str, str] = {
    "color_temp": "色温",
    "color_temperature": "色温",
    "target_temp": "目标温度",
    "target_temperature": "目标温度",
    "target_humidity": "目标湿度",
    "fan_speed": "风速",
    "filter_life": "滤芯寿命",
    "filter_life_remaining": "滤芯剩余寿命",
    "power_state": "电源状态",
    "input_source": "输入源",
    "brightness_level": "亮度",
    "switch_state": "开关状态",
    "run_status": "运行状态",
    "work_status": "工作状态",
    "device_status": "设备状态",
}


def _split_tokens(name: str) -> list[str]:
    """把属性名拆成单词：snake_case / kebab-case / camelCase 都处理。"""
    text = re.sub(r"[^0-9A-Za-z]+", "_", str(name))
    # 数字与字母之间也切开（如 dp_1、hue_2）
    text = re.sub(r"(?<=[a-zA-Z])(?=\d)|(?<=\d)(?=[a-zA-Z])", "_", text)
    tokens: list[str] = []
    for part in text.split("_"):
        if not part:
            continue
        # camelCase 进一步拆
        tokens.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part))
    return [t.lower() for t in tokens]


def translate_name(name: str, description: str = "", known: dict | None = None) -> str:
    """优先保留设备的中文描述，再查已知词表、完整短语和通用词。"""
    raw = str(name or "")
    description = str(description or "")
    if re.search(r"[\u4e00-\u9fff]", description):
        return description
    if not raw:
        return description
    tokens = _split_tokens(raw)
    normalized = "_".join(tokens)
    if known:
        for key in (raw, normalized, normalized.replace("_", "-")):
            if key in known:
                return known[key]
    if normalized in PHRASES:
        return PHRASES[normalized]
    if not tokens:
        return raw

    # 逐词翻译，保留原顺序；连续两个及以上命中才认为是可信翻译，
    # 避免 "model" 之类偶然命中导致误译。
    translated = [GENERIC_WORDS.get(t, t) for t in tokens]
    hits = sum(1 for t in tokens if t in GENERIC_WORDS)
    if hits >= 2:
        return " ".join(translated)
    if hits == 1 and len(tokens) <= 2:
        return " ".join(translated)

    # 单一 token 且是常见能力词，也翻译
    if hits == 1 and tokens[0] in GENERIC_WORDS and len(tokens) == 1:
        return GENERIC_WORDS[tokens[0]]
    return raw


def translate_value(value: object, enum_name: str = "", known_values: dict | None = None) -> str:
    """翻译一个属性取值。

    优先查协议社区字典（``known_values``），再查通用枚举表，最后原样返回。
    """
    if value is None:
        return ""
    raw = str(value)
    if known_values and raw in known_values:
        return known_values[raw]
    zh = catalog.enum_zh(enum_name, raw)
    if zh is not None:
        return zh
    # 仅原生 bool 或明确的布尔能力上下文允许把 0/1 显示为开关。
    # mode=0、数字字符串及其它枚举必须保留原意。
    boolean_context = catalog.CAPABILITY_LABELS.get(enum_name, {}).get("kind") == "bool"
    if isinstance(value, bool) or boolean_context:
        if raw.lower() in ("true", "1", "on", "yes"):
            return "开"
        if raw.lower() in ("false", "0", "off", "no"):
            return "关"
    return raw


def translate_value_list(
    values: list,
    enum_name: str = "",
    known_values: dict | None = None,
) -> list[dict]:
    """翻译一组可选值，返回 ``[{"value": 原始值, "zh": 中文, "en": 英文}]``。"""
    out: list[dict] = []
    for value in values or []:
        # MIoT 等协议已经提供 {value, description, ...}，只能补标签，
        # 不能把整条记录再次塞进 value，也不能把线上值转成字符串。
        entry = copy.deepcopy(value) if isinstance(value, dict) else {"value": copy.deepcopy(value)}
        raw_value = entry.get("value")
        description = str(entry.get("desc_zh_cn") or entry.get("description") or entry.get("desc") or "")
        if not entry.get("zh"):
            if re.search(r"[\u4e00-\u9fff]", description):
                entry["zh"] = description
            else:
                label = description or raw_value
                translated = translate_value(label, enum_name, known_values)
                entry["zh"] = translated if translated != str(label) else translate_name(str(label))
        if not entry.get("en"):
            entry["en"] = description or str(raw_value)
        out.append(entry)
    return out


# ------------------------------------------------------------------ 协议社区字典

# 主流协议的常见字段翻译。这些字段名来自各协议社区文档与常见固件，
# 属于「官网没有、社区资料」这一层。
PROTOCOL_ZH: dict[str, dict[str, str]] = {
    # 米家规格（miot-spec）。完整表在 app/core/spec_zh_ext.py（官方规格），
    # 这里放最常见的字段，作为本地查表的快速通道。
    "mihome": {
        "power": "电源",
        "on": "开",
        "off": "关",
        "brightness": "亮度",
        "color_temp": "色温",
        "color": "颜色",
        "mode": "模式",
        "fan_speed": "风速",
        "target_temp": "目标温度",
        "target_humidity": "目标湿度",
        "swing": "摆风",
        "position": "开合度",
        "volume": "音量",
        "mute": "静音",
        "input_source": "输入源",
        "channel": "频道",
        "clean": "清扫",
        "charge": "回充",
        "locate": "定位",
        "reboot": "重启",
        "filter_life": "滤芯寿命",
        "battery": "电量",
        "schedule": "定时",
        "timer": "定时",
        "child_lock": "童锁",
        "swing_vertical": "上下摆风",
        "swing_horizontal": "左右摆风",
        "work_mode": "工作模式",
        "work_status": "工作状态",
        "run_status": "运行状态",
        "status": "状态",
    },
    # 涂鸦 Data Point（DP）
    "tuya": {
        "power": "开关",
        "switch": "开关",
        "brightness": "亮度",
        "brightness_value": "亮度",
        "color": "颜色",
        "color_temp": "色温",
        "color_temperature": "色温",
        "mode": "模式",
        "fan_speed": "风速",
        "target_temp": "目标温度",
        "target_temperature": "目标温度",
        "humidity": "湿度",
        "target_humidity": "目标湿度",
        "curtain": "窗帘",
        "position": "开合度",
        "volume": "音量",
        "mute": "静音",
        "input_source": "输入源",
        "channel": "频道",
        "work_mode": "工作模式",
        "work_status": "工作状态",
        "run_status": "运行状态",
        "filter_life": "滤芯寿命",
        "battery_percentage": "电量",
        "battery_percent": "电量",
        "local_state": "本地状态",
        "schedule": "定时",
        "timer": "定时",
        "child_lock": "童锁",
        "anion": "负离子",
        "uv": "紫外线",
        "swing": "摆风",
        "speed": "风速",
        "level": "档位",
        "clean": "清扫",
        "charge": "回充",
        "locate": "定位",
        "reboot": "重启",
        "start": "开始",
        "stop": "停止",
        "pause": "暂停",
        "resume": "继续",
        "lock": "锁",
        "unlock": "解锁",
        "open": "开启",
        "close": "关闭",
        "online": "在线",
        "offline": "离线",
    },
    # Shelly
    "shelly": {
        "ison": "开关状态",
        "sw": "开关",
        "sw_1": "开关 1",
        "sw_2": "开关 2",
        "sw_3": "开关 3",
        "sw_4": "开关 4",
        "power": "功率",
        "energy": "电量",
        "energy_watts": "功率",
        "voltage": "电压",
        "current": "电流",
        "brightness": "亮度",
        "bri": "亮度",
        "color_temp": "色温",
        "ct": "色温",
        "color": "颜色",
        "rgb": "颜色",
        "rgbw": "颜色",
        "mode": "模式",
        "power_mode": "电源模式",
        "volume": "音量",
        "mute": "静音",
        "input": "输入",
        "input_1": "输入 1",
        "input_2": "输入 2",
        "input_3": "输入 3",
        "input_4": "输入 4",
        "roller": "卷帘",
        "position": "开合度",
        "status": "状态",
        "timer": "定时",
        "schedule": "定时",
        "reboot": "重启",
        "sys": "系统",
        "wifi": "无线",
        "ap": "接入点",
    },
    # Tasmota
    "tasmota": {
        "power": "电源",
        "power_state": "电源状态",
        "brightness": "亮度",
        "dimmer": "调光",
        "color": "颜色",
        "color_temp": "色温",
        "color_temp_kelvin": "色温",
        "mode": "模式",
        "light_mode": "灯光模式",
    },
    # WLED
    "wled": {
        "on": "开关",
        "bri": "亮度",
        "brightness": "亮度",
        "seg": "分区",
        "seg0": "分区 0",
        "seg1": "分区 1",
        "color": "颜色",
        "cl": "颜色",
        "fx": "效果",
        "mode": "模式",
        "playlist": "播放列表",
        "pl": "播放列表",
        "ms": "时长",
        "master": "总开关",
    },
    # TP-Link Kasa
    "kasa": {
        "system": "系统",
        "get_sysinfo": "读取系统信息",
        "relay_state": "开关状态",
        "ison": "开关状态",
        "err_code": "错误码",
        "uptime": "运行时长",
        "alias": "名称",
        "model": "型号",
        "hw_ver": "硬件版本",
        "sw_ver": "固件版本",
        "mac": "物理地址",
        "location": "位置",
        "rssi": "信号强度",
        "smartlife": "智能生活",
        "smartplug": "智能插座",
        "get_dev_realtime": "读取实时数据",
        "voltage_mv": "电压",
        "power_mw": "功率",
        "current_ma": "电流",
        "energy_wh": "电量",
        "over_power": "过载",
        "set_relay_state": "设置开关状态",
        "set_dp_mode": "设置工作模式",
        "dp_mode": "工作模式",
    },
    # Yeelight
    "yeelight": {
        "get_prop": "读取属性",
        "set_power": "设置电源",
        "set_bright": "设置亮度",
        "set_ct_abx": "设置色温与亮度",
        "set_rgb": "设置颜色",
        "set_hsv": "设置色相饱和度",
        "set_scene": "设置场景",
        "set_music": "设置音乐",
        "get_status": "读取状态",
        "start_music": "开始音乐",
        "stop_music": "停止音乐",
        "set_name": "设置名称",
        "toggle": "切换",
        "set_ct": "设置色温",
        "set_rgb": "设置颜色",
        "bri": "亮度",
        "bright": "亮度",
        "hue": "色相",
        "sat": "饱和度",
        "ct": "色温",
        "rgb": "颜色",
        "power": "电源",
        "mode": "模式",
        "scene": "场景",
    },
    # LIFX
    "lifx": {
        "power": "电源",
        "brightness": "亮度",
        "color": "颜色",
        "hue": "色相",
        "saturation": "饱和度",
        "kelvin": "色温",
        "label": "名称",
        "relay_state": "开关状态",
        "infrared_power": "红外功率",
        "infrared_state": "红外状态",
        "wifi_signal": "无线信号",
        "last_seen": "最后在线",
        "get": "读取",
        "set": "设置",
        "state": "状态",
    },
    # Philips Hue
    "hue": {
        "on": "开关",
        "bri": "亮度",
        "bri_max": "最大亮度",
    # Hue 的属性名本身很简短，主要靠通用词表翻译
    },
    # Home Assistant
    "home_assistant": {
        "state": "状态",
        "attributes": "属性",
        "entity_id": "实体",
        "friendly_name": "名称",
        "available": "可用",
        "last_changed": "最后变更",
        "last_updated": "最后更新",
    },
    # ONVIF
    "onvif": {
        "device_service": "设备服务",
        "device_information": "设备信息",
        "media": "媒体",
        "profiles": "配置",
        "stream_uri": "流地址",
        "ptz": "云台",
        "status": "状态",
        "power": "电源",
        "reboot": "重启",
        "system": "系统",
    },
    # 通用网络设备
    "network": {
        "uptime": "运行时长",
        "firmware": "固件",
        "firmware_version": "固件版本",
        "hardware_version": "硬件版本",
        "software_version": "软件版本",
        "model": "型号",
        "serial": "序列号",
        "mac": "物理地址",
        "ip_address": "IP 地址",
        "dns_server": "DNS 服务器",
        "gateway": "网关",
        "wifi": "无线",
        "wifi_ssid": "无线名称",
        "wifi_password": "无线密码",
        "wifi_channel": "无线信道",
        "wifi_mode": "无线模式",
        "wifi_enabled": "无线开关",
        "wan": "外网",
        "lan": "内网",
        "dhcp": "动态主机配置",
        "dhcp_enabled": "DHCP 开关",
        "system_info": "系统信息",
        "status": "状态",
        "reboot": "重启",
        "reset": "恢复出厂",
        "admin_password": "管理密码",
        "led": "指示灯",
        "led_enabled": "指示灯开关",
        "power": "电源",
        "port": "端口",
        "vlan": "虚拟局域网",
        "port_security": "端口安全",
        "stp": "生成树",
        "link_speed": "链路速率",
        "duplex": "双工模式",
        "mirror": "端口镜像",
        "qos": "服务质量",
    },
}


def protocol_zh(protocol: str, name: str) -> str:
    """按官方规格、协议社区字典和通用词表依次翻译。"""
    return _field_zh(name, protocol=protocol)


def protocol_value_zh(protocol: str, value: object, enum_name: str = "") -> str:
    """按协议查社区字典翻译取值。"""
    return translate_value(value, enum_name)


# ------------------------------------------------------------------ 平台资料

# 厂商 / 平台官方资料层。天猫精灵（AliGenie）、华为智慧生活（AI Life）等
# 平台收录了大量设备，其属性名与米家规格并不一致，但语义相同。这里收录它们
# 的常见属性名作为「官网资料」的补充，翻译时在协议字典之后、通用词表之前
# 匹配。
PLATFORM_ZH: dict[str, dict[str, str]] = {
    "aligenie": {
        # 天猫精灵
        "switch": "开关",
        "power": "电源",
        "brightness": "亮度",
        "color_temp": "色温",
        "color": "颜色",
        "mode": "模式",
        "fan_speed": "风速",
        "speed": "风速",
        "target_temp": "目标温度",
        "humidity": "湿度",
        "target_humidity": "目标湿度",
        "curtain": "窗帘",
        "position": "开合度",
        "volume": "音量",
        "mute": "静音",
        "input_source": "输入源",
        "channel": "频道",
        "work_mode": "工作模式",
        "work_status": "工作状态",
        "filter_life": "滤芯寿命",
        "battery": "电量",
        "timer": "定时",
        "schedule": "定时",
        "child_lock": "童锁",
        "swing": "摆风",
        "clean": "清扫",
        "charge": "回充",
        "locate": "定位",
        "reboot": "重启",
        "start": "开始",
        "stop": "停止",
        "pause": "暂停",
        "resume": "继续",
        "lock": "锁",
        "unlock": "解锁",
        "open": "开启",
        "close": "关闭",
        "online": "在线",
        "offline": "离线",
        "level": "档位",
        "duration": "时长",
        "delay": "延时",
    },
    "ailife": {
        # 华为智慧生活
        "switch": "开关",
        "power": "电源",
        "on": "开",
        "off": "关",
        "brightness": "亮度",
        "color_temp": "色温",
        "color": "颜色",
        "mode": "模式",
        "fan_speed": "风速",
        "speed": "风速",
        "target_temp": "目标温度",
        "target_temperature": "目标温度",
        "humidity": "湿度",
        "target_humidity": "目标湿度",
        "curtain": "窗帘",
        "position": "开合度",
        "volume": "音量",
        "mute": "静音",
        "input_source": "输入源",
        "channel": "频道",
        "work_mode": "工作模式",
        "run_status": "运行状态",
        "filter_life": "滤芯寿命",
        "battery": "电量",
        "timer": "定时",
        "schedule": "定时",
        "child_lock": "童锁",
        "swing": "摆风",
        "anion": "负离子",
        "uv": "紫外线",
        "clean": "清扫",
        "charge": "回充",
        "locate": "定位",
        "reboot": "重启",
        "start": "开始",
        "stop": "停止",
        "pause": "暂停",
        "resume": "继续",
        "lock": "锁",
        "unlock": "解锁",
        "open": "开启",
        "close": "关闭",
        "online": "在线",
        "offline": "离线",
        "level": "档位",
        "duration": "时长",
        "delay": "延时",
        "temperature": "温度",
        "state": "状态",
        "status": "状态",
    },
}


def platform_zh(platform: str, name: str) -> str:
    """按厂商 / 平台字典翻译（天猫精灵、华为智慧生活等）。"""
    table = PLATFORM_ZH.get(platform, {})
    if name in table:
        return table[name]
    return ""


# ------------------------------------------------------------------ 设备本地化


@lru_cache(maxsize=1)
def _official_tables() -> tuple[dict, dict, dict, dict]:
    """只加载纯词表；桥接未安装 app 时仍可离线走社区与通用层。"""
    try:
        from app.core.spec_zh_ext import ACTION_ZH, PROPERTY_ZH, VALUE_ZH, VALUE_ZH_BY_PROP
    except ImportError:
        return {}, {}, {}, {}
    return PROPERTY_ZH, ACTION_ZH, VALUE_ZH, VALUE_ZH_BY_PROP


def _field_zh(name: str, description: str = "", protocol: str = "", platform: str = "", *, action: bool = False) -> str:
    if re.search(r"[\u4e00-\u9fff]", str(description)):
        return str(description)
    normalized = "_".join(_split_tokens(name))
    keys = (name, normalized, normalized.replace("_", "-"))
    official = _official_tables()[1 if action else 0] if protocol in ("mihome", "miio", "miot") else {}
    for table in (official, PROTOCOL_ZH.get(protocol, {}), PLATFORM_ZH.get(platform, {})):
        for key in keys:
            if key in table:
                return table[key]
    # catalog.capability_zh 返回原始 key，不能用 truthy 判断是否命中。
    if normalized in catalog.CAPABILITY_LABELS:
        return catalog.capability_zh(normalized)
    return translate_name(name, description)


def _value_labels(protocol: str, name: str) -> dict:
    if protocol not in ("mihome", "miio", "miot"):
        return {}
    _, _, generic, by_prop = _official_tables()
    key = "-".join(_split_tokens(name))
    return {**generic, **by_prop.get(key, {})}


def localize_controls(controls: list[dict], protocol: str = "") -> list[dict]:
    """补齐公共 schema 和显示标签，保留设备原始范围、枚举类型及写权限。"""
    out = []
    for item in controls or []:
        entry = copy.deepcopy(item)
        entry["key"] = str(entry.get("key") or entry.get("name") or "")
        entry["name"] = str(entry.get("name") or entry["key"])
        if "range" not in entry:
            entry["range"] = [entry["min"], entry["max"], entry.get("step", 1)] if entry.get("min") is not None and entry.get("max") is not None else None
        if "value_list" not in entry:
            entry["value_list"] = copy.deepcopy(entry.get("values") or [])
        if isinstance(entry["value_list"], list):
            entry["value_list"] = translate_value_list(entry["value_list"], entry.get("enum") or entry["key"], _value_labels(protocol, entry["name"]))
        if not entry.get("kind"):
            if entry["value_list"]:
                entry["kind"] = "enum"
            elif entry["range"] is not None:
                entry["kind"] = "range"
            elif str(entry.get("type", "")).lower() in ("bool", "boolean"):
                entry["kind"] = "bool"
            else:
                entry["kind"] = "value"
        entry.setdefault("writable", False)
        if not entry.get("zh"):
            entry["zh"] = _field_zh(entry["name"], entry.get("desc_zh_cn") or entry.get("desc") or entry.get("description") or "", protocol)
        if not entry.get("en"):
            entry["en"] = entry.get("desc") or entry.get("description") or entry["name"]
        out.append(entry)
    return out


def localize_device(info: dict, protocol: str = "", platform: str = "") -> dict:
    """深拷贝后补标签；不改变原始值、枚举元数据或调用方的对象。"""
    out = copy.deepcopy(info or {})
    for collection, action in (("props", False), ("actions", True)):
        for entry in out.get(collection) or []:
            name = str(entry.get("name") or "")
            description = entry.get("desc_zh_cn") or entry.get("desc") or entry.get("description") or ""
            if not entry.get("zh"):
                entry["zh"] = _field_zh(name, description, protocol, platform, action=action)
            if not entry.get("en"):
                entry["en"] = entry.get("desc") or entry.get("description") or name
            if isinstance(entry.get("value_list"), list):
                entry["value_list"] = translate_value_list(entry["value_list"], entry.get("enum") or name, _value_labels(protocol, name))
    return out
