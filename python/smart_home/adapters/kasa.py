# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy TP-Link Kasa and Yeelight local protocol adapters.

Kasa support is limited to observed legacy relay states. Yeelight controls
require a reported method in the device's support list. Newer encrypted Kasa
variants are not implemented here.
"""

from __future__ import annotations

import asyncio
import json
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


# ------------------------------------------------------------------ Kasa


def _kasa_encode(payload: bytes) -> bytes:
    """Legacy Kasa TCP framing and rolling XOR."""
    key = 0xAB
    body = bytearray()
    for byte in payload:
        key ^= byte
        body.append(key)
    return struct.pack(">I", len(body)) + body


def _kasa_decode(data: bytes) -> bytes:
    if len(data) < 4:
        raise ValueError("Truncated Kasa header")
    length = struct.unpack(">I", data[:4])[0]
    if len(data) != length + 4:
        raise ValueError("Invalid Kasa frame length")
    key = 0xAB
    result = bytearray()
    for byte in data[4:]:
        result.append(byte ^ key)
        key = byte
    return bytes(result)


async def _kasa_call(ip: str, payload: dict, timeout: float = 4.0) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, 9999), timeout)
    try:
        writer.write(_kasa_encode(body))
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
        length = struct.unpack(">I", header)[0]
        if length > 1024 * 1024:
            raise ValueError("Kasa frame too large")
        data = header + await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    finally:
        writer.close()
        await writer.wait_closed()
    result = json.loads(_kasa_decode(data).decode("utf-8"))
    for module in result.values():
        if isinstance(module, dict):
            for response in module.values():
                if isinstance(response, dict) and response.get("err_code", 0) != 0:
                    raise RuntimeError(f"Kasa error: {response}")
    return result


@register
class KasaAdapter(BaseAdapter):
    """TP-Link Kasa 智能插座 / 开关。"""

    protocol = "kasa"
    platform = ""

    async def _sysinfo(self) -> dict:
        result = await _kasa_call(self.ip, {"system": {"get_sysinfo": {}}})
        return (result.get("system") or {}).get("get_sysinfo") or {}

    async def list_capabilities(self):
        info = await self._sysinfo()
        if 'relay_state' not in info:
            return []
        return [{'key':'power','name':'relay_state','kind':'bool','readable':True,'writable':True}]

    async def read_capability(self, capability):
        if capability != 'power':
            return None
        info = await self._sysinfo()
        return _bool(info['relay_state']) if 'relay_state' in info else None

    async def write_capability(self, capability, value):
        if capability != 'power' or 'relay_state' not in await self._sysinfo():
            raise ValueError(f'Kasa unsupported capability {capability}')
        state = 1 if self._value_for_capability(capability, value) else 0
        result = await _kasa_call(self.ip, {'system': {'set_relay_state': {'state': state}}})
        ack = (result.get('system') or {}).get('set_relay_state')
        if not isinstance(ack, dict) or ack.get('err_code') != 0:
            raise RuntimeError('Kasa relay command not acknowledged')
        return {'ok':True,'capability':capability,'value':value}
    async def read_all(self) -> dict:
        info = await self._sysinfo()
        return {'power': _bool(info['relay_state'])} if 'relay_state' in info else {}


# ------------------------------------------------------------------ Yeelight


async def _yeelight_call(ip: str, method: str, params: list, timeout: float = 4.0) -> dict:
    payload = json.dumps({"id": 1, "method": method, "params": params}, separators=(",", ":"))
    reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, 55443), timeout)
    try:
        writer.write((payload + "\r\n").encode("utf-8"))
        await writer.drain()
        async def receive():
            while True:
                line = await reader.readline()
                if not line:
                    raise EOFError("Yeelight connection closed before response")
                result = json.loads(line.decode("utf-8"))
                if result.get("id") != 1:
                    continue
                if "error" in result:
                    raise RuntimeError(f"Yeelight error: {result['error']}")
                if "result" not in result:
                    raise ValueError("Invalid Yeelight response")
                return result
        return await asyncio.wait_for(receive(), timeout)
    finally:
        writer.close()
        await writer.wait_closed()


@register
class YeelightAdapter(BaseAdapter):
    """Yeelight 智能灯（端口 55443）。"""

    protocol = "yeelight"
    platform = "mihome"

    _props = {'power':'power', 'brightness':'bright', 'color_temp':'ct', 'color':'rgb'}
    _methods = {'power':'set_power', 'brightness':'set_bright', 'color_temp':'set_ct_abx', 'color':'set_rgb'}

    def _supported(self):
        fields = getattr(self.device, 'fields', {}) or {}
        support = self.config.get('support', fields.get('support', []))
        if isinstance(support, str):
            return set(support.replace(',', ' ').split())
        return set(support) if isinstance(support, (list, tuple, set)) else set()

    def _ct_range(self):
        limits = self.config.get('color_temp_range')
        if isinstance(limits, (list,tuple)) and len(limits) == 2 and all(type(x) is int for x in limits) and 0 < limits[0] < limits[1]:
            return list(limits)
        return None

    async def read_all(self):
        result = await _yeelight_call(self.ip, 'get_prop', list(self._props.values()))
        values = result.get('result')
        if not isinstance(values, list) or len(values) != len(self._props):
            raise RuntimeError('Invalid Yeelight property response')
        out = {}
        for key, value in zip(self._props, values):
            if value is None or value == '':
                continue
            if key == 'power':
                if value not in ('on','off'):
                    continue
                out[key] = value == 'on'
            else:
                try:
                    val = int(value)
                except (TypeError, ValueError):
                    continue
                if key == 'brightness' and not 1 <= val <= 100: continue
                if key == 'color' and not 0 <= val <= 0xffffff: continue
                if key == 'color_temp' and val <= 0: continue
                out[key] = val
        return out

    async def list_capabilities(self):
        values = await self.read_all()
        supported = self._supported()
        controls = []
        for key in self._props:
            if key not in values: continue
            writable = self._methods[key] in supported
            item = {'key':key,'name':self._props[key],'kind':'bool' if key == 'power' else 'range',
                    'readable':True,'writable':writable}
            if key == 'brightness': item.update({'range':[1,100,1], 'unit':'%'})
            if key == 'color': item['range'] = [0,0xffffff,1]
            if key == 'color_temp':
                limits = self._ct_range()
                item['unit'] = 'K'
                item['writable'] = writable and limits is not None
                if limits: item['range'] = [*limits,1]
            controls.append(item)
        return controls

    async def read_capability(self, capability):
        return (await self.read_all()).get(capability)

    async def write_capability(self, capability, value):
        controls = {c['key']:c for c in await self.list_capabilities()}
        if capability not in controls or not controls[capability]['writable']:
            raise ValueError(f'Yeelight does not support writing {capability}')
        if capability == 'power':
            value = self._value_for_capability(capability, value)
            arg = 'on' if value else 'off'
        else:
            if type(value) is not int:
                raise ValueError('Yeelight value must be an integer')
            low, high, _ = controls[capability]['range']
            if not low <= value <= high:
                raise ValueError('Yeelight value outside supported range')
            arg = value
        result = await _yeelight_call(self.ip, self._methods[capability], [arg,'smooth',300])
        if result.get('result') != ['ok']:
            raise RuntimeError('Yeelight command not acknowledged')
        return {'ok':True,'capability':capability,'value':value}

    async def run_action(self, name, params=None):
        raise NotImplementedError('Yeelight actions are not mapped')


def _rgb_to_int(text: str) -> int:
    import re
    if not isinstance(text, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', text):
        raise ValueError('RGB must be #RRGGBB')
    return int(text[1:], 16)