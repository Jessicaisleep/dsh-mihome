# SPDX-License-Identifier: GPL-3.0-or-later
"""路由器、交换机、光猫、NAS 和通用 HTTP 设备的公开页面读取。

提取公开页面的标题、表单字段名和表格；HTTP 成功仅表示页面读取成功，
不表示已经登录或取得管理设置。当前没有厂商认证、提权或设置写入实现。
"""

from __future__ import annotations

import re

from .. import catalog, util
from .base import BaseAdapter, register


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes", "开")
    return bool(value)


def _parse_html(text: str) -> dict:
    """从 HTML 里提取可读的结构化信息。

    不引第三方 HTML 解析库（插件依赖已经不少）：标签结构用正则即可，
    目标是「把页面里用户能看到的信息搬出来」，不是渲染页面。
    """
    title = ""
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    if match:
        title = util.strip_html(match.group(1))[:200]

    links: list[str] = []
    for href in re.findall(r'(?is)href=["\']([^"\']+)["\']', text):
        if href and not href.startswith(("javascript:", "#", "data:")):
            links.append(href[:200])

    forms: list[dict] = []
    for form in re.findall(r"(?is)<form[^>]*>(.*?)</form>", text):
        fields: dict[str, str] = {}
        for m in re.finditer(r'(?is)<input[^>]+>', form):
            tag = m.group(0)
            name = re.search(r'name=["\']([^"\']+)["\']', tag)
            if name:
                # Form values can include credentials, CSRF tokens and session IDs.
                fields[name.group(1)] = ""
        if fields:
            forms.append(fields)

    # 表格：第一列作键、第二列作值
    table: dict[str, str] = {}
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", text):
        cells = [util.strip_html(c) for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row)]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            table[cells[0][:100]] = cells[1][:200]

    visible = util.strip_html(text)[:4000]

    return {
        "title": title,
        "links": links[:50],
        "forms": forms[:20],
        "table": table,
        "visible": visible,
    }


async def _page(ip: str, port: int, path: str = "/", tls: bool = False, timeout: float = 3.0):
    from .. import util

    resp = await util.http_get_async(f"{'https' if tls else 'http'}://{ip}:{port}{path}", timeout=timeout)
    if resp is None:
        return None
    try:
        text = resp.text or ""
    except Exception:  # noqa: BLE001
        text = resp.content.decode("utf-8", errors="replace")
    server = resp.headers.get("Server", "") if resp.headers is not None else ""
    return {
        "url": f"{'https' if tls else 'http'}://{ip}:{port}{path}",
        "status": resp.status_code,
        "server": server,
        "text": text[:20000],
    }


class _PageAdapter(BaseAdapter):
    """按 HTTP 管理页工作的设备适配器基类。"""

    def _ports(self) -> list[int]:
        return list(getattr(self.device, "ports", []) or [])

    async def _scrape(self) -> dict:
        ports = self._ports() or [80, 443]
        failure = {"ok": False, "public_only": True, "protocol": self.protocol,
                   "error": "管理页不可达或未返回可读取的公开页面"}
        for port in ports:
            if port not in (80, 443, 8080, 8081, 8000, 5000, 5001):
                continue
            for tls in (False, True):
                page = await _page(self.ip, port, "/", tls)
                if page is None:
                    continue
                if not 200 <= page["status"] < 300:
                    failure.update(http_status=page["status"], url=page["url"],
                                   error=f"公开页面读取失败（HTTP {page['status']}）；未执行登录或设置修改")
                    continue
                data = _parse_html(page["text"])
                data.update(ok=True, public_only=True)
                data["url"] = page["url"]
                data["server"] = page["server"]
                data["status"] = page["status"]
                data["http_status"] = page["status"]
                data["protocol"] = self.protocol
                return data
        return failure

    async def read_capability(self, capability: str):
        if capability == "status":
            return await self.scrape()
        if capability == "reboot":
            return False
        return None

    async def write_capability(self, capability: str, value) -> dict:
        if capability == "reboot":
            return await self.reboot()
        raise ValueError(f"{self.protocol} 不支持写入 {capability}")

    async def reboot(self) -> dict:
        raise NotImplementedError(f"{self.protocol} 未实现重启接口")

    async def scrape(self) -> dict:
        return await self._scrape()


@register
class RouterAdapter(_PageAdapter):
    """路由器：扒管理页设置。"""

    protocol = "router"
    platform = ""

    async def list_capabilities(self) -> list[dict]:
        return [
            {"key": "status", "name": "system_info", "zh": catalog.capability_zh("status"),
             "en": catalog.capability_en("status"), "kind": "json", "readable": True, "writable": False},
        ]

    async def reboot(self) -> dict:
        raise NotImplementedError("Unverified router reboot is unsupported")

    async def scrape(self) -> dict:
        data = await self._scrape()
        if not data:
            return {"protocol": "router", "note": "管理页不可达（可能需要登录或页面由 JS 动态加载）"}
        data["note"] = "仅读取公开管理页；登录页不等于设备设置，当前未实现登录或设置修改"
        return data


@register
class SwitchAdapter(_PageAdapter):
    """交换机：仅读取可公开访问的管理页内容。"""

    protocol = "switch"
    platform = ""

    async def list_capabilities(self) -> list[dict]:
        return [
            {"key": "status", "name": "system_info", "zh": catalog.capability_zh("status"),
             "en": catalog.capability_en("status"), "kind": "json", "readable": True, "writable": False},
        ]

    async def scrape(self) -> dict:
        data = await self._scrape()
        if not data:
            return {"protocol": "switch", "note": "交换机管理页不可达（可能需要登录或非网管型交换机）"}
        data["note"] = "交换机设置通过管理页扒取；品牌页面差异大，字段以页面为准"
        return data


@register
class OnuAdapter(_PageAdapter):
    """光猫 / ONT：需要超级管理员权限。"""

    protocol = "onu"
    platform = ""

    async def list_capabilities(self) -> list[dict]:
        return [
            {"key": "status", "name": "system_info", "zh": catalog.capability_zh("status"),
             "en": catalog.capability_en("status"), "kind": "json", "readable": True, "writable": False},
        ]

    async def scrape(self) -> dict:
        data = await self._scrape()
        if not data:
            return {"protocol": "onu", "note": "光猫管理页不可达（可能被运营商锁定或需要超管账号）"}
        data["note"] = "仅读取公开信息页；当前未实现超管认证、提权或光猫设置修改"
        return data


@register
class NasAdapter(_PageAdapter):
    """NAS：扒官方 Web 管理页。"""

    protocol = "nas"
    platform = ""

    async def list_capabilities(self) -> list[dict]:
        return [
            {"key": "status", "name": "system_info", "zh": catalog.capability_zh("status"),
             "en": catalog.capability_en("status"), "kind": "json", "readable": True, "writable": False},
        ]

    async def reboot(self) -> dict:
        raise NotImplementedError("Unverified NAS reboot is unsupported")

    async def scrape(self) -> dict:
        data = await self._scrape()
        if not data:
            return {"protocol": "nas", "note": "NAS 管理页不可达（可能需要登录）"}
        data["note"] = "仅读取公开 Web 页面；当前未实现 NAS 认证会话或设置修改"
        return data


@register
class HttpGenericAdapter(_PageAdapter):
    """识别不出具体协议、但开了 HTTP 服务的设备：扒页面当状态看。"""

    protocol = "http"
    platform = ""

    async def list_capabilities(self) -> list[dict]:
        return [
            {"key": "status", "name": "page", "zh": catalog.capability_zh("status"),
             "en": catalog.capability_en("status"), "kind": "json", "readable": True, "writable": False},
        ]

    async def scrape(self) -> dict:
        data = await self._scrape()
        if not data:
            return {"protocol": "http", "note": "HTTP 服务不可达或未返回可解析页面"}
        data["note"] = "未识别到具体协议，仅展示 HTTP 页面内容"
        return data
