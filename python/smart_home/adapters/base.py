# SPDX-License-Identifier: GPL-3.0-or-other
"""适配器基类与注册表。

每个适配器负责一种协议的「读状态 + 控制」，统一输出**能力项**（capability），
上层 ``manager`` 不关心具体协议。能力项的名字来自 ``catalog.py``，保证
中文叫法一致。

适配器需要实现的接口（其余可留空，表示不支持）：

- ``list_capabilities()``：本设备支持哪些控制项，返回 ``catalog`` 里的能力项。
- ``read_capability(cap)``：读某个能力项的当前值。
- ``write_capability(cap, value)``：写某个能力项。
- ``run_action(name, params)``：执行动作（如回充、定位）。
- ``read_all()``：一次性读出所有可读值（面板首屏用）。
- ``scrape()``：管理类设备（路由器/交换机/光猫/NAS）扒设置页。
"""

from __future__ import annotations

from .. import catalog

# 所有能力项的默认取值类型
CAPABILITY_KIND = {key: info.get("kind", "bool") for key, info in catalog.CAPABILITY_LABELS.items()}


class BaseAdapter:
    """适配器基类。"""

    protocol: str = "base"
    platform: str = ""

    def __init__(self, device, config: dict | None = None):
        self.device = device
        self.config = config or {}
        self.ip = getattr(device, "ip", "") or ""
        self.mac = getattr(device, "mac", "") or ""
        self.log = print

    # ---- 可选实现 ----

    def is_supported(self) -> bool:
        return True

    async def probe(self) -> dict:
        """补一次协议级探测，返回要合并进 device.fields 的信息。"""
        return {}

    async def list_capabilities(self) -> list[dict]:
        """能力必须由具体协议确认；类别只是标签，不代表支持控制。"""
        return []

    async def read_capability(self, capability: str):
        """默认：能力项不在可读集合里，返回 None。"""
        return None

    async def write_capability(self, capability: str, value) -> dict:
        raise NotImplementedError(f"{self.protocol} 不支持写入 {capability}")

    async def run_action(self, name: str, params: dict | None = None) -> dict:
        raise NotImplementedError(f"{self.protocol} 不支持动作 {name}")

    async def read_all(self) -> dict:
        """默认：逐个读可读能力项。"""
        out: dict = {}
        for cap in await self.list_capabilities():
            key = cap.get("key")
            if cap.get("kind") == "action" or cap.get("readable") is False:
                continue
            try:
                value = await self.read_capability(key)
            except Exception as exc:  # noqa: BLE001
                self.log(f"[smart-home] 读取 {self.ip} {key} 失败：{exc}")
                continue
            if value is not None:
                out[key] = value
        return out

    async def scrape(self) -> dict:
        """管理类设备的设置页扒取，默认不支持。"""
        raise NotImplementedError(f"{self.protocol} 不支持设置页扒取")

    async def close(self) -> None:
        """释放资源（长连接等），默认无需处理。"""

    # ---- 工具 ----

    def device_id(self) -> str:
        """统一设备标识：优先用设备自带 id，否则用协议 + 地址。"""
        explicit = getattr(self.device, "did", "") or getattr(self.device, "id", "")
        if explicit:
            return str(explicit)
        return f"{self.protocol}:{self.ip}"

    def device_name(self) -> str:
        return getattr(self.device, "name", "") or self.device_id()

    def category(self) -> str:
        return getattr(self.device, "category", "other") or "other"

    def capability_info(self, capability: str) -> dict:
        return catalog.capability_info(capability)

    def _value_for_capability(self, capability: str, value):
        """把统一能力项的取值按类型归一化。"""
        info = catalog.CAPABILITY_LABELS.get(capability, {})
        kind = info.get("kind", "bool")
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and value in (0, 1):
                return bool(value)
            if isinstance(value, str):
                normalized = value.strip().casefold()
                if normalized in {"1", "true", "on", "yes", "enabled", "开", "开启", "打开"}:
                    return True
                if normalized in {"0", "false", "off", "no", "disabled", "关", "关闭"}:
                    return False
            raise ValueError(f"{capability} 需要布尔值，收到 {value!r}")
        if kind == "range":
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{capability} 需要数值，收到 {value!r}")
        return value


# ------------------------------------------------------------------ 注册表

_REGISTRY: dict[str, type[BaseAdapter]] = {}


def register(cls: type[BaseAdapter]) -> type[BaseAdapter]:
    """把适配器注册进协议名表。"""
    _REGISTRY[cls.protocol] = cls
    return cls


def get_adapter_class(protocol: str) -> type[BaseAdapter] | None:
    return _REGISTRY.get(protocol)


def all_protocols() -> list[str]:
    return sorted(_REGISTRY)
