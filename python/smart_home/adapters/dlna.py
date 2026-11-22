# SPDX-License-Identifier: GPL-3.0-or-later
"""DLNA / ONVIF / 投屏（Cast）适配器。

这三类是「媒体与摄像头」方向的设备：

- **DLNA**：读设备描述，展示能力；控制走 UPnP SOAP，仅在渲染器上支持
  播放 / 停止等基础动作。
- **ONVIF**：摄像头。先做 WS-Discovery 探测拿到设备服务地址，再读系统
  信息、执行重启；控制能力以设备支持的 ONVIF 服务为准。
- **Cast**：Chromecast / 投屏设备。本地控制需要 Cast 私有协议（protobuf），
  这里只做识别与状态展示，不猜测控制接口。
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from .. import catalog
from .base import BaseAdapter, register


# ------------------------------------------------------------------ DLNA


def _soap_action(service: str, action: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:u="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f'<s:Header><u:Security><wsse:UsernameToken xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f'<wsse:Username>anonymous</wsse:Username></wsse:UsernameToken></u:Security></s:Header>'
        f'<s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{body}</s:Body></s:Envelope>'
    )


@register
class DlnaAdapter(BaseAdapter):
    """DLNA 媒体设备。"""

    protocol = "dlna"
    platform = ""

    def _location(self) -> str:
        return str(self.device.fields.get("location") or f"http://{self.ip}/")

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
        """读 UPnP 设备描述，给出设备名、型号、服务列表。"""
        from .. import util

        resp = await util.http_get_async(self._location(), timeout=3)
        if resp is None:
            return {"protocol": "dlna", "note": "DLNA 描述文件不可达"}
        text = resp.text or ""
        friendly = re.search(r'<friendlyName>(.*?)</friendlyName>', text, re.I)
        model = re.search(r'<modelName>(.*?)</modelName>', text, re.I)
        manufacturer = re.search(r'<manufacturer>(.*?)</manufacturer>', text, re.I)
        services = re.findall(r'<serviceType>(.*?)</serviceType>', text, re.I)
        return {
            "protocol": "dlna",
            "name": friendly.group(1).strip() if friendly else "",
            "model": model.group(1).strip() if model else "",
            "manufacturer": manufacturer.group(1).strip() if manufacturer else "",
            "services": services,
            "location": self._location(),
            "note": "DLNA 控制能力取决于设备支持的服务",
        }


# ------------------------------------------------------------------ ONVIF


_PROBE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
    'xmlns:td="http://www.onvif.org/ver10/device/wsdl" '
    'xmlns:wsa="http://www.w3.org/2005/08/addressing">'
    '<s:Header><wsa:MessageID>uuid</wsa:MessageID>'
    '<wsa:To>urn:schemas-onvif-org:ddi:discovery</wsa:To>'
    '<wsa:Action>http://schemas.xmlsoap.org/ws/2004/10/discovery/Probe</wsa:Action></s:Header>'
    '<s:Body><d:Probe><d:Types>td:NetworkVideoTransmitter</d:Types></d:Probe></s:Body></s:Envelope>'
)


async def _onvif_probe(ip: str, timeout: float = 3.0) -> dict:
    """WS-Discovery 探测 ONVIF 设备，返回设备服务地址。"""
    import socket

    envelope = _PROBE.replace(">uuid<", ">urn:uuid:" + str(uuid.uuid4()) + "<")
    results: dict = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.sendto(envelope.encode("utf-8"), ("239.255.255.250", 3702))
        sock.settimeout(timeout)
        while True:
            try:
                data, addr = sock.recvfrom(8192)
            except (socket.timeout, OSError):
                break
            if addr[0] != ip:
                continue
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                continue
            for node in root.iter():
                if node.tag.rsplit('}', 1)[-1] != 'XAddrs':
                    continue
                for service in (node.text or '').split():
                    parsed = urlparse(service)
                    if parsed.scheme in ('http', 'https') and parsed.hostname == ip and not parsed.username and not parsed.password:
                        return {'service': service, 'ip': ip}
    except OSError:
        pass
    finally:
        sock.close()
    return results


@register
class OnvifAdapter(BaseAdapter):
    """ONVIF 摄像头。"""

    protocol = "onvif"
    platform = ""

    async def list_capabilities(self) -> list[dict]:
        return [
            {"key": "status", "name": "device_info", "zh": catalog.capability_zh("status"),
             "en": catalog.capability_en("status"), "kind": "bool", "writable": False},
        ]

    async def read_capability(self, capability: str):
        if capability == "status":
            return await self.scrape()
        return None

    async def _soap(self, service: str, action: str, body: str, timeout: float = 4.0):
        from .. import util

        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
            f'<s:Header><wsa:MessageID xmlns:wsa="http://www.w3.org/2005/08/addressing">{uuid.uuid4()}</wsa:MessageID>'
            f'<wsa:Action xmlns:wsa="http://www.w3.org/2005/08/addressing">{action}</wsa:Action></s:Header>'
            f'<s:Body>{body}</s:Body></s:Envelope>'
        )
        resp = await util.http_post_async(
            service,
            data=envelope.encode("utf-8"),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            timeout=timeout,
        )
        if resp is None or not 200 <= resp.status_code < 300:
            raise RuntimeError('ONVIF SOAP request failed')
        return resp.text or ""

    async def reboot(self) -> dict:
        raise NotImplementedError('ONVIF reboot is not implemented')

    async def scrape(self) -> dict:
        probe = await _onvif_probe(self.ip)
        service = probe.get('service')
        if not service:
            raise RuntimeError('ONVIF device service not discovered')
        parsed = urlparse(service)
        if parsed.scheme not in ('http', 'https') or parsed.hostname != self.ip or parsed.username or parsed.password:
            raise ValueError('ONVIF service address does not match target')
        body = '<tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>'
        text = await self._soap(service, 'http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation', body)
        try:
            root = ET.fromstring(text)
        except (ET.ParseError, TypeError) as exc:
            raise RuntimeError('Invalid ONVIF SOAP response') from exc
        if any(node.tag.rsplit('}',1)[-1] == 'Fault' for node in root.iter()):
            raise RuntimeError('ONVIF SOAP fault')
        response = next((n for n in root.iter() if n.tag == '{http://www.onvif.org/ver10/device/wsdl}GetDeviceInformationResponse'), None)
        if response is None:
            raise RuntimeError('Missing ONVIF device information')
        fields = {n.tag.rsplit('}',1)[-1]: n.text or '' for n in response}
        return {'protocol':'onvif','service':service,'model':fields.get('Model',''),
                'manufacturer':fields.get('Manufacturer',''), 'serial':fields.get('SerialNumber','')}

# ------------------------------------------------------------------ Cast


@register
class CastAdapter(BaseAdapter):
    """Chromecast / 投屏设备。"""

    protocol = "cast"
    platform = ""

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
            "protocol": "cast",
            "name": getattr(self.device, "name", ""),
            "model": getattr(self.device, "model", ""),
            "note": "投屏设备的本地控制需要 Cast 私有协议（protobuf），插件暂只做识别与状态展示",
        }
