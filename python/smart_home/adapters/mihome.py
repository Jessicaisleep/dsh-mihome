# SPDX-License-Identifier: GPL-3.0-or-later
"""Mi Home cloud and explicitly mapped local miIO adapters.

Cloud capabilities come from the available MIoT specification and service
metadata. Local miIO controls require a token and explicit method mappings;
there is no automatic fallback between transports and no claim of parity with
the Mi Home app.
"""

from __future__ import annotations

import asyncio
import json
import struct
import socket

from .. import localize
from .base import BaseAdapter, register


def _capability_from_spec(spec_type: str, prop_name: str) -> str | None:
    """把米家规格属性名映射到统一能力项。

    映射不了的返回 None（这些属性仍会作为「其他属性」展示，只是不进
    统一控制面）。
    """
    name = (prop_name or "").lower()
    if name in ("power", "on", "switch", "power_state"):
        return "power"
    if "brightness" in name or name in ("bri", "bright"):
        return "brightness"
    if "color_temp" in name or "ct" in name.split("_"):
        return "color_temp"
    if name in ("color", "color_value", "rgb", "hue", "saturation"):
        return "color"
    if name in ("mode", "mode_enum", "work_mode"):
        return "mode"
    if "fan_speed" in name or "speed" in name.split("_"):
        return "fan_speed"
    if "target_temp" in name or "target_temperature" in name:
        return "target_temp"
    if "target_humidity" in name or "target_humidity" in name:
        return "target_humidity"
    if "swing" in name or "swing_vertical" in name or "swing_horizontal" in name:
        return "swing"
    if name in ("position", "curtain_position", "percent"):
        return "position"
    if name in ("volume", "volume_level"):
        return "volume"
    if name in ("mute", "muted"):
        return "mute"
    if "input_source" in name or "input" in name.split("_"):
        return "input_source"
    if name in ("channel", "tv_channel"):
        return "channel"
    if name in ("clean", "start_clean", "clean_mode"):
        return "clean"
    if name in ("charge", "start_charge", "go_charge"):
        return "charge"
    if name in ("locate", "find", "find_device"):
        return "locate"
    if name in ("reboot", "restart"):
        return "reboot"
    if name in ("filter_life", "filter_life_remaining", "filter_life_time"):
        return "filter_life"
    if name in ("battery", "battery_level", "battery_percentage"):
        return "battery"
    return None


def _map_spec_props(props: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep property names as stable, service-qualified control identifiers."""
    controls = []
    for prop in props or []:
        name = str(prop.get('name') or '')
        if not name:
            continue
        values = prop.get('value_list') or []
        value_range = prop.get('range')
        typ = str(prop.get('type') or '').lower()
        kind = 'enum' if values else ('bool' if typ in ('bool', 'boolean') else ('range' if value_range else 'text'))
        controls.append({'key': name, 'name': name, 'zh': prop.get('zh') or prop.get('desc') or name,
                         'kind': kind, 'type': prop.get('type'), 'readable': bool(prop.get('readable')),
                         'writable': bool(prop.get('writable')), 'range': value_range,
                         'value_list': values, 'unit': prop.get('unit') or ''})
    return controls, []


@register
class MiHomeCloudAdapter(BaseAdapter):
    protocol = 'mihome'
    platform = 'mihome'

    def __init__(self, device, config=None):
        super().__init__(device, config)
        self.cloud = (config or {}).get('cloud')
        self._detail = None

    def _svc(self):
        if self.cloud is None:
            raise RuntimeError('米家云端未注入')
        return self.cloud

    async def _ensure_detail(self):
        if self._detail is None:
            detail = self._svc().device_detail(self.device.did)
            self._detail = localize.localize_device(_plain_detail(detail), self.protocol, self.platform)
        return self._detail

    async def list_capabilities(self):
        if self.cloud is None:
            return []
        try:
            detail = await self._ensure_detail()
        except Exception as exc:
            self.log(f'[smart-home] 米家规格读取失败：{exc}')
            return []
        controls, _ = _map_spec_props(detail.get('props', []))
        for action in detail.get('actions', []):
            name = str(action.get('name') or '')
            if name:
                controls.append({'key': name, 'name': name, 'zh': action.get('zh') or action.get('desc') or name,
                                 'kind': 'action', 'readable': False, 'writable': True,
                                 'range': None, 'value_list': [], 'unit': ''})
        return controls

    async def power_state(self):
        """Use Mi Home's confirmed power-state mapping, not a guessed spec key."""
        return self._svc().power_state(self.device.did)

    async def power(self, state):
        if not isinstance(state, bool):
            raise ValueError('state 必须为布尔值')
        self._svc().set_power_state(self.device.did, state)
        return {'ok': True, 'on': state}

    async def read_capability(self, capability):
        controls = await self.list_capabilities()
        control = next((c for c in controls if c['key'] == capability and c['kind'] != 'action'), None)
        if not control or not control['readable']:
            return None
        values = self._svc().read_props(self.device.did, [control['name']])
        if isinstance(values, dict):
            return values.get(control['name'])
        return values[0] if isinstance(values, list) and values else None

    async def write_capability(self, capability, value):
        controls = await self.list_capabilities()
        control = next((c for c in controls if c['key'] == capability and c['kind'] != 'action'), None)
        if not control or not control['writable']:
            raise ValueError(f'属性 {capability} 不可写')
        self._svc().write_prop(self.device.did, control['name'], value)
        return {'ok': True, 'capability': capability, 'value': value}

    async def run_action(self, name, params=None):
        if not any(c['key'] == name and c['kind'] == 'action' for c in await self.list_capabilities()):
            raise ValueError(f'未知动作 {name}')
        self._svc().run_action(self.device.did, name, params)
        return {'ok': True, 'action': name}

    async def read_all(self):
        controls = [c for c in await self.list_capabilities() if c['readable'] and c['kind'] != 'action']
        if not controls:
            return {}
        values = self._svc().read_props(self.device.did, [c['name'] for c in controls])
        if isinstance(values, dict):
            return {c['key']: values[c['name']] for c in controls if c['name'] in values}
        if isinstance(values, list):
            return {c['key']: value for c, value in zip(controls, values)}
        return {}

    async def close(self):
        self._detail = None


def _plain_detail(detail):
    from dataclasses import asdict, is_dataclass
    if is_dataclass(detail):
        return asdict(detail)
    return detail if isinstance(detail, dict) else {}


_MIIO_NOOP = bytes.fromhex('21310020ffffffffffffffffffffffffffffffffffffffffffffffffffffffff')


def _miio_unpack(packet, token, expected_id=None):
    import hashlib
    import hmac
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    if len(packet) < 32:
        raise ValueError('Truncated miIO packet')
    magic, length, _, did, stamp = struct.unpack('>HHIII', packet[:16])
    if magic != 0x2131 or length != len(packet):
        raise ValueError('Invalid miIO packet header')
    if expected_id is not None and did != expected_id:
        raise ValueError('Unexpected miIO device id')
    body = packet[32:]
    checksum = hashlib.md5(packet[:16] + token + body).digest()
    if not hmac.compare_digest(packet[16:32], checksum):
        raise ValueError('Invalid miIO checksum')
    if not body or len(body) % 16:
        raise ValueError('Invalid miIO ciphertext length')
    key = hashlib.md5(token).digest()
    iv = hashlib.md5(key + token).digest()
    return json.loads(unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(body), 16).decode('utf-8'))


async def _miio_send(ip, token, payload, timeout=5.0):
    import hashlib
    import time
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    try:
        token = bytes.fromhex(token)
    except (ValueError, TypeError):
        raise ValueError('miIO token must be 16-byte hex') from None
    if len(token) != 16:
        raise ValueError('miIO token must be 16-byte hex')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.connect((ip, 54321))
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(loop.sock_sendall(sock, _MIIO_NOOP), timeout)
        hello = await asyncio.wait_for(loop.sock_recv(sock, 65535), timeout)
        if len(hello) != 32:
            raise ValueError('Invalid miIO hello length')
        magic, length, _, did, stamp = struct.unpack('>HHIII', hello[:16])
        if magic != 0x2131 or length != 32 or did == 0xffffffff or stamp == 0xffffffff:
            raise ValueError('Invalid miIO hello')
        start = time.monotonic()
        key = hashlib.md5(token).digest()
        iv = hashlib.md5(key + token).digest()
        body = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(json.dumps(payload, separators=(',', ':')).encode('utf-8'), 16))
        header = struct.pack('>HHIII', 0x2131, 32 + len(body), 0, did, (stamp + int(time.monotonic() - start)) & 0xffffffff)
        packet = header + hashlib.md5(header + token + body).digest() + body
        await asyncio.wait_for(loop.sock_sendall(sock, packet), timeout)
        response = await asyncio.wait_for(loop.sock_recv(sock, 65535), timeout)
        result = _miio_unpack(response, token, did)
        if not isinstance(result, dict) or result.get('id') != payload.get('id'):
            raise ValueError('Unexpected miIO response id')
        return result
    finally:
        sock.close()


def _replace_value(obj, value):
    if obj == '$value':
        return value
    if isinstance(obj, list):
        return [_replace_value(x, value) for x in obj]
    if isinstance(obj, dict):
        return {k: _replace_value(v, value) for k, v in obj.items()}
    return obj


@register
class MiIOAdapter(BaseAdapter):
    """Local miIO controls require an explicit per-device miio_mapping.

    Each mapping entry declares kind, readable/writable, read_method/read_params,
    write_method/write_params; '$value' is replaced without type conversion.
    """
    protocol = 'miio'
    platform = 'mihome'

    def __init__(self, device, config=None):
        super().__init__(device, config)
        self.cloud = self.config.get('cloud')
        creds = getattr(device, 'credentials', {}) or {}
        tokens = self.config.get('miio_tokens') or {}
        self.token = tokens.get(self.ip) or tokens.get(self.mac.upper()) or creds.get('token') or self.config.get('miio_token') or ''
        if not self.token and self.cloud is not None and getattr(self.cloud, 'miio_token', None):
            self.token = self.cloud.miio_token(self.ip, self.mac)

    def is_supported(self):
        return bool(self.token)

    def _mapping(self):
        mapping = self.config.get('miio_mapping') or self.config.get('miot_mapping') or {}
        return mapping if isinstance(mapping, dict) else {}

    async def _call(self, method, params=None):
        if not self.token:
            raise RuntimeError('miIO 控制需要设备 token')
        result = await _miio_send(self.ip, self.token, {'id': 1, 'method': method, 'params': [] if params is None else params})
        if result.get('error') or result.get('code', 0) != 0:
            raise RuntimeError(f"miIO 返回错误：{result.get('error', result)}")
        if 'result' not in result:
            raise RuntimeError('miIO response missing result')
        for item in result['result'] if isinstance(result['result'], list) else [result['result']]:
            if isinstance(item, dict) and item.get('code', 0) != 0:
                raise RuntimeError(f'miIO property error: {item}')
        return result

    async def list_capabilities(self):
        controls = []
        for key, spec in self._mapping().items():
            if not isinstance(spec, dict):
                continue
            item = {k: spec[k] for k in ('kind', 'range', 'value_list', 'unit', 'zh') if k in spec}
            item.update({'key': key, 'name': spec.get('name', key), 'kind': spec.get('kind', 'text'),
                         'readable': bool(spec.get('readable') and spec.get('read_method')),
                         'writable': bool(spec.get('writable') and spec.get('write_method'))})
            controls.append(item)
        return controls

    def _spec(self, capability, operation):
        spec = self._mapping().get(capability)
        if not isinstance(spec, dict) or not spec.get('readable' if operation == 'read' else 'writable') or not spec.get(operation + '_method'):
            raise ValueError(f'miIO {capability} does not support {operation}')
        return spec

    async def read_capability(self, capability):
        spec = self._spec(capability, 'read')
        result = (await self._call(spec['read_method'], spec.get('read_params', [])))['result']
        if isinstance(result, list):
            result = result[0] if result else None
        if isinstance(result, dict) and 'value' in result:
            result = result['value']
        return result

    async def write_capability(self, capability, value):
        spec = self._spec(capability, 'write')
        params = _replace_value(spec.get('write_params', ['$value']), value)
        await self._call(spec['write_method'], params)
        return {'ok': True, 'capability': capability, 'value': value}

    async def run_action(self, name, params=None):
        spec = self._mapping().get(name)
        if not isinstance(spec, dict) or spec.get('kind') != 'action' or not spec.get('writable') or not spec.get('write_method'):
            raise ValueError(f'未知动作 {name}')
        result = await self._call(spec['write_method'], params if params is not None else spec.get('write_params', []))
        return {'ok': True, 'action': name, 'result': result['result']}

    async def read_all(self):
        out = {}
        for cap in await self.list_capabilities():
            if cap['readable'] and cap['kind'] != 'action':
                out[cap['key']] = await self.read_capability(cap['key'])
        return out