# SPDX-License-Identifier: GPL-3.0-or-later
"""设备类别与能力项的中英对照字典（翻译基准）。

插件面向模型的工具会输出这些中文名，模型再据此与用户对话。这里的翻译
一旦不统一，前端和提示词就会出现「同一种能力两种叫法」的问题，所以
**所有能力项的中文名都必须从这里取**，不要在适配器里各写各的。

- ``CATEGORY_LABELS``：设备类别（灯、开关、扫地机器人……）。
- ``CAPABILITY_LABELS``：控制能力（开关、亮度、色温……）及其取值类型。
- ``ENUM_LABELS``：枚举值翻译（开/关、低/中/高、制冷/制热……）。
- ``CATEGORY_CONTROLS``：每个类别典型提供哪些控制选项，用于给模型说明
  「这类设备能控制什么」。
- ``CATEGORY_KEYWORDS``：从型号 / 名称 / 页面文本推断类别用的关键词。
"""

from __future__ import annotations

import re

# ------------------------------------------------------------------ 设备类别

CATEGORY_LABELS: dict[str, dict[str, str]] = {
    "light": {"zh": "灯具", "en": "Light"},
    "switch": {"zh": "开关", "en": "Switch"},
    "plug": {"zh": "插座", "en": "Plug / Outlet"},
    "vacuum": {"zh": "扫地机器人", "en": "Robot Vacuum"},
    "air_purifier": {"zh": "空气净化器", "en": "Air Purifier"},
    "humidifier": {"zh": "加湿器", "en": "Humidifier"},
    "dehumidifier": {"zh": "除湿机", "en": "Dehumidifier"},
    "fan": {"zh": "风扇", "en": "Fan"},
    "air_conditioner": {"zh": "空调", "en": "Air Conditioner"},
    "heater": {"zh": "取暖器", "en": "Heater"},
    "refrigerator": {"zh": "冰箱", "en": "Refrigerator"},
    "washing_machine": {"zh": "洗衣机", "en": "Washing Machine"},
    "dryer": {"zh": "烘干机", "en": "Dryer"},
    "dishwasher": {"zh": "洗碗机", "en": "Dishwasher"},
    "oven": {"zh": "烤箱", "en": "Oven"},
    "microwave": {"zh": "微波炉", "en": "Microwave"},
    "rice_cooker": {"zh": "电饭煲", "en": "Rice Cooker"},
    "air_fryer": {"zh": "空气炸锅", "en": "Air Fryer"},
    "kettle": {"zh": "电热水壶", "en": "Electric Kettle"},
    "coffee_machine": {"zh": "咖啡机", "en": "Coffee Machine"},
    "tv": {"zh": "电视", "en": "Television"},
    "soundbar": {"zh": "回音壁", "en": "Soundbar"},
    "speaker": {"zh": "音箱", "en": "Speaker"},
    "projector": {"zh": "投影仪", "en": "Projector"},
    "camera": {"zh": "摄像头", "en": "Camera"},
    "doorbell": {"zh": "门铃", "en": "Doorbell"},
    "lock": {"zh": "门锁", "en": "Smart Lock"},
    "curtain": {"zh": "窗帘", "en": "Curtain"},
    "sensor": {"zh": "传感器", "en": "Sensor"},
    "router": {"zh": "路由器", "en": "Router"},
    "net_switch": {"zh": "交换机", "en": "Network Switch"},
    "onu": {"zh": "光猫", "en": "Optical Modem / ONT"},
    "nas": {"zh": "网络存储", "en": "NAS / Storage"},
    "server": {"zh": "服务器", "en": "Server"},
    "printer": {"zh": "打印机", "en": "Printer"},
    "gateway": {"zh": "网关", "en": "Gateway"},
    "ir_remote": {"zh": "红外遥控", "en": "IR Remote"},
    "meter": {"zh": "智能电表", "en": "Smart Meter"},
    "other": {"zh": "其他设备", "en": "Other Device"},
}

# ------------------------------------------------------------------ 能力项

# kind:
#   bool   —— 开关类，取值开 / 关
#   range  —— 数值范围，带 unit / min / max / step
#   enum   —— 枚举，取值见 ENUM_LABELS 里的对应表
CAPABILITY_LABELS: dict[str, dict] = {
    "power": {"zh": "开关", "en": "Power", "kind": "bool"},
    "brightness": {"zh": "亮度", "en": "Brightness", "kind": "range", "min": 0, "max": 100, "step": 1, "unit": "%"},
    "color_temp": {"zh": "色温", "en": "Color Temperature", "kind": "range", "min": 0, "max": 100, "step": 1, "unit": "%"},
    "color": {"zh": "颜色", "en": "Color", "kind": "range", "min": 0, "max": 360, "step": 1, "unit": "°"},
    "mode": {"zh": "模式", "en": "Mode", "kind": "enum", "enum": "mode"},
    "fan_speed": {"zh": "风速", "en": "Fan Speed", "kind": "enum", "enum": "fan_speed"},
    "target_temp": {"zh": "目标温度", "en": "Target Temperature", "kind": "range", "min": 16, "max": 30, "step": 1, "unit": "°C"},
    "target_humidity": {"zh": "目标湿度", "en": "Target Humidity", "kind": "range", "min": 30, "max": 80, "step": 5, "unit": "%"},
    "swing": {"zh": "摆风", "en": "Swing", "kind": "bool"},
    "position": {"zh": "开合度", "en": "Position", "kind": "range", "min": 0, "max": 100, "step": 1, "unit": "%"},
    "volume": {"zh": "音量", "en": "Volume", "kind": "range", "min": 0, "max": 100, "step": 1, "unit": "%"},
    "mute": {"zh": "静音", "en": "Mute", "kind": "bool"},
    "input_source": {"zh": "输入源", "en": "Input Source", "kind": "enum", "enum": "input_source"},
    "channel": {"zh": "频道", "en": "Channel", "kind": "range", "min": 1, "max": 200, "step": 1, "unit": ""},
    "temperature": {"zh": "温度", "en": "Temperature", "kind": "range", "min": -50, "max": 100, "step": 0.1, "unit": "°C"},
    "humidity": {"zh": "湿度", "en": "Humidity", "kind": "range", "min": 0, "max": 100, "step": 1, "unit": "%"},
    "battery": {"zh": "电量", "en": "Battery", "kind": "range", "min": 0, "max": 100, "step": 1, "unit": "%"},
    "filter_life": {"zh": "滤芯寿命", "en": "Filter Life", "kind": "range", "min": 0, "max": 100, "step": 1, "unit": "%"},
    "schedule": {"zh": "定时", "en": "Schedule", "kind": "enum", "enum": "schedule"},
    "reboot": {"zh": "重启", "en": "Reboot", "kind": "bool"},
    "wake_on_lan": {"zh": "网络唤醒", "en": "Wake on LAN", "kind": "bool"},
    "settings": {"zh": "设备设置", "en": "Device Settings", "kind": "bool"},
    "status": {"zh": "状态", "en": "Status", "kind": "bool"},
    "clean": {"zh": "开始清扫", "en": "Start Cleaning", "kind": "bool"},
    "charge": {"zh": "回充", "en": "Return to Charge", "kind": "bool"},
    "locate": {"zh": "寻找设备", "en": "Locate", "kind": "bool"},
}

# ------------------------------------------------------------------ 枚举翻译

ENUM_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "power": {
        "on": {"zh": "开", "en": "On"},
        "off": {"zh": "关", "en": "Off"},
    },
    "mode": {
        "auto": {"zh": "自动", "en": "Auto"},
        "cool": {"zh": "制冷", "en": "Cool"},
        "heat": {"zh": "制热", "en": "Heat"},
        "dry": {"zh": "除湿", "en": "Dry"},
        "fan": {"zh": "送风", "en": "Fan"},
        "sleep": {"zh": "睡眠", "en": "Sleep"},
        "clean": {"zh": "净化", "en": "Purify"},
        "manual": {"zh": "手动", "en": "Manual"},
        "auto_clean": {"zh": "自动清扫", "en": "Auto Clean"},
        "quiet": {"zh": "静音", "en": "Quiet"},
        "boost": {"zh": "强力", "en": "Boost"},
    },
    "fan_speed": {
        "low": {"zh": "低", "en": "Low"},
        "medium": {"zh": "中", "en": "Medium"},
        "high": {"zh": "高", "en": "High"},
        "auto": {"zh": "自动", "en": "Auto"},
        "silent": {"zh": "静音", "en": "Silent"},
        "turbo": {"zh": "强力", "en": "Turbo"},
    },
    "input_source": {
        "hdmi1": {"zh": "HDMI 1", "en": "HDMI 1"},
        "hdmi2": {"zh": "HDMI 2", "en": "HDMI 2"},
        "hdmi3": {"zh": "HDMI 3", "en": "HDMI 3"},
        "hdmi": {"zh": "HDMI", "en": "HDMI"},
        "av": {"zh": "AV", "en": "AV"},
        "tv": {"zh": "电视", "en": "TV"},
        "pc": {"zh": "电脑", "en": "PC"},
        "usb": {"zh": "USB", "en": "USB"},
        "cast": {"zh": "投屏", "en": "Cast"},
        "bluetooth": {"zh": "蓝牙", "en": "Bluetooth"},
        "optical": {"zh": "光纤", "en": "Optical"},
        "airplay": {"zh": "AirPlay", "en": "AirPlay"},
        "spotify": {"zh": "Spotify", "en": "Spotify"},
    },
    "schedule": {
        "on": {"zh": "开启", "en": "On"},
        "off": {"zh": "关闭", "en": "Off"},
    },
}

# ------------------------------------------------------------------ 类别控制选项

# 每个类别典型能控制哪些能力，顺序即推荐展示顺序。模型据此回答
# 「这台设备能做什么」，避免对不支持的控制项瞎猜。
CATEGORY_CONTROLS: dict[str, list[str]] = {
    "light": ["power", "brightness", "color_temp", "color", "mode"],
    "switch": ["power", "schedule"],
    "plug": ["power", "schedule", "status"],
    "vacuum": ["power", "clean", "charge", "mode", "locate", "status"],
    "air_purifier": ["power", "mode", "fan_speed", "filter_life", "status"],
    "humidifier": ["power", "target_humidity", "mode", "status"],
    "dehumidifier": ["power", "target_humidity", "mode", "status"],
    "fan": ["power", "mode", "fan_speed", "swing", "status"],
    "air_conditioner": ["power", "mode", "target_temp", "fan_speed", "swing", "status"],
    "heater": ["power", "target_temp", "mode", "status"],
    "refrigerator": ["power", "mode", "status"],
    "washing_machine": ["power", "mode", "schedule", "status"],
    "dryer": ["power", "mode", "schedule", "status"],
    "dishwasher": ["power", "mode", "schedule", "status"],
    "oven": ["power", "target_temp", "mode", "schedule", "status"],
    "microwave": ["power", "schedule", "status"],
    "rice_cooker": ["power", "mode", "schedule", "status"],
    "air_fryer": ["power", "target_temp", "mode", "schedule", "status"],
    "kettle": ["power", "schedule", "status"],
    "coffee_machine": ["power", "mode", "schedule", "status"],
    "tv": ["power", "input_source", "volume", "mute", "channel", "status"],
    "soundbar": ["power", "input_source", "volume", "mute", "status"],
    "speaker": ["power", "volume", "mute", "input_source", "status"],
    "projector": ["power", "brightness", "mode", "status"],
    "camera": ["power", "status"],
    "doorbell": ["power", "status"],
    "lock": ["status"],
    "curtain": ["power", "position", "status"],
    "sensor": ["status"],
    "router": ["power", "reboot", "status", "settings"],
    "net_switch": ["power", "status", "settings"],
    "onu": ["power", "reboot", "status", "settings"],
    "nas": ["power", "reboot", "status", "settings"],
    "server": ["power", "reboot", "status", "settings"],
    "printer": ["power", "status", "settings"],
    "gateway": ["power", "reboot", "status"],
    "ir_remote": ["power", "mode", "status"],
    "meter": ["status"],
    "other": ["status"],
}

# ------------------------------------------------------------------ 关键词推断

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "light": ["light", "lamp", "bulb", "luminaire", "yeelight", "灯", "灯泡", "台灯", "吊灯"],
    "switch": ["switch", "继电器", "开关"],
    "plug": ["plug", "socket", "outlet", "插座", "插排", "排插"],
    "vacuum": ["vacuum", "roborock", "robot", "扫拖", "扫地", "清洁机器人"],
    "air_purifier": ["purifier", "净化器"],
    "humidifier": ["humidifier", "加湿器"],
    "dehumidifier": ["dehumidifier", "除湿机"],
    "fan": ["fan", "风扇", "空气循环"],
    "air_conditioner": ["air conditioner", "ac_", "空调", "空调伴侣"],
    "heater": ["heater", "取暖器", "电暖器", "暖风机"],
    "refrigerator": ["refrigerator", "fridge", "冰箱", "冷柜"],
    "washing_machine": ["washing machine", "washer", "洗衣机", "洗烘一体机"],
    "dryer": ["dryer", "烘干机", "干衣机"],
    "dishwasher": ["dishwasher", "洗碗机"],
    "oven": ["oven", "烤箱"],
    "microwave": ["microwave", "微波炉"],
    "rice_cooker": ["rice cooker", "cooker", "电饭煲", "电饭锅"],
    "air_fryer": ["air fryer", "空气炸锅"],
    "kettle": ["kettle", "热水壶", "电水壶"],
    "coffee_machine": ["coffee", "咖啡机"],
    "tv": ["television", "tv", "电视", "电视盒子"],
    "soundbar": ["soundbar", "回音壁"],
    "speaker": ["speaker", "音箱", "音响"],
    "projector": ["projector", "投影"],
    "camera": ["camera", "cam", "摄像头", "摄像机"],
    "doorbell": ["doorbell", "门铃"],
    "lock": ["lock", "门锁", "智能锁"],
    "curtain": ["curtain", "窗帘", "卷帘"],
    "sensor": ["sensor", "传感器", "温湿度", "人体", "门窗", "水浸", "烟雾", "燃气"],
    "router": ["router", "路由器", "无线"],
    "net_switch": ["network switch", "ethernet switch", "managed switch", "unmanaged switch", "poe switch", "gigabit switch", "交换机"],
    "onu": ["onu", "ont", "光猫", "光调制解调器", "gpon", "epon"],
    "nas": ["nas", "synology", "qnap", "群晖", "存储服务器"],
    "server": ["server", "服务器"],
    "printer": ["printer", "打印机"],
    "gateway": ["gateway", "网关", "中枢"],
    "ir_remote": ["ir_remote", "红外遥控", "遥控"],
    "meter": ["meter", "电表", "水表", "燃气表"],
}

# ------------------------------------------------------------------ 辅助函数


def category_zh(key: str) -> str:
    return CATEGORY_LABELS.get(key, {"zh": "其他设备"})["zh"]


def category_en(key: str) -> str:
    return CATEGORY_LABELS.get(key, {"en": "Other Device"})["en"]


def capability_zh(key: str) -> str:
    return CAPABILITY_LABELS.get(key, {"zh": key})["zh"]


def capability_en(key: str) -> str:
    return CAPABILITY_LABELS.get(key, {"en": key})["en"]


def capability_info(key: str) -> dict:
    """能力项的完整元信息（含中文名），供控制选项返回给模型。"""
    info = CAPABILITY_LABELS.get(key)
    if not info:
        return {"key": key, "zh": key, "en": key, "kind": "enum"}
    out = {"key": key, "zh": info["zh"], "en": info["en"], "kind": info.get("kind", "bool")}
    if info.get("kind") == "range":
        out["unit"] = info.get("unit", "")
        out["min"] = info.get("min")
        out["max"] = info.get("max")
        out["step"] = info.get("step")
    if info.get("enum"):
        out["values"] = enum_values(info["enum"])
    return out


def enum_values(enum_name: str) -> list[dict]:
    """枚举项列表：``[{"value": "on", "zh": "开", "en": "On"}, ...]``。"""
    return [
        {"value": value, "zh": item["zh"], "en": item["en"]}
        for value, item in ENUM_LABELS.get(enum_name, {}).items()
    ]


def enum_zh(enum_name: str, value: object) -> str | None:
    """把设备返回的原始枚举值翻成中文；不认识就返回 None。"""
    if value is None:
        return None
    raw = str(value)
    table = ENUM_LABELS.get(enum_name, {})
    if raw in table:
        return table[raw]["zh"]
    lowered = raw.lower()
    if lowered in table:
        return table[lowered]["zh"]
    # 兼容设备返回中文/英文字面量的情况：本身就命中就不翻译
    for v, item in table.items():
        if raw == item["zh"] or raw == item["en"]:
            return item["zh"]
    return None


def guess_category(*texts: str) -> str:
    """从多段文本（型号、名称、页面标题）推断设备类别。

    命中关键词最多的类别胜出；明确的多词短语比单个泛词优先，
    例如 ``network switch`` 优先于电气开关的 ``switch``。
    一个都没命中返回 ``other``。

    **短关键词必须按词边界匹配**：早期用裸 ``in`` 判断，``ont``（光猫的英文
    缩写）会命中 HTML 里的 ``content=``、``font-family`` 之类，把一台 TP-Link
    路由器判成光猫。ASCII 关键词走词边界正则，中文关键词不受影响（中文没有
    词边界概念，直接包含判断即可）。
    """
    best = "other"
    best_score = 0
    for text in texts:
        if not text:
            continue
        lowered = str(text).lower()
        for category, keywords in CATEGORY_KEYWORDS.items():
            score = 0
            for kw in keywords:
                token = kw.lower()
                if not token:
                    continue
                if token.isascii():
                    # 词边界：前后不能是字母数字，避免 ont 命中 content
                    if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lowered):
                        score += max(1, len(token.split()))
                elif token in lowered:
                    score += 1
            if score > best_score:
                best_score = score
                best = category
    return best


def category_controls(key: str) -> list[dict]:
    """类别对应的控制选项清单（含中文名与取值类型）。"""
    return [capability_info(item) for item in CATEGORY_CONTROLS.get(key, [])]
