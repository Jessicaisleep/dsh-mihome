# SPDX-License-Identifier: GPL-3.0-or-later
"""米家云端代理。

``SmartHomeManager`` 不直接认识 mijiaAPI 的 ``MijiaService``——适配器只依赖
一组很窄的方法（设备列表、详情、读写属性、执行动作、电源、miio token）。
把这个适配层放在这里，好处是：

- 管理器 / 适配器可以脱离米家独立测试（传任意对象即可）；
- 桥接进程只把 ``MijiaService`` 包一层交给管理器，不把云细节泄漏到上层。
"""

from __future__ import annotations

from typing import Any


class MiHomeCloudProxy:
    """把 ``MijiaService`` 适配成管理器/适配器需要的方法集。"""

    def __init__(self, service: Any):
        self._svc = service

    # ------------------------------------------------------------ 设备

    def list_devices(self) -> list[dict]:
        """云端设备列表（统一成 dict，避免 dataclass 泄漏到 JSON）。"""
        return [self._plain(item) for item in (self._svc.list_devices() or [])]

    def device_detail(self, did: str) -> dict:
        return self._plain(self._svc.device_detail(did))

    # ------------------------------------------------------------ 属性

    def read_props(self, did: str, names: list[str]) -> dict:
        return self._svc.read_props(did, list(names)) or {}

    def write_prop(self, did: str, name: str, value) -> None:
        return self._svc.write_prop(did, name, value)

    # ------------------------------------------------------------ 动作

    def run_action(self, did: str, name: str, params=None) -> None:
        return self._svc.run_action(did, name, params)

    # ------------------------------------------------------------ 电源

    def power_state(self, did: str):
        return self._svc.power_state(did)

    def set_power_state(self, did: str, state: bool) -> None:
        return self._svc.set_power_state(did, bool(state))

    # ------------------------------------------------------------ 局域网

    def miio_token(self, ip: str, mac: str = "") -> str:
        """取 miIO 设备令牌（本地控制需要）。取不到返回空串。"""
        getter = getattr(self._svc, "miio_token", None)
        if callable(getter):
            try:
                return str(getter(ip, mac) or "")
            except Exception:  # noqa: BLE001
                return ""
        return ""

    # ------------------------------------------------------------ 工具

    @staticmethod
    def _plain(item):
        """把 dataclass / 嵌套结构转成纯 JSON 可序列化结构。

        必须递归：``MijiaService.device_detail`` 返回的 ``DeviceDetail`` 里
        ``props`` 是 ``PropInfo`` 列表，只取顶层 ``__dict__`` 会把
        ``PropInfo`` 原样留在里面，后续 ``localize`` 拿到对象就会崩。
        """
        from dataclasses import asdict, is_dataclass

        if is_dataclass(item) and not isinstance(item, type):
            return {k: MiHomeCloudProxy._plain(v) for k, v in asdict(item).items()}
        if isinstance(item, dict):
            return {str(k): MiHomeCloudProxy._plain(v) for k, v in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [MiHomeCloudProxy._plain(v) for v in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return str(item)
