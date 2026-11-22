#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""全屋智能家居离线自检。

不联网、不碰真实设备：用假适配器验证管理器的核心链路（扫描结构 → 设备
详情 → 统一控制 → 场景 → 翻译），作为插件改动的回归基线。

    .venv\\Scripts\\python.exe python\\smart_home\\test_offline.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from smart_home import catalog, localize  # noqa: E402
from smart_home.adapters import BaseAdapter, register  # noqa: E402
from smart_home.manager import SmartHomeManager  # noqa: E402


@register
class FakeAdapter(BaseAdapter):
    """假适配器：记录写入，模拟可读写能力。"""

    protocol = "fake"

    def __init__(self, device, config=None):
        super().__init__(device, config)
        self.written: list[tuple] = []
        self.read = {"power": True, "brightness": 60}

    async def list_capabilities(self) -> list[dict]:
        return [
            {"key": "power", "name": "power", "zh": catalog.capability_zh("power"),
             "en": catalog.capability_en("power"), "kind": "bool", "writable": True},
            {"key": "brightness", "name": "brightness", "zh": catalog.capability_zh("brightness"),
             "en": catalog.capability_en("brightness"), "kind": "range", "writable": True},
        ]

    async def read_capability(self, capability: str):
        return self.read.get(capability)

    async def write_capability(self, capability: str, value) -> dict:
        if capability not in ("power", "brightness"):
            raise ValueError(f"fake 不支持写入 {capability}")
        self.written.append((capability, value))
        self.read[capability] = value
        return {"ok": True, "capability": capability, "value": value}

    async def run_action(self, name: str, params=None) -> dict:
        self.written.append((f"action:{name}", params))
        return {"ok": True, "action": name}

    async def read_all(self) -> dict:
        return dict(self.read)


class FakeScanned:
    """模拟 discovery.ScannedDevice 的最小对象。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        raise SystemExit(1)


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="dsh-mihome-smarthome-")
    manager = SmartHomeManager({"data_dir": tmp})

    print("[1] 目录与翻译")
    _check("能力项有中文名", catalog.capability_zh("brightness") == "亮度")
    _check("类别有中文名", catalog.category_zh("vacuum") == "扫地机器人")
    _check("枚举有中文名", catalog.enum_zh("power", "on") == "开")
    _check("官网规格翻译", localize.protocol_zh("mihome", "brightness") == "亮度")
    _check("通用翻译", localize.translate_name("target_temp") == "目标温度")
    _check("平台字典", localize.platform_zh("aligenie", "color_temp") == "色温")
    _check("类别推断", catalog.guess_category("Roborock S8", "扫地机器人") == "vacuum")
    # 回归：ont 是「光猫」缩写，但会命中 HTML 的 content= / font。
    # 裸 in 判断曾把 TP-Link 路由器判成光猫。
    _check("ont 不误命中 content", catalog.guess_category("x", '<meta content="text/html">') != "onu")
    _check("ont 能认出真光猫", catalog.guess_category("ONT GPON 光猫") == "onu")

    print("[1b] 指纹识别（不联网，走纯函数）")
    from smart_home import discovery as _disc
    # TP-Link 登录页标题只有型号，页面里不出现品牌名——必须靠 TL- 前缀认出来
    probe = _disc.fingerprint_http("TL-WDA6332RE", "", "<title>TL-WDA6332RE</title>", "192.0.2.9", 80)
    _check("TL- 型号认作 TP-Link", probe is not None and probe.protocol == "tplink", str(probe))
    _check("TP-Link 型号不当光猫", probe is not None and probe.category != "onu", str(probe))
    probe_onu = _disc.fingerprint_http("ONT", "", "<title>GPON ONT</title> gpon epon", "192.0.2.10", 80)
    _check("真光猫仍判为 onu", probe_onu is not None and probe_onu.category == "onu", str(probe_onu))

    print("[1c] 指纹产出的协议都有适配器 / 别名")
    # 扫描层会给出 tplink / h3c / synology 这类「更细的厂商协议」用于展示，
    # 它们没有独立适配器，必须能经别名落到通用的扒页面适配器上，否则会
    # 出现「扫得到、点开报没有适配器」。
    from smart_home.adapters import get_adapter_class
    from smart_home.manager import _ADAPTER_ALIASES

    def resolvable(proto: str) -> bool:
        return (
            get_adapter_class(proto) is not None
            or get_adapter_class(_ADAPTER_ALIASES.get(proto, "")) is not None
        )

    fingerprints = [
        "tplink", "h3c", "openwrt", "mirouter", "synology", "qnap", "truenas",
        "hisense_tv", "tcl_tv", "shelly", "tasmota", "wled", "esphome", "yeelight",
        "hue", "lifx", "kasa", "tuya", "miio", "mihome", "onvif", "dlna", "cast",
        "samsung_tv", "lg_tv", "vizio_tv", "ble", "router", "nas", "onu", "http",
    ]
    unresolved = [p for p in fingerprints if not resolvable(p)]
    _check("全部协议可适配", not unresolved, f"未适配：{unresolved}")

    print("[2] 扫描结构与设备详情")
    fake = FakeScanned(
        ip="192.168.1.50", mac="A42B58C1D2E3", vendor="TP-Link",
        ports=[80, 55443], protocol="fake", category="light", name="客厅灯",
        model="fake.lamp.v1", confidence=0.9, source="arp",
        http_title="", http_server="", fields={},
    )
    device = manager._build_device(fake)
    manager._scan = {"devices": [device]}
    manager._scan_at = 0
    _check("设备 id", device["id"] == "fake:192.168.1.50", device["id"])
    _check("类别中文", device["category_zh"] == "灯具", device["category_zh"])

    detail = await manager.device("fake:192.168.1.50")
    _check("详情有控制项", len(detail["controls"]) == 2, str(detail["controls"]))
    _check("详情有当前值", detail["values"].get("power") is True)

    print("[3] 统一控制")
    out = await manager.control("fake:192.168.1.50", "power", state=False)
    _check("开关关闭", out["on"] is False)
    adapter = manager._adapter_for(device)
    _check("写入被记录", ("power", False) in adapter.written, str(adapter.written))

    out = await manager.control("fake:192.168.1.50", "write", name="brightness", value=80)
    _check("写属性", out["value"] == 80)
    out = await manager.control("fake:192.168.1.50", "action", name="clean")
    _check("执行动作", out["action"] == "clean")

    print("[4] 场景")
    scene = manager.scene_save({
        "name": "回家模式",
        "actions": [
            {"device": "fake:192.168.1.50", "mode": "power", "state": True},
            {"device": "fake:192.168.1.50", "mode": "write", "name": "brightness", "value": 70},
        ],
    })
    _check("场景保存", bool(scene.get("id")))
    _check("场景列表", len(manager.scenes_list()) == 1)
    run = await manager.scene_run(scene["id"])
    _check("场景执行成功", run["success"] == 2 and run["total"] == 2, str(run))
    _check("场景动作生效", ("power", True) in adapter.written)
    _check("场景删除", manager.scene_delete(scene["id"]) is True)
    _check("删除后为空", len(manager.scenes_list()) == 0)

    print("[5] 目录输出")
    cat = manager.catalog()
    _check("目录有类别", len(cat["categories"]) > 30, str(len(cat["categories"])))
    _check("目录有能力项", len(cat["capabilities"]) > 20)
    _check("目录有枚举", "power" in cat["enums"])

    print("[6] 错误处理")
    try:
        await manager.control("nope", "power")
        _check("未知设备报错", False)
    except ValueError:
        _check("未知设备报错", True)
    try:
        await manager.control("fake:192.168.1.50", "write", name="color")
        _check("不支持的写报错", False)
    except ValueError:
        _check("不支持的写报错", True)

    await manager.close()
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
