#!/usr/bin/env python
"""回归测试：核心控制错误必须在修复前失败。仅使用假设备，不联网。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from smart_home.adapters import BaseAdapter, register
from smart_home.manager import SmartHomeManager


@register
class FailingAdapter(BaseAdapter):
    protocol = "repair-failing"
    written = []

    async def list_capabilities(self):
        return [{"key": "power", "name": "power", "kind": "bool", "writable": True}]

    async def read_capability(self, capability):
        return False

    async def write_capability(self, capability, value):
        self.written.append((capability, value))
        return {"ok": False, "error": "device rejected command"}

    async def run_action(self, name, params=None):
        return {"ok": False, "error": "action rejected"}


class Scanned:
    def __init__(self, **values):
        self.__dict__.update(values)


def make_manager():
    manager = SmartHomeManager({"data_dir": tempfile.mkdtemp(prefix="repair-test-")})
    device = manager._build_device(Scanned(
        ip="192.0.2.10", mac="AA:BB:CC:00:00:10", protocol="repair-failing",
        category="switch", name="失败设备", model="fake", ports=[], fields={},
    ))
    manager._scan = {"devices": [device]}
    return manager, device


async def main():
    manager, device = make_manager()
    device_id = device["id"]

    power = await manager.control(device_id, "power", state=True)
    assert power["ok"] is False, power

    assert FailingAdapter(manager._adapter_for(device))._value_for_capability("power", "off") is False
    assert FailingAdapter(manager._adapter_for(device))._value_for_capability("power", "on") is True
    await manager.control(device_id, "power", state="off")
    assert FailingAdapter.written[-1] == ("power", False)

    action = await manager.control(device_id, "action", name="toggle")
    assert action["ok"] is False, action

    scene_file = Path(manager.data_dir) / "smart_home" / "scenes.json"
    try:
        manager.scene_save({"name": "坏场景", "actions": [None]})
    except ValueError:
        pass
    else:
        raise AssertionError("scene_save must reject non-object actions")

    scene = manager.scene_save({
        "name": "失败场景",
        "actions": [{"device": device_id, "mode": "power", "state": True}],
    })
    try:
        manager.scene_save({"id": scene["id"], "name": scene["name"], "actions": [None]})
    except ValueError:
        pass
    else:
        raise AssertionError("scene updates must validate actions too")
    result = await manager.scene_run(scene["id"])
    assert result["ok"] is False, result
    assert result["success"] == 0, result

    assert manager._find_device("mihome:123") is None
    cloud_device = {"id": "mihome:123", "protocol": "mihome", "ip": "", "category": "other"}
    manager._scan = {"devices": [cloud_device]}
    assert manager._find_device("123") == cloud_device

    ble_a = manager._build_device(Scanned(
        ip="", mac="AA:BB:CC:00:00:11", protocol="ble", category="sensor", name="手表A", fields={}, ports=[]
    ))
    ble_b = manager._build_device(Scanned(
        ip="", mac="AA:BB:CC:00:00:12", protocol="ble", category="sensor", name="手表B", fields={}, ports=[]
    ))
    assert ble_a["id"] != ble_b["id"], (ble_a, ble_b)
    await manager.close()
    assert scene_file.exists()


if __name__ == "__main__":
    asyncio.run(main())
    print("repair tests passed")
