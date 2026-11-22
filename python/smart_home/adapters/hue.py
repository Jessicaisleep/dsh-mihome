# SPDX-License-Identifier: GPL-3.0-or-later
"""Philips Hue light adapter.

Hue controls are limited to fields exposed by the configured bridge resource.
LIFX writes are intentionally disabled until its LAN protocol path is verified.
The Hue bridge username must be supplied in the private connection record.
"""

from __future__ import annotations

import asyncio
import struct

from .base import BaseAdapter, register


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes", "开")
    return bool(value)


# ------------------------------------------------------------------ Hue


@register
class HueAdapter(BaseAdapter):
    """Philips Hue 桥接器（端口 80 / 443）。"""

    protocol = "hue"
    platform = ""

    def _base(self) -> str:
        return f"http://{self.ip}"

    def _username(self) -> str:
        return self.config.get("hue_username") or (getattr(self.device, 'credentials', {}) or {}).get('hue_username') or ""

    def _pair(self) -> str:
        """按物理按钮配对；已配对直接返回用户名。"""
        if self._username():
            return self._username()
        import requests

        payload = {"devicetype": "dsh-mihome#plugin"}
        resp = requests.post(f"{self._base()}/api", json=payload, timeout=5,
                             headers={"User-Agent": "dsh-mihome/2.0"})
        if resp.status_code in (200, 201):
            data = resp.json()
            if isinstance(data, list):
                for entry in data:
                    username = entry.get('success', {}).get('username')
                    if username:
                        self.config['hue_username'] = username
                        return username
        raise RuntimeError("Hue 配对失败：请先按住桥接器上的物理配对按钮 5 秒，再重试")

    async def _api(self, method: str, path: str, payload: dict | None = None) -> dict | str:
        import requests

        username = self._pair()
        url = f"{self._base()}/api/{username}{path}"
        if method == "GET":
            resp = requests.get(url, timeout=5, headers={"User-Agent": "dsh-mihome/2.0"})
        else:
            resp = requests.put(url, json=payload or {}, timeout=5,
                                headers={"User-Agent": "dsh-mihome/2.0"})
        if resp.status_code >= 400:
            raise RuntimeError(f"Hue API {path} 失败：{resp.status_code} {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, list):
            for entry in data:
                if 'error' in entry:
                    raise RuntimeError(f"Hue API error: {entry['error']}")
            if method != 'GET' and (not data or not all('success' in entry for entry in data)):
                raise RuntimeError('Invalid Hue write response')
        elif method != 'GET':
            raise RuntimeError('Invalid Hue write response')
        return data

    async def _lights(self) -> dict:
        data = await self._api("GET", "/lights")
        return data if isinstance(data, dict) else {}

    async def _selected_light(self):
        lights = await self._lights()
        if not lights:
            raise ValueError('No Hue lights found')
        light_id = str(self.config.get('hue_light_id') or next(iter(lights)))
        if light_id not in lights:
            raise ValueError('Configured Hue light not found')
        return light_id, lights[light_id]

    async def list_capabilities(self):
        _, light = await self._selected_light()
        state = light.get('state') or {}
        result = []
        if 'on' in state:
            result.append({'key':'power','name':'on','kind':'bool','readable':True,'writable':True})
        if 'bri' in state:
            result.append({'key':'brightness','name':'bri','kind':'range','range':[1,254,1],'readable':True,'writable':True})
        if 'xy' in state:
            result.append({'key':'color','name':'xy','kind':'text','readable':True,'writable':True})
        if 'ct' in state:
            limits = ((light.get('capabilities') or {}).get('control') or {}).get('ct') or {}
            valid = isinstance(limits.get('min'), int) and isinstance(limits.get('max'), int) and limits['min'] < limits['max']
            item = {'key':'color_temp','name':'ct','kind':'range','unit':'mired','readable':True,'writable':bool(valid)}
            if valid: item['range'] = [limits['min'], limits['max'], 1]
            result.append(item)
        return result
    async def read_capability(self, capability: str):
        lights = await self._lights()
        if not lights:
            return None
        # 没有单灯标识时，取第一盏灯
        light_id = str(self.config.get('hue_light_id') or next(iter(lights)))
        if light_id not in lights:
            raise ValueError('Configured Hue light not found')
        light = lights[light_id] or {}
        state = light.get("state") or {}
        if capability == "power":
            return _bool(state.get("on", False))
        if capability == "brightness":
            return state.get("bri")
        if capability == "color":
            xy = state.get("xy") or [0, 0]
            return f"{xy[0]:.4f},{xy[1]:.4f}"
        if capability == "color_temp":
            return state.get("ct")
        return None

    async def write_capability(self, capability: str, value) -> dict:
        controls = {c['key']: c for c in await self.list_capabilities()}
        if capability not in controls or not controls[capability]['writable']:
            raise ValueError(f'Hue does not support writing {capability}')
        light_id, _ = await self._selected_light()
        if capability == "power":
            payload = {"on": bool(self._value_for_capability(capability, value))}
        elif capability == "brightness":
            value = self._value_for_capability(capability, value)
            if not 1 <= value <= 254: raise ValueError('Hue brightness must be 1..254')
            payload = {"bri": int(value)}
        elif capability == "color":
            try:
                xy = [float(x) for x in (value.split(',') if isinstance(value, str) else value)]
            except (TypeError, ValueError):
                raise ValueError('Hue color must be an x,y pair') from None
            if len(xy) != 2 or not all(0 <= x <= 1 for x in xy):
                raise ValueError('Hue color must be an x,y pair between 0 and 1')
            payload = {"xy": xy}
        elif capability == "color_temp":
            value = self._value_for_capability(capability, value)
            low, high, _ = controls['color_temp']['range']
            if not low <= value <= high: raise ValueError('Hue color temperature outside light range')
            payload = {"ct": int(value)}
        else:
            raise ValueError(f"Hue 不支持写入 {capability}")
        await self._api("PUT", f"/lights/{light_id}/state", payload)
        return {"ok": True, "capability": capability, "value": value}

    def _first_light_id(self) -> str:
        return (self.config or {}).get("hue_light_id") or "1"

    async def read_all(self) -> dict:
        try:
            lights = await self._lights()
            out: dict = {}
            for light_id, light in (lights or {}).items():
                out[light_id] = {
                    "name": light.get("name", ""),
                    "state": light.get("state", {}),
                }
            return out
        except Exception:  # noqa: BLE001
            return {}


# ------------------------------------------------------------------ LIFX


def _build_lifx(type_id: int, payload: bytes = b"") -> bytes:
    """构造 LIFX 局域网报文。"""
    seq = 0
    size = 36 + len(payload)
    header = struct.pack(
        "<IBHIQBB",
        size,
        2,          # Origin = Link
        type_id,
        1,          # Version
        seq,
        0,          # Success
        0,          # Reserved
    ) + struct.pack("<I", len(payload))
    return header + payload


async def _lifx_call(ip: str, type_id: int, payload: bytes = b"", timeout: float = 4.0) -> bytes:
    reader, writer = await asyncio.open_connection(ip, 56700)
    try:
        writer.write(_build_lifx(type_id, payload))
        await writer.drain()
        data = await asyncio.wait_for(reader.read(2048), timeout=timeout)
    finally:
        try:
            writer.close()
        except OSError:
            pass
    return data[36:] if len(data) > 36 else b""


@register
class LifxAdapter(BaseAdapter):
    protocol = 'lifx'
    platform = ''

    async def list_capabilities(self):
        return []

    async def write_capability(self, capability, value):
        raise NotImplementedError('LIFX LAN protocol is not implemented')
