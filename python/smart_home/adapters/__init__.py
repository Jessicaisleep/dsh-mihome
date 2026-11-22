# SPDX-License-Identifier: GPL-3.0-or-later
"""各协议适配器。

导入本包即完成全部适配器的注册（见 ``base.register``）。新增协议时在
这里加一行 import，并给新适配器类加上 ``@register``。
"""

from .base import BaseAdapter, get_adapter_class, register, all_protocols  # noqa: F401
from .mihome import MiHomeCloudAdapter, MiIOAdapter  # noqa: F401
from .tuya import TuyaAdapter  # noqa: F401
from .shelly import ShellyAdapter, TasmotaAdapter, WledAdapter, EspHomeAdapter  # noqa: F401
from .kasa import KasaAdapter, YeelightAdapter  # noqa: F401
from .hue import HueAdapter, LifxAdapter  # noqa: F401
from .network import RouterAdapter, SwitchAdapter, OnuAdapter, NasAdapter, HttpGenericAdapter  # noqa: F401
from .dlna import DlnaAdapter, OnvifAdapter, CastAdapter  # noqa: F401
from .tv import SamsungTvAdapter, LgTvAdapter, VizioTvAdapter  # noqa: F401
from .ble import BleAdapter, ble_scan  # noqa: F401
from .home_assistant import HomeAssistantAdapter  # noqa: F401

__all__ = [
    "BaseAdapter",
    "register",
    "get_adapter_class",
    "all_protocols",
    "MiHomeCloudAdapter",
    "MiIOAdapter",
    "TuyaAdapter",
    "ShellyAdapter",
    "TasmotaAdapter",
    "WledAdapter",
    "EspHomeAdapter",
    "KasaAdapter",
    "YeelightAdapter",
    "HueAdapter",
    "LifxAdapter",
    "RouterAdapter",
    "SwitchAdapter",
    "OnuAdapter",
    "NasAdapter",
    "HttpGenericAdapter",
    "DlnaAdapter",
    "OnvifAdapter",
    "CastAdapter",
    "SamsungTvAdapter",
    "LgTvAdapter",
    "VizioTvAdapter",
    "HomeAssistantAdapter",
    "BleAdapter",
    "ble_scan",
]
