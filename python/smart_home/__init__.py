# SPDX-License-Identifier: GPL-3.0-or-later
"""dsh-mihome 全屋智能家居扩展（Python SDK）。

在原米家（Mi Home）云端控制之上，增加**局域网发现 + 多协议控制**能力：
扫描本网段，识别路由器、交换机、光猫、NAS、电视、扫地机器人、灯具、开关、
插座、空调、冰箱、洗衣机、烘干机、传感器等设备，统一成一套可读写的控制面。

模块划分：
- ``catalog``   ：设备类别 / 能力项的中英对照字典（翻译基准）。
- ``util``      ：网络与 HTTP 工具。
- ``discovery`` ：局域网扫描（ARP/ping/端口/SSDP/mDNS）与设备指纹识别。
- ``adapters``  ：各协议的读写实现。
- ``manager``   ：统一设备模型、状态刷新、控制分发、扫描结果缓存。

设计原则：
- 每个适配器只负责「探测 + 读状态 + 写控制」，不关心 UI。
- 控制写操作只走显式声明的映射，不做无根据的猜测；拿不到的就如实报不支持。
- 所有能力项的中文名统一由 ``catalog`` 提供，避免同一能力多套叫法。
"""

from .manager import SmartHomeManager  # noqa: F401

__all__ = ["SmartHomeManager"]
__version__ = "2.0.0"
