# SPDX-License-Identifier: GPL-3.0-or-later
"""蓝牙（BLE）适配器。

Wi-Fi 不是唯一的发现手段：智能手表、部分灯具、门锁、传感器走蓝牙。
这类设备的控制协议高度私有，插件**只做识别与状态展示**，不做猜测；
需要控制时，由用户提供设备的 GATT 特征（在配置里声明）后再扩展。

依赖 ``bleak``（可选）。未安装时扫描返回空，不影响其它协议。
"""

from __future__ import annotations

from .. import catalog
from .base import BaseAdapter, register


async def ble_scan(timeout: float = 4.0) -> list[dict]:
    """扫描附近的 BLE 设备。

    返回 ``[{"ip"/"address", "name", "mac", "rssi", "uuids"}]``。
    没有 ``bleak`` 或平台不支持时返回空列表。
    """
    try:
        from bleak import BleakScanner  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    try:
        devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[smart-home] BLE 扫描失败：{exc}")
        return []
    if isinstance(devices, dict):
        entries = devices.values()
    else:
        entries = devices or []
    for entry in entries:
        try:
            if isinstance(entry, (tuple, list)) and len(entry) == 2:
                device, adv = entry
            else:
                device, adv = entry, None
            name = (
                getattr(adv, "local_name", "") if adv is not None else ""
            ) or getattr(device, "name", "") or ""
            address = str(getattr(device, "address", "") or "")
            rssi = (getattr(adv, "rssi", None) if adv is not None else None) or 0
            uuids = (
                getattr(adv, "service_uuids", None) if adv is not None else None
            ) or getattr(device, "uuids", []) or []
            out.append({
                "address": address,
                "mac": address,
                "name": name,
                "rssi": rssi,
                "uuids": list(uuids),
                "protocol": "ble",
                "category": catalog.guess_category(name, address),
                "confidence": 0.6,
                "source": "ble",
            })
        except Exception:  # noqa: BLE001
            continue
    return out


@register
class BleAdapter(BaseAdapter):
    """蓝牙设备：识别与状态展示。"""

    protocol = "ble"
    platform = ""

    def __init__(self, device, config=None):
        super().__init__(device, config)
        # BLE 用地址而非 IP
        self.address = getattr(device, "ip", "") or getattr(device, "mac", "")

    def device_id(self) -> str:
        return f"{self.protocol}:{self.address}"

    async def list_capabilities(self) -> list[dict]:
        return [
            {"key": "status", "name": "device_info", "zh": catalog.capability_zh("status"),
             "en": catalog.capability_en("status"), "kind": "bool", "writable": False},
        ]

    async def read_capability(self, capability: str):
        if capability == "status":
            return await self.scrape()
        return None

    async def scrape(self) -> dict:
        return {
            "protocol": "ble",
            "name": getattr(self.device, "name", ""),
            "mac": self.address,
            "rssi": getattr(self.device, "fields", {}).get("rssi"),
            "note": "蓝牙设备的控制协议私有，插件暂只做识别与状态展示",
        }
