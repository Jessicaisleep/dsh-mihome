# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapters for selected Shelly, Tasmota and WLED local endpoints.

Capabilities are limited to fields confirmed by the device response. ESPHome's
unverified REST write path is disabled; firmware generations may expose
additional controls not represented here.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse

from .base import BaseAdapter, register


def _post_json(url: str, payload: dict, timeout: float = 4.0):
    import requests

    return requests.post(url, json=payload, timeout=timeout,
                         headers={"User-Agent": "dsh-mihome/2.0"})


def _post_form(url: str, payload: dict, timeout: float = 4.0):
    import requests

    return requests.post(url, data=payload, timeout=timeout,
                         headers={"User-Agent": "dsh-mihome/2.0"})


def _get(url: str, timeout: float = 3.0):
    import requests

    return requests.get(url, timeout=timeout, headers={"User-Agent": "dsh-mihome/2.0"})


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes", "开")
    return bool(value)


# ------------------------------------------------------------------ Shelly


@register
class ShellyAdapter(BaseAdapter):
    """Shelly（Gen1 用 /status，Gen2 用 /rpc JSON-RPC）。"""

    protocol = "shelly"
    platform = ""

    def _base(self) -> str:
        return f"http://{self.ip}"

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        payload = {"id": 1, "method": method, "params": params or {}}
        resp = await asyncio.to_thread(_post_json, f"{self._base()}/rpc", payload)
        if resp is None or resp.status_code != 200:
            raise RuntimeError(f"Shelly RPC 失败：{resp.status_code if resp else '无响应'}")
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"Shelly RPC 错误：{data['error']}")
        return data.get("result") or {}

    async def _status(self):
        if self.config.get('shelly_gen') == 1:
            resp = await asyncio.to_thread(_get, f"{self._base()}/status")
            if resp is None or resp.status_code != 200:
                raise RuntimeError('Shelly status unavailable')
            return resp.json()
        return await self._rpc('Shelly.GetStatus')

    async def read_all(self):
        status = await self._status()
        out = {}
        if isinstance(status.get('switch:0'), dict) and 'output' in status['switch:0']:
            out['power'] = status['switch:0']['output']
        elif status.get('relays') and 'ison' in status['relays'][0]:
            out['power'] = status['relays'][0]['ison']
        if isinstance(status.get('cover:0'), dict) and 'current_pos' in status['cover:0']:
            out['position'] = status['cover:0']['current_pos']
        elif status.get('rollers') and 'current_pos' in status['rollers'][0]:
            out['position'] = status['rollers'][0]['current_pos']
        return out

    async def list_capabilities(self):
        state = await self.read_all()
        controls = []
        if 'power' in state:
            controls.append({'key':'power','name':'power','kind':'bool','readable':True,'writable':True})
        if 'position' in state:
            controls.append({'key':'position','name':'position','kind':'range','range':[0,100,1],'unit':'%','readable':True,'writable':self.config.get('shelly_gen') != 1})
        return controls

    async def read_capability(self, capability):
        return (await self.read_all()).get(capability)
    async def write_capability(self, capability: str, value) -> dict:
        value = self._value_for_capability(capability, value)
        if capability == "power":
            if self.config.get('shelly_gen') == 1:
                resp = await asyncio.to_thread(_get, f"{self._base()}/relay/0?turn={'on' if value else 'off'}")
                if resp is None or resp.status_code != 200 or resp.json().get('ison') is not value:
                    raise RuntimeError('Shelly Gen1 relay write failed')
            else:
                await self._rpc("Switch.Set", {"id": 0, "on": value})
        elif capability == "position":
            if self.config.get('shelly_gen') == 1:
                raise ValueError('Shelly Gen1 position writes are not implemented')
            if not 0 <= value <= 100:
                raise ValueError("Position must be between 0 and 100")
            await self._rpc("Cover.GoToPosition", {"id": 0, "pos": int(value)})
        else:
            raise ValueError(f"Shelly 不支持写入 {capability}")
        return {"ok": True, "capability": capability, "value": value}

    async def run_action(self, name: str, params: dict | None = None) -> dict:
        await self._rpc(name, params or {})
        return {"ok": True, "action": name}

# ------------------------------------------------------------------ Tasmota


@register
class TasmotaAdapter(BaseAdapter):
    """Tasmota（/cm?cmnd=...）。"""

    protocol = "tasmota"
    platform = ""

    def _base(self) -> str:
        return f"http://{self.ip}"

    async def _cmd(self, command: str) -> dict:
        url = f"{self._base()}/cm?cmnd={urllib.parse.quote(command)}"
        resp = await asyncio.to_thread(_get, url)
        if resp is None or resp.status_code != 200:
            raise RuntimeError(f"Tasmota 命令失败：{command}")
        text = resp.text or ""
        if not text.startswith("{"):
            return {}
        try:
            return json.loads(text)
        except ValueError:
            return {}

    async def list_capabilities(self):
        state = await self.read_all()
        controls = []
        for key, kind, limits, unit in [('power','bool',None,''), ('brightness','range',[0,100,1],'%'), ('color_temp','range',None,'mired'), ('color','text',None,'')]:
            if key in state:
                item = {'key':key,'name':key,'kind':kind,'readable':True,'writable':key in ('power','brightness')}
                if limits is not None: item['range'] = limits
                if unit: item['unit'] = unit
                controls.append(item)
        return controls
    async def read_capability(self, capability: str):
        return (await self.read_all()).get(capability)

    async def write_capability(self, capability: str, value) -> dict:
        if capability == "power":
            wanted = self._value_for_capability(capability, value)
            response = await self._cmd("Power " + ("On" if wanted else "Off"))
            if response.get('POWER') not in ('ON', 'OFF') or _bool(response['POWER']) != wanted:
                raise RuntimeError('Tasmota power command was not confirmed')
        elif capability == "brightness":
            brightness = self._value_for_capability(capability, value)
            if not 0 <= brightness <= 100:
                raise ValueError('Tasmota brightness must be 0..100')
            response = await self._cmd(f"Dimmer {int(brightness)}")
            if response.get('Dimmer') != int(brightness):
                raise RuntimeError('Tasmota dimmer command was not confirmed')
        else:
            raise ValueError(f"Tasmota 不支持写入 {capability}")
        return {"ok": True, "capability": capability, "value": value}

    async def read_all(self) -> dict:
        status = (await self._cmd("Status 11")).get("StatusSTS", {})
        out = {}
        if "POWER" in status:
            out["power"] = _bool(status["POWER"])
        if "Dimmer" in status:
            out["brightness"] = status["Dimmer"]
        if "CT" in status:
            out["color_temp"] = status["CT"]
        if "Color" in status:
            out["color"] = status["Color"]
        return out


# ------------------------------------------------------------------ WLED


@register
class WledAdapter(BaseAdapter):
    """WLED（/json/state）。"""

    protocol = "wled"
    platform = ""

    def _base(self) -> str:
        return f"http://{self.ip}"

    async def list_capabilities(self) -> list[dict]:
        return [
            {'key': 'power', 'name': 'on', 'kind': 'bool', 'writable': True},
            {'key': 'brightness', 'name': 'bri', 'kind': 'range', 'range': [0, 100, 1], 'unit': '%', 'writable': True},
            {'key': 'color', 'name': 'col', 'kind': 'text', 'writable': True},
        ]

    async def read_capability(self, capability: str):
        resp = await asyncio.to_thread(_get, f"{self._base()}/json/state")
        if resp is None or resp.status_code != 200:
            return None
        data = resp.json()
        if capability == "power":
            return _bool(data.get("on"))
        if capability == "brightness":
            return round(data['bri'] * 100 / 255) if 'bri' in data else None
        if capability == "color":
            seg = data.get("seg") or [{}]
            col = seg[0].get("col") or []
            if col and len(col[0]) >= 3:
                return "#%02x%02x%02x" % tuple(col[0][:3])
        return None

    async def write_capability(self, capability: str, value) -> dict:
        if capability == "power":
            payload = {"on": bool(self._value_for_capability(capability, value))}
        elif capability == "brightness":
            brightness = self._value_for_capability(capability, value)
            if not 0 <= brightness <= 100:
                raise ValueError("Brightness must be between 0 and 100")
            payload = {"bri": round(brightness * 255 / 100)}
        elif capability == "color":
            import re
            if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
                raise ValueError('Color must be #RRGGBB')
            payload = {"seg": [{"col": [[_hex_to_int(value[1:3]), _hex_to_int(value[3:5]), _hex_to_int(value[5:7])]]}]}
        else:
            raise ValueError(f"WLED 不支持写入 {capability}")
        resp = await asyncio.to_thread(_post_json, f"{self._base()}/json/state", payload)
        if resp is None or resp.status_code != 200:
            raise RuntimeError("WLED 写入失败")
        return {"ok": True, "capability": capability, "value": value}

    async def read_all(self) -> dict:
        resp = await asyncio.to_thread(_get, f"{self._base()}/json/state")
        if resp is None or resp.status_code != 200:
            return {}
        data = resp.json()
        out = {}
        if 'on' in data:
            out['power'] = bool(data['on'])
        if 'bri' in data:
            out['brightness'] = round(data['bri'] * 100 / 255)
        segments = data.get('seg') or []
        colors = segments[0].get('col') or [] if segments else []
        if colors and len(colors[0]) >= 3:
            out['color'] = '#%02x%02x%02x' % tuple(colors[0][:3])
        return out


def _hex_to_int(text: str) -> int:
    return int(text, 16) if text else 0


# ------------------------------------------------------------------ ESPHome


@register
class EspHomeAdapter(BaseAdapter):
    """ESPHome entity discovery and its native API are not implemented."""
    protocol = 'esphome'
    platform = ''

    async def list_capabilities(self):
        return []

    async def write_capability(self, capability, value):
        raise NotImplementedError('ESPHome entity API is not implemented')

    async def read_all(self):
        return {}