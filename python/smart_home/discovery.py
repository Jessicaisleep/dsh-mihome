# SPDX-License-Identifier: GPL-3.0-or-later
"""局域网发现：扫描网段、识别设备、给出协议与类别。

扫描分三步，每步都设计成「失败不致命」：

1. **候选主机**：先读 ARP 表拿到已缓存的主机，再按需要做 ping 扫描补齐。
   ARP 表是最快的一条路径，绝大多数常在线设备都会在表里。
2. **端口探测**：对候选主机并发探测一组常见端口。
3. **指纹识别**：对开放端口做协议级探测（HTTP 页面、SSDP、mDNS、miIO
   hello、涂鸦 UDP 等），输出 ``protocol`` / ``category`` / ``name`` /
   ``model``，交给 ``manager`` 建适配器。

识别不到的设备仍然会被保留（协议为 ``unknown``），只是不能控制——
宁可把设备列出来让用户看到，也不要悄悄漏掉。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
from dataclasses import dataclass, field

from . import catalog, util

# ------------------------------------------------------------------ 数据结构


@dataclass
class ProbeResult:
    """一次指纹识别的结论。"""

    protocol: str
    category: str
    name: str = ""
    model: str = ""
    confidence: float = 0.5
    fields: dict = field(default_factory=dict)


_SENSITIVE_FIELD_RE = re.compile(
    r"(?:^|[_-])(local[_-]?key|password|passwd|token|secret|credential|api[_-]?key|private[_-]?key|auth)(?:$|[_-])",
    re.I,
)


def _public_value(value):
    if isinstance(value, dict):
        return {
            str(key): _public_value(item)
            for key, item in value.items()
            if not _SENSITIVE_FIELD_RE.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return value


@dataclass
class ScannedDevice:
    """扫描得到的一台设备（尚未建适配器）。"""

    ip: str
    address: str = ""
    mac: str = ""
    vendor: str = ""
    ports: list[int] = field(default_factory=list)
    protocol: str = "unknown"
    category: str = "other"
    name: str = ""
    model: str = ""
    confidence: float = 0.0
    source: str = ""
    http_title: str = ""
    http_server: str = ""
    requires_credentials: bool = False
    fields: dict = field(default_factory=dict)
    credentials: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        """序列化可公开设备信息；credential 永不进入扫描结果。"""
        return {
            "ip": self.ip,
            "address": self.address,
            "mac": self.mac,
            "vendor": self.vendor,
            "ports": list(self.ports),
            "protocol": self.protocol,
            "category": self.category,
            "name": self.name,
            "model": self.model,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "http_title": self.http_title,
            "http_server": self.http_server,
            "requires_credentials": bool(self.requires_credentials),
            "fields": _public_value(self.fields),
        }


# ------------------------------------------------------------------ 基础探测


async def fetch_http(ip: str, port: int, use_tls: bool = False, path: str = "/", timeout: float = 2.5):
    """抓一次 HTTP 响应，返回 ``(title, server, text, final_url)``；失败返回 None。"""
    scheme = "https" if use_tls else "http"
    resp = await util.http_get_async(f"{scheme}://{ip}:{port}{path}", timeout=timeout)
    if resp is None:
        return None
    try:
        text = resp.text or ""
    except Exception:  # noqa: BLE001
        text = resp.content.decode("utf-8", errors="replace")
    title = ""
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    if match:
        title = util.strip_html(match.group(1))[:120]
    server = resp.headers.get("Server", "") if resp.headers is not None else ""
    return title, server, text[:8000], f"{scheme}://{ip}:{port}{path}"


def fingerprint_http(
    title: str,
    server: str,
    text: str,
    ip: str,
    port: int,
) -> ProbeResult | None:
    """从 HTTP 首页文本识别设备。

    注意：识别顺序很重要。像「Tasmota」「WLED」「Shelly」这类固件会在页面
    里写明自己，先看它们，再退回通用的「管理页面」判断，避免把
    Tasmota 设备误判成普通路由器。
    """
    haystack = f"{title} {server} {text}".lower()

    # ---- 智能家居固件（优先，特征最明确）------------------------
    if "tasmota" in haystack or "tasmota" in title.lower():
        model = _first(text, r"Tasmota\s+([0-9][0-9.]*)", r"Device:\s*([^\n<]+)")
        return ProbeResult("tasmota", catalog.guess_category(title, text), name=title,
                           model=model, confidence=0.95, fields={"http_title": title, "http_server": server})
    if "wled" in haystack:
        model = _first(text, r'"ver"\s*:\s*"([^"]+)"')
        return ProbeResult("wled", catalog.guess_category(title, text), name=title,
                           model=model, confidence=0.95, fields={"http_title": title})
    if "shelly" in haystack or "shelly" in title.lower():
        model = _first(text, r'"model"\s*:\s*"([^"]+)"', r'"device"\s*:\s*"([^"]+)"')
        return ProbeResult("shelly", catalog.guess_category(title, text), name=title,
                           model=model, confidence=0.95, fields={"http_title": title})
    if "esphome" in haystack:
        model = _first(text, r'"model"\s*:\s*"([^"]+)"', r'"name"\s*:\s*"([^"]+)"')
        return ProbeResult("esphome", catalog.guess_category(title, text), name=title,
                           model=model, confidence=0.9, fields={"http_title": title})
    if "yeelight" in haystack or "yeelink" in haystack:
        model = _first(text, r'"model"\s*:\s*"([^"]+)"', r'"id"\s*:\s*"([^"]+)"')
        return ProbeResult("yeelight", catalog.guess_category(title, text), name=title,
                           model=model, confidence=0.95, fields={"http_title": title})
    if "home assistant" in haystack:
        return ProbeResult("home_assistant", "gateway", name=title,
                           model="home-assistant", confidence=0.95, fields={"http_title": title})
    if "hue bridge" in haystack or "philips hue" in haystack:
        return ProbeResult("hue", "gateway", name=title, model="hue-bridge",
                           confidence=0.95, fields={"http_title": title})
    if "onvif" in haystack or "/onvif/" in text.lower():
        return ProbeResult("onvif", catalog.guess_category(title, text), name=title,
                           model="onvif", confidence=0.85, fields={"http_title": title})

    # ---- 电视 / 投影 ----------------------------------------------
    if "samsung" in haystack or "tizen" in haystack or "samsung" in server.lower():
        return ProbeResult("samsung_tv", "tv", name=title, model="samsung-tv",
                           confidence=0.9, fields={"http_title": title, "http_server": server})
    if "lg webos" in haystack or "webos" in haystack:
        return ProbeResult("lg_tv", "tv", name=title, model="lg-tv",
                           confidence=0.9, fields={"http_title": title})
    if "vizio" in haystack:
        return ProbeResult("vizio_tv", "tv", name=title, model="vizio-tv",
                           confidence=0.9, fields={"http_title": title})
    if "tcl" in haystack and "tv" in haystack:
        return ProbeResult("tcl_tv", "tv", name=title, model="tcl-tv",
                           confidence=0.75, fields={"http_title": title})
    if "hisense" in haystack or "海信" in title:
        return ProbeResult("hisense_tv", "tv", name=title, model="hisense-tv",
                           confidence=0.75, fields={"http_title": title})

    # ---- 网络设备 -------------------------------------------------
    # 顺序很重要：先认「厂商 + 品类都很明确」的条目（NAS / OpenWrt / H3C /
    # TP-Link / 小米路由），再退到「可能的光猫」这种弱判断。光猫只按
    # GPON/EPON 这类协议词或页面明确自称 ONT 来认，不能因为标题里有个
    # 「光纤」就把路由器当成光猫。
    if "synology" in haystack or "dsm" in haystack:
        return ProbeResult("synology", "nas", name=title, model="synology",
                           confidence=0.9, fields={"http_title": title})
    if "qnap" in haystack:
        return ProbeResult("qnap", "nas", name=title, model="qnap",
                           confidence=0.9, fields={"http_title": title})
    if "truenas" in haystack:
        return ProbeResult("truenas", "nas", name=title, model="truenas",
                           confidence=0.9, fields={"http_title": title})
    if "openwrt" in haystack or "luci" in haystack:
        return ProbeResult("openwrt", "router", name=title, model="openwrt",
                           confidence=0.85, fields={"http_title": title})
    if "h3c" in haystack or "comware" in haystack:
        return ProbeResult("h3c", "net_switch", name=title, model="h3c",
                           confidence=0.8, fields={"http_title": title})
    if _looks_like_tplink(title, haystack):
        return ProbeResult("tplink", _router_or_light(haystack), name=title, model="tp-link",
                           confidence=0.85, fields={"http_title": title})
    if "miwifi" in haystack or "小米路由器" in title or "xiaomi router" in haystack:
        return ProbeResult("mirouter", "router", name=title, model="mi-router",
                           confidence=0.85, fields={"http_title": title})
    if "huawei" in haystack or "华为" in title or "epon" in haystack or "gpon" in haystack:
        # 华为 + GPON/EPON 才像光猫；单说华为更可能是路由器/交换机
        category = "onu" if ("epon" in haystack or "gpon" in haystack or "光猫" in title) else _router_or_light(haystack)
        return ProbeResult("onu", category, name=title, model="huawei",
                           confidence=0.75, fields={"http_title": title})
    if "zte" in haystack or "中兴" in title:
        category = "onu" if ("epon" in haystack or "gpon" in haystack or "光猫" in title) else _router_or_light(haystack)
        return ProbeResult("onu", category, name=title, model="zte",
                           confidence=0.7, fields={"http_title": title})

    # ---- 兜底：能开 HTTP 管理页 ---------------------------------
    if title or server:
        category = catalog.guess_category(title, text, server)
        # 标题里没认出品类的管理页，按页面里的路由/光猫/交换机词做一次
        # 弱判断：家庭网段里能开 HTTP 管理页、又不是已知固件的，绝大多数
        # 是网络设备。判不出来才退回 other。
        if category == "other":
            guessed = _router_or_light(haystack)
            if guessed != "router" or _has_network_words(haystack):
                category = guessed
        return ProbeResult("http", category, name=title or f"{ip}:{port}",
                           model=server or "", confidence=0.4,
                           fields={"http_title": title, "http_server": server})
    return None


def _has_network_words(haystack: str) -> bool:
    """页面里有没有管理界面的网络词（用来确认「这是个网络设备管理页」）。"""
    return any(_word_hit(haystack, w) for w in _ROUTER_WORDS + _ONU_WORDS + _SWITCH_WORDS)


def _first(text: str, *patterns: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()[:120]
    return ""


# 路由器管理页里常见的词。厂商前缀能认出牌子，但「路由器 / 光猫 / 交换机」
# 还得看页面是不是这套 Web 管理界面；TP-Link / 华为 / 中兴都同时做路由器和
# 光猫，不能只凭厂商名定品类。
_ROUTER_WORDS = (
    "archer", "易展", "router", "wireless", "wifi", "wlan", "lan", "wan",
    "dhcp", "ssid", "无线", "路由", "上网", "宽带",
)
_ONU_WORDS = ("gpon", "epon", "ont", "光猫", "光调制", "光纤入户", "宽带接入")
_SWITCH_WORDS = ("switch", "vlan", "trunk", "交换机", "端口镜像", "生成树")


def _looks_like_tplink(title: str, haystack: str) -> bool:
    """认得 TP-Link 的三种写法：品牌名、``TL-`` 型号前缀、``tplink``。

    TP-Link 的 Web 登录页标题往往只有型号（``TL-WDA6332RE``、
    ``TL-7DR7230易展版``），页面里反而不出现品牌名——只看 "tp-link"
    会把它们漏成未知设备。
    """
    lowered = (title or "").lower()
    return (
        "tp-link" in haystack
        or "tplink" in haystack
        or "tp-link" in lowered
        or re.match(r"^\s*tl-[a-z0-9]", lowered) is not None
    )


def _word_hit(haystack: str, word: str) -> bool:
    """关键词命中：ASCII 词按词边界，中文词按包含。

    与 ``catalog.guess_category`` 同一规则。这里必须一致——``ont``（光猫
    缩写）若用裸包含判断，会命中 HTML 里的 ``content=``，把路由器判成光猫。
    """
    token = (word or "").lower()
    if not token:
        return False
    if token.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack) is not None
    return token in haystack


def _router_or_light(haystack: str) -> str:
    """在「路由器 / 光猫 / 交换机」之间做一次弱判断。

    只在厂商已经确定、但品类还不清楚时调用。命中数打平或一个都没命中
    时按「路由器」处理——家庭网段里 HTTP 管理页设备最常见的确实是路由器，
    但这只是**默认值**，不是断言：UI 上仍会显示协议与页面标题，用户能自己
    判断。
    """
    def score(words) -> int:
        return sum(1 for w in words if _word_hit(haystack, w))

    onu = score(_ONU_WORDS)
    switch = score(_SWITCH_WORDS)
    router = score(_ROUTER_WORDS)
    if onu > max(router, switch):
        return "onu"
    if switch > max(router, onu):
        return "net_switch"
    return "router"


# ------------------------------------------------------------------ SSDP / mDNS

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900


async def ssdp_discover(timeout: float = 2.0) -> list[dict]:
    """用 UDP 组播做一次通用 SSDP 发现（DLNA / AirPlay / Chromecast / UPnP）。"""
    import socket

    targets = [
        "ssdp:all",
        "urn:dial-multiscreen-org:service:dial:1",
        "urn:schemas-upnp-org:device:MediaServer:1",
        "urn:schemas-upnp-org:device:MediaRenderer:1",
    ]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    except OSError:
        pass
    out: list[dict] = []
    try:
        sock.bind(("", 0))
        for st in targets:
            query = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                "MAN: \"ssdp:discover\"\r\n"
                "MX: 1\r\n"
                f"ST: {st}\r\n\r\n"
            )
            sock.sendto(query.encode(), (SSDP_ADDR, SSDP_PORT))
        deadline = time.time() + timeout
        sock.settimeout(max(0.1, deadline - time.time()))
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            text = data.decode("utf-8", errors="replace")
            location = ""
            match = re.search(r"LOCATION:\s*(\S+)", text, re.I)
            if match:
                location = match.group(1)
            server = ""
            match = re.search(r"SERVER:\s*([^\r\n]+)", text, re.I)
            if match:
                server = match.group(1)
            out.append({
                "ip": addr[0],
                "location": location,
                "server": server,
                "raw": text[:500],
            })
    except OSError:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return out


async def mdns_discover(timeout: float = 2.5) -> list[dict]:
    """用 zeroconf 的异步浏览器发现并解析 mDNS 服务。

    zeroconf 的 ``async_get_service_info`` 只能解析一个完整的服务名，不能
    把服务类型本身当成可迭代结果。先用 ``AsyncServiceBrowser`` 收集服务名，
    再逐个解析地址和 TXT 属性；解析失败只跳过该条服务。
    """
    try:
        from zeroconf import IPVersion  # type: ignore
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf  # type: ignore
    except Exception:  # noqa: BLE001
        return []

    wanted = (
        "_googlecast._tcp.local.",
        "_airplay._tcp.local.",
        "_matter._tcp.local.",
        "_hap._tcp.local.",
        "_sop._tcp.local.",
        "_home-assistant._tcp.local.",
    )

    class Listener:
        def __init__(self, service_type: str):
            self.service_type = service_type
            self.names: set[str] = set()

        def add_service(self, _zeroconf, _type, name):
            self.names.add(str(name))

        def update_service(self, _zeroconf, _type, name):
            self.names.add(str(name))

        def remove_service(self, _zeroconf, _type, name):
            self.names.discard(str(name))

    try:
        azc = AsyncZeroconf(ip_version=IPVersion.V4Only)
    except Exception:  # noqa: BLE001
        return []

    browsers = []
    listeners: list[Listener] = []
    loop = asyncio.get_running_loop()
    budget = max(0.0, float(timeout))
    deadline = loop.time() + budget
    try:
        for service in wanted:
            listener = Listener(service)
            listeners.append(listener)
            browsers.append(AsyncServiceBrowser(azc.zeroconf, service, listener=listener))
        await asyncio.sleep(budget * 0.5)

        found: list[dict] = []
        for listener in listeners:
            for name in sorted(listener.names):
                remaining_ms = int((deadline - loop.time()) * 1000)
                if remaining_ms <= 0:
                    break
                try:
                    info = await azc.async_get_service_info(
                        listener.service_type, name, timeout=remaining_ms
                    )
                except Exception:  # noqa: BLE001
                    continue
                if info is None:
                    continue
                try:
                    addresses = list(info.parsed_addresses())
                except Exception:  # noqa: BLE001
                    addresses = []
                props = getattr(info, "decoded_properties", None)
                if not isinstance(props, dict):
                    props = getattr(info, "properties", {}) or {}
                normalized_props = {}
                if isinstance(props, dict):
                    for key, value in props.items():
                        key = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
                        value = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
                        normalized_props[key] = value
                model = normalized_props.get("model", normalized_props.get("md", ""))
                for ip in addresses:
                    item = {
                        "ip": str(ip).strip("[]"),
                        "service": listener.service_type.rstrip("."),
                        "name": str(getattr(info, "name", "") or name),
                        "model": str(model or ""),
                    }
                    try:
                        port = int(getattr(info, "port", 0))
                    except (TypeError, ValueError):
                        port = 0
                    if 1 <= port <= 65535:
                        item["port"] = port
                    if listener.service_type == "_home-assistant._tcp.local.":
                        # Only public identity metadata is needed. TXT URLs may
                        # contain userinfo or point outside the resolved LAN host.
                        for key in ("uuid", "version", "location_name"):
                            value = normalized_props.get(key)
                            if isinstance(value, str) and value:
                                item[key] = value
                    found.append(item)
        return found
    except Exception:  # noqa: BLE001
        return []
    finally:
        for browser in browsers:
            try:
                await browser.async_cancel()
            except Exception:  # noqa: BLE001
                pass
        try:
            await azc.async_close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ miIO hello

MIIO_HELLO = bytes.fromhex("21310020ffffffffffffffffffffffffffffffffffffffffffffffffffffffff")


async def miio_hello(ip: str, timeout: float = 1.5) -> dict:
    """发送 UDP/54321 hello，只返回公开的设备 ID 与时间戳。

    python-miio 的握手响应是 32 字节大端二进制头，不包含型号或固件 JSON。
    后 16 字节可能包含未初始化设备的 token，不能进入公开发现结果。
    https://python-miio.readthedocs.io/en/stable/_modules/miio/miioprotocol.html
    """
    import math
    import socket
    import struct

    try:
        address = ipaddress.ip_address(ip)
        budget = float(timeout)
    except (TypeError, ValueError):
        return {}
    if address.version != 4 or not math.isfinite(budget) or budget <= 0:
        return {}

    def exchange() -> dict:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(budget)
            # Connected UDP accepts responses only from this host and port.
            sock.connect((str(address), 54321))
            sock.send(MIIO_HELLO)
            data = sock.recv(512)
            if len(data) != 32:
                return {}
            magic, length, _, device_id, timestamp = struct.unpack(">HHIII", data[:16])
            if (magic != 0x2131 or length != 32
                    or device_id == 0xffffffff or timestamp == 0xffffffff):
                return {}
            return {"device_id": str(device_id), "timestamp": timestamp}
        except OSError:
            return {}
        finally:
            if sock is not None:
                sock.close()

    return await asyncio.to_thread(exchange)


# ------------------------------------------------------------------ 涂鸦发现

async def tuya_discover(timeout: float = 3.0) -> list[dict]:
    """涂鸦设备局域网发现。

    优先用 ``tinytuya``（可选依赖，支持 3.1/3.3/3.4/3.5 与本地密钥）；
    没装就退化为 UDP 广播探测端口，把设备列出来但标为「需要本地密钥」。
    """
    try:
        import tinytuya  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    results: list[dict] = []
    try:
        # tinytuya 1.21+ 用 deviceScan()（同步、阻塞 UDP 等待），没有旧版
        # Scanner 类。放线程里跑，避免卡住事件循环。
        scanner = await asyncio.to_thread(
            tinytuya.deviceScan, False, None, False, True, False
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[smart-home] 涂鸦扫描失败：{exc}")
        return []
    for dev in (scanner or {}).values():
        if not isinstance(dev, dict):
            continue
        try:
            results.append({
                "ip": dev.get("ip", ""),
                "mac": dev.get("mac", ""),
                "gw_id": dev.get("gwId", ""),
                "product_key": dev.get("productKey", ""),
                "version": dev.get("version", ""),
                "local_key": dev.get("localKey", ""),
                "dps": dev.get("dps", {}),
                "protocol": "tuya",
                "category": catalog.guess_category(
                    dev.get("productKey", ""), json.dumps(dev.get("dps", {}), ensure_ascii=False)
                ),
                "name": dev.get("name", ""),
                "confidence": 0.9,
                "source": "tuya-udp",
            })
        except Exception:  # noqa: BLE001
            continue
    return results


# ------------------------------------------------------------------ 主扫描


def _device_identity(device: ScannedDevice) -> str:
    return device.ip or f"{device.protocol}:{device.address or device.mac}"


def _device_sort_key(device: ScannedDevice) -> tuple:
    return (0, util._ip_key(device.ip)) if device.ip else (1, device.address or device.mac)


def _merge_item(target: ScannedDevice, item: dict, credentials: dict | None = None) -> None:
    """提升弱指纹并显式保存扩展字段，密钥只进入私有凭据。"""
    incoming_confidence = float(item.get("confidence") or 0)
    promote = target.protocol in ("unknown", "http", "mdns") or incoming_confidence > target.confidence
    known = set(ScannedDevice.__dataclass_fields__)
    for key, value in item.items():
        if key == "credentials":
            target.credentials.update(value or {})
        elif _SENSITIVE_FIELD_RE.search(str(key)):
            target.credentials[key] = value
        elif key == "fields":
            for name, content in (value or {}).items():
                if _SENSITIVE_FIELD_RE.search(str(name)):
                    target.credentials[name] = content
                else:
                    # mDNS supplies an HTTP fallback, while an HTTP probe may
                    # already have confirmed this endpoint requires TLS.
                    if (name == "base_url" and item.get("source") == "mdns"
                            and target.fields.get(name)):
                        continue
                    target.fields[name] = _public_value(content)
        elif key == "ports":
            target.ports = sorted(set(target.ports) | set(value or []))
        elif key == "requires_credentials":
            target.requires_credentials = target.requires_credentials or bool(value)
        elif key in ("protocol", "category"):
            if value not in (None, "", "unknown", "other") and (promote or getattr(target, key) in ("unknown", "other")):
                setattr(target, key, value)
        elif key == "confidence":
            target.confidence = max(target.confidence, incoming_confidence)
        elif key in known:
            if value and (not getattr(target, key) or (promote and key in ("name", "model", "source"))):
                setattr(target, key, value)
        elif value is not None:
            target.fields[key] = _public_value(value)
    target.credentials.update(credentials or {})
    if target.address:
        target.fields["address"] = target.address
    if target.mac and not target.vendor:
        target.vendor = util.mac_vendor(target.mac)


async def scan_subnet(
    subnet: str,
    ports: list[int] | None = None,
    port_timeout: float = 0.5,
    scan_ping: bool = True,
    concurrency: int = 24,
) -> list[ScannedDevice]:
    """扫描一个子网。

    返回按 IP 排序的设备列表。任何一步失败都只影响该步，不会让扫描中断。
    """
    subnets, _, _, ports, scan_ping = normalize_scan_options([subnet], 24, None, ports, scan_ping)
    network = ipaddress.ip_network(subnets[0])

    # 1. 候选主机：ARP 表优先，再 ping 扫描兜底
    arp = util.arp_table()
    candidates: set[str] = set()
    for ip, mac in arp.items():
        try:
            if ipaddress.ip_address(ip) in network:
                candidates.add(ip)
        except ValueError:
            continue

    if scan_ping and len(candidates) < max(8, network.num_addresses // 8) and network.num_addresses <= 258:
        addresses = [str(addr) for addr in network.hosts()]
        alive = util.ping_sweep(addresses, timeout=port_timeout + 0.1, concurrency=64)
        candidates.update(alive)

    # 广播发现（SSDP/mDNS/Tuya/BLE）独立于ARP与ping候选，不能因空候选而跳过。

    # 2. 端口探测（并发受限）
    sem = asyncio.Semaphore(max(1, concurrency))

    async def probe_host(ip: str) -> tuple[str, list[int]]:
        async with sem:
            # miIO uses UDP; a failed TCP probe on 54321 says nothing about it.
            tcp_ports = [port for port in ports if port != 54321]
            open_ports = await util.scan_ports(ip, tcp_ports, port_timeout)
        return ip, open_ports

    hosts = await asyncio.gather(*(probe_host(ip) for ip in candidates))

    # 3. 指纹识别
    async def identify_host(ip: str, open_ports: list[int]) -> ScannedDevice:
        mac = arp.get(ip, "")
        device = ScannedDevice(ip=ip, mac=mac, vendor=util.mac_vendor(mac), ports=open_ports)
        async with sem:
            await _fingerprint(device, probe_miio=54321 in ports)
        return device

    # A UDP timeout is normal on non-miIO hosts. Bound concurrency rather than
    # adding one full timeout serially for every ARP/ping candidate.
    devices = list(await asyncio.gather(*(identify_host(ip, found) for ip, found in hosts)))

    # 4. 跨协议发现补充（SSDP / mDNS / 涂鸦 / miIO）
    try:
        extra = await _extra_discovery(subnet)
    except Exception as exc:  # 单个广播发现失败不能抹掉已经探测到的主机
        print(f"[smart-home] 跨协议发现失败：{exc}")
        extra = []
    by_identity = {_device_identity(d): d for d in devices}
    for item in extra:
        ip = str(item.get("ip") or "")
        address = str(item.get("address") or "")
        if ip:
            try:
                if ipaddress.ip_address(ip) not in network:
                    continue
            except ValueError:
                continue
        elif not address:
            continue
        incoming = ScannedDevice(ip=ip, address=address)
        _merge_item(incoming, item)
        identity = _device_identity(incoming)
        if identity in by_identity:
            _merge_item(by_identity[identity], incoming.to_dict(), incoming.credentials)
        else:
            devices.append(incoming)
            by_identity[identity] = incoming
    return sorted(devices, key=_device_sort_key)


async def _fingerprint(device: ScannedDevice, probe_miio: bool = False) -> None:
    """对一台设备的开放 TCP 端口及可选 UDP miIO 服务做指纹识别。"""
    if probe_miio or 54321 in device.ports:
        info = await miio_hello(device.ip, timeout=1.2)
        if info:
            device.protocol = "miio"
            device.category = catalog.guess_category(info.get("model", ""), info.get("description", ""))
            device.model = str(info.get("model", ""))
            device.name = str(info.get("description", "")) or device.name
            device.confidence = 0.9
            device.fields["miio"] = info
            return

    # HTTP 指纹
    http_ports = [p for p in device.ports if p in (80, 443, 8080, 8081, 8000, 5000, 5001, 8123)]
    for port in http_ports:
        for tls in (False, True):
            result = await fetch_http(device.ip, port, use_tls=tls)
            if result is None:
                continue
            title, server, text, _url = result
            probe = fingerprint_http(title, server, text, device.ip, port)
            if probe and probe.confidence >= 0.4:
                device.protocol = probe.protocol
                device.category = probe.category
                device.name = probe.name or device.name
                device.model = probe.model or device.model
                device.confidence = probe.confidence
                device.http_title = title
                device.http_server = server
                device.fields["http"] = {"title": title, "server": server, "path": "/"}
                if probe.protocol == "home_assistant":
                    scheme = "https" if tls else "http"
                    device.fields.update({"port": port, "base_url": f"{scheme}://{device.ip}:{port}"})
                    device.requires_credentials = True
                return
            if not device.http_title and title:
                device.http_title = title
                device.http_server = server

    # 非 HTTP 端口但已知协议
    for port, protocol in ((6666, "tuya"), (6667, "tuya"), (7000, "airplay"), (1883, "mqtt")):
        if port in device.ports and device.protocol == "unknown":
            device.protocol = protocol
            device.category = catalog.guess_category(device.http_title)
            device.confidence = 0.5
            return

    if device.http_title or device.http_server:
        device.protocol = "http"
        device.category = catalog.guess_category(device.http_title, device.http_server)
        device.confidence = 0.35


async def _extra_discovery(subnet: str) -> list[dict]:
    """跨协议发现：SSDP、mDNS、涂鸦、以及已知端口的 miIO。"""
    out: list[dict] = []
    ssdp = await ssdp_discover(timeout=1.5)
    for item in ssdp:
        ip = item.get("ip", "")
        if not ip or not util.ip_in_subnets(ip, [subnet]):
            continue
        server = item.get("server", "")
        protocol = "dlna"
        category = "other"
        if "dial" in (server + item.get("raw", "")).lower():
            protocol = "cast"
            category = "tv"
        elif "mediaserver" in server.lower() or "mediarenderer" in server.lower():
            protocol = "dlna"
            category = catalog.guess_category(server)
        elif "airplay" in server.lower() or "airplay" in item.get("raw", "").lower():
            protocol = "airplay"
            category = "tv"
        out.append({
            "ip": ip,
            "protocol": protocol,
            "category": category,
            "name": item.get("server", ""),
            "model": server,
            "confidence": 0.7,
            "source": "ssdp",
            "location": item.get("location", ""),
        })

    for item in await mdns_discover(timeout=1.5):
        ip = item.get("ip", "")
        if not ip or not util.ip_in_subnets(ip, [subnet]):
            continue
        service = item.get("service", "")
        protocol = "mdns"
        category = "other"
        if service.rstrip(".") == "_home-assistant._tcp.local":
            try:
                port = int(item.get("port", 0))
            except (TypeError, ValueError):
                port = 0
            # The SRV record, rather than a default or TXT URL, owns the port.
            if not 1 <= port <= 65535:
                continue
            out.append({
                "ip": ip, "ports": [port], "protocol": "home_assistant", "category": "gateway",
                "name": item.get("location_name") or item.get("name", ""),
                "model": "home-assistant", "confidence": 0.95, "source": "mdns",
                "requires_credentials": True,
                "fields": {"port": port, "base_url": f"http://{ip}:{port}",
                           **{key: item[key] for key in ("uuid", "version", "location_name") if item.get(key)}},
            })
            continue
        if "googlecast" in service:
            protocol, category = "cast", "tv"
        elif "airplay" in service:
            protocol, category = "airplay", "tv"
        elif "matter" in service:
            protocol, category = "matter", "other"
        elif "hap" in service:
            protocol, category = "homekit", "other"
        out.append({
            "ip": ip,
            "protocol": protocol,
            "category": category,
            "name": item.get("name", ""),
            "model": item.get("model", ""),
            "confidence": 0.65,
            "source": "mdns",
        })

    for item in await tuya_discover(timeout=2.0):
        out.append(item)

    # 蓝牙设备没有 IP，用地址作为键；不参与子网过滤
    from .adapters.ble import ble_scan

    for item in await ble_scan(timeout=2.0):
        out.append(item)
    return out


def normalize_scan_options(subnets=None, prefix=24, extra_subnets=None, ports=None, scan_ping=True):
    """兼容工具/UI字符串输入，在开始任何网络操作前严格验证。"""
    def items(value):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part for part in re.split(r"[,;\s]+", value.strip()) if part]
        if not isinstance(value, (list, tuple)):
            raise ValueError("子网/端口必须为列表或逗号分隔字符串")
        return list(value)

    def networks(value):
        result = []
        for part in items(value):
            network = ipaddress.ip_network(str(part), strict=False)
            if network.version != 4:
                raise ValueError("当前子网扫描仅支持 IPv4")
            result.append(str(network))
        return list(dict.fromkeys(result))

    if isinstance(prefix, bool) or not str(prefix).isdigit() or not 0 <= int(prefix) <= 32:
        raise ValueError("prefix 必须为 0–32 的单个整数")
    parsed_ports = items(ports) if ports is not None else list(util.DEFAULT_PORTS)
    for port in parsed_ports:
        if isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("端口必须为 1–65535 的整数")
    if isinstance(scan_ping, str):
        if scan_ping.lower() not in ("true", "false", "1", "0"):
            raise ValueError("scan_ping 必须为布尔值")
        scan_ping = scan_ping.lower() in ("true", "1")
    if not isinstance(scan_ping, bool):
        raise ValueError("scan_ping 必须为布尔值")
    return networks(subnets), int(prefix), networks(extra_subnets), list(dict.fromkeys(map(int, parsed_ports))), scan_ping


async def discover_all(
    subnets: list[str] | None = None,
    prefix: int = 24,
    extra_subnets: list[str] | None = None,
    ports: list[int] | None = None,
    scan_ping: bool = True,
) -> list[ScannedDevice]:
    """扫描所有配置子网，汇总去重。

    子网为空时自动取本机网卡所在子网，用户不用先查 IP。
    """
    subnets, prefix, extra_subnets, ports, scan_ping = normalize_scan_options(
        subnets, prefix, extra_subnets, ports, scan_ping
    )
    if not subnets:
        subnets = util.local_subnets(prefix, extra_subnets)
    devices: list[ScannedDevice] = []
    for subnet in subnets:
        try:
            found = await scan_subnet(subnet, ports=ports, scan_ping=scan_ping)
        except Exception as exc:  # noqa: BLE001 - 单个子网失败不影响其余
            print(f"[smart-home] 扫描子网 {subnet} 失败：{exc}")
            found = []
        devices.extend(found)
    by_identity: dict[str, ScannedDevice] = {}
    for device in devices:
        identity = _device_identity(device)
        if identity in by_identity:
            _merge_item(by_identity[identity], device.to_dict(), device.credentials)
        else:
            by_identity[identity] = device
    return sorted(by_identity.values(), key=_device_sort_key)
