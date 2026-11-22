# SPDX-License-Identifier: GPL-3.0-or-later
"""网络与 HTTP 工具。

尽量只依赖标准库：扫描和控制是插件的核心路径，不希望再引入一个重量级
依赖。``requests`` 是米家桥接本来就有依赖，这里复用它，但所有调用都
显式传短超时，避免网络异常把桥接进程拖死。
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import subprocess
from typing import Iterable

# 默认探针端口：覆盖常见智能家居与网络设备。顺序越靠前越常见，扫描时
# 命中即停，减少对每台设备的尝试次数。
DEFAULT_PORTS = [
    55443,  # Yeelight / 米家灯
    54321,  # 小米 miIO
    6666, 6667, 7000,  # 涂鸦 / AirPlay
    9999,  # TP-Link Kasa
    1883,  # MQTT
    6053,  # ESPHome
    8123,  # Home Assistant
    8080, 8081, 8000, 8009, 5000, 5001,
    80, 443,
]

DEFAULT_PROBE_TIMEOUT = 0.5


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    """跑外部命令拿文本输出，失败返回空串。

    扫描依赖 ``arp`` / ``ping``，它们在 Windows 与 Linux 上参数不同，
    这里做一次平台判断；命令不存在或超时都不该让整次扫描失败。
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


# ------------------------------------------------------------------ 地址工具


def own_ipv4_addresses() -> list[str]:
    """本机所有 IPv4 地址（去重、跳过回环与链路本地）。

    不依赖 psutil：先用 UDP socket 拿主地址，再解析 ``ipconfig`` 补齐多网卡
    （有线 / 无线 / Docker 网桥），这样多网卡机器上的设备不会被漏掉。
    """
    found: set[str] = set()

    # 主地址：向公网地址「connect」一个 UDP socket 不会真的发包，只是让
    # 内核选出源地址，代价极低。
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            found.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass

    # 多网卡：Windows 用 ipconfig，Linux 用 ip addr。
    text = _run(["ipconfig"]) if os.name == "nt" else _run(["ip", "-4", "addr"])
    # 只解析接口地址字段；整段 IPv4 正则会把 DNS、网关、掩码和广播地址
    # 也当成本机接口，进而把不相关的网段加入扫描范围。IPv4 标签不受
    # Windows 的 Address / 地址本地化影响，inet 则对应 ip -4 addr。
    pattern = r"(?im)^\s*(?:IPv4[^:\r\n]*[:：]\s*|inet\s+)(\d{1,3}(?:\.\d{1,3}){3})(?:/\d+)?"
    found.update(match.group(1) for match in re.finditer(pattern, text))
    addresses: set[str] = set()
    for ip in found:
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if (addr.is_loopback or addr.is_link_local or addr.is_unspecified
                or addr.is_multicast or int(addr) == 0xFFFFFFFF):
            continue
        addresses.add(str(addr))
    return sorted(addresses, key=_ip_key)


def _ip_key(ip: str) -> tuple[int, ...]:
    return tuple(int(x) for x in ip.split("."))


def subnet_for(ip: str, prefix: int = 24) -> str:
    try:
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError:
        return ""
    return str(net)


def local_subnets(prefix: int = 24, extra: Iterable[str] = ()) -> list[str]:
    """本机所在子网列表。

    扫描范围默认是每个本机网卡的 /24；用户也可以在配置里追加子网（比如
    设备在 /16 网段时，按 /24 一个个扫太慢，可以显式加 ``192.168.0.0/16``）。
    """
    nets: set[str] = set()
    for ip in own_ipv4_addresses():
        net = subnet_for(ip, prefix)
        if net:
            nets.add(net)
    for item in extra:
        item = str(item).strip()
        if item and "/" in item:
            nets.add(item)
    return sorted(nets)


def ip_in_subnets(ip: str, subnets: Iterable[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in subnets:
        try:
            if addr in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


def is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


# ------------------------------------------------------------------ 存活探测


def arp_table() -> dict[str, str]:
    """ARP 表 -> {ip: mac}。

    ARP 是最快的存活判断：不需要逐主机发包，系统里已有的表项直接拿来用。
    扫描前先读它，能覆盖大部分常在线设备。
    """
    text = _run(["arp", "-a"]) if os.name == "nt" else _run(["arp", "-an"])
    table: dict[str, str] = {}
    for line in text.splitlines():
        match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
        if not match:
            continue
        mac = re.search(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", line)
        table[match.group(1)] = (mac.group(0).replace(":", "").replace("-", "").lower() if mac else "")
    return table


def ping(ip: str, timeout: float = 0.6) -> bool:
    """单主机存活探测。失败返回 False，不抛异常。"""
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip]
    out = _run(cmd, timeout=timeout + 2.0)
    # Windows 成功时输出 "TTL="，Linux 成功时输出 "bytes from"
    return bool(re.search(r"TTL=|bytes from", out))


async def tcp_open(ip: str, port: int, timeout: float = DEFAULT_PROBE_TIMEOUT) -> bool:
    """异步 TCP 连接探测。超时即视为未开放。"""
    try:
        fut = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def scan_ports(
    ip: str,
    ports: Iterable[int] = DEFAULT_PORTS,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    concurrency: int = 16,
) -> list[int]:
    """探测一台主机开放的端口。并发受限，避免把本机网卡打满。"""
    ports = list(ports)
    if not ports:
        return []
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(port: int) -> int | None:
        async with sem:
            if await tcp_open(ip, port, timeout):
                return port
        return None

    results = await asyncio.gather(*(one(p) for p in ports))
    return [p for p in results if p is not None]


def ping_sweep(ips: Iterable[str], timeout: float = 0.6, concurrency: int = 64) -> set[str]:
    """并行 ping 网段内主机。只用于没有 ARP 表项时的兜底。"""
    import concurrent.futures

    alive: set[str] = set()
    ips = list(ips)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(ping, ip, timeout): ip for ip in ips}
        for fut in concurrent.futures.as_completed(futures):
            if fut.result():
                alive.add(futures[fut])
    return alive


# ------------------------------------------------------------------ HTTP


def http_get(url: str, timeout: float = 2.5, headers: dict | None = None, allow_redirects: bool = True):
    """同步 GET。失败返回 None（调用方按「不可达」处理，不抛）。"""
    import requests

    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "dsh-mihome/2.0", **(headers or {})},
            allow_redirects=allow_redirects,
        )
        return resp
    except Exception:  # noqa: BLE001 - 网络设备五花八门，任何异常都按不可达
        return None


def http_post(url: str, data=None, json_body=None, timeout: float = 3.0, headers: dict | None = None):
    """同步 POST。失败返回 None。"""
    import requests

    try:
        resp = requests.post(
            url,
            data=data,
            json=json_body,
            timeout=timeout,
            headers={"User-Agent": "dsh-mihome/2.0", **(headers or {})},
        )
        return resp
    except Exception:  # noqa: BLE001
        return None


async def http_get_async(url: str, timeout: float = 2.5, headers: dict | None = None):
    """把同步 GET 放到线程里，供异步扫描使用，避免阻塞事件循环。"""
    return await asyncio.to_thread(http_get, url, timeout, headers)


async def http_post_async(url: str, data=None, json_body=None, timeout: float = 3.0, headers: dict | None = None):
    return await asyncio.to_thread(http_post, url, data, json_body, timeout, headers)


def safe_text(value: object, limit: int = 4000) -> str:
    """把任意响应内容压成可读文本，截断超长内容。"""
    if value is None:
        return ""
    if hasattr(value, "text"):
        text = value.text or ""
    elif isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = text.strip()
    if len(text) > limit:
        text = text[:limit] + f"…(截断，共 {len(text)} 字符)"
    return text


def strip_html(text: str) -> str:
    """粗略去 HTML 标签，用于把管理页面正文转成可读文本。"""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def mac_vendor(mac: str) -> str:
    """返回全球单播 MAC 的 IEEE MA-L 注册组织；未知或本地地址返回空串。

    OUI 只能说明地址块的注册组织，不能证明设备品牌、类别或控制协议。
    支持完整的冒号、连字符、Cisco 点分和 12 位十六进制格式。
    """
    from .oui_table import OUI_TABLE

    raw = str(mac or "").strip()
    if not any(re.fullmatch(pattern, raw) for pattern in (
        r"[0-9A-Fa-f]{12}",
        r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}",
        r"(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}",
        r"(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}",
    )):
        return ""
    compact = re.sub(r"[:.\-]", "", raw).upper()
    # I/G 与 U/L 位分别排除组播及随机/本地管理地址；全零也不是设备身份。
    if int(compact[:2], 16) & 0x03 or int(compact, 16) == 0:
        return ""
    return OUI_TABLE.get(compact[:6], "")
