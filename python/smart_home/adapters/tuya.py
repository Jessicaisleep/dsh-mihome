# SPDX-License-Identifier: GPL-3.0-or-later
"""Tuya LAN adapter using explicitly configured data-point schemas."""
from __future__ import annotations

import asyncio
from .. import catalog
from .base import BaseAdapter, register


def _make_device(ip: str, key: str, version: str, device_id: str, timeout: float = 5.0):
    import tinytuya
    return tinytuya.Device(device_id, address=ip, local_key=key,
                           version=version, connection_timeout=timeout)


def _checked(result):
    if not isinstance(result, dict):
        raise RuntimeError("Tuya returned an invalid response")
    if result.get('Error') or result.get('Err'):
        raise RuntimeError(f"Tuya request failed (code {result.get('Err', 'unknown')})")
    return result


@register
class TuyaAdapter(BaseAdapter):
    protocol = 'tuya'
    platform = 'aligenie'

    def __init__(self, device, config=None):
        super().__init__(device, config)
        credentials = getattr(device, 'credentials', {}) or {}
        self.key = self.config.get('local_key') or credentials.get('local_key') or getattr(device, 'local_key', '')
        self.gw_id = self.config.get('device_id') or credentials.get('device_id') or credentials.get('gw_id') or getattr(device, 'gw_id', '')
        self.version = self.config.get('version') or credentials.get('version') or getattr(device, 'version', '') or '3.3'
        self.dps = self.config.get('dps') or getattr(device, 'dps', {}) or {}
        self._dev = None

    async def _device(self):
        if self._dev is None:
            if not self.key or not self.gw_id:
                raise RuntimeError('Tuya requires device_id and local_key')
            self._dev = await asyncio.to_thread(_make_device, self.ip, self.key, self.version, self.gw_id)
        return self._dev

    def _schema(self):
        for dp, spec in self.dps.items():
            if isinstance(spec, dict) and spec.get('key') and spec.get('kind') in ('bool', 'range', 'enum', 'text'):
                yield str(dp), spec

    async def list_capabilities(self):
        result = []
        for dp, spec in self._schema():
            cap = {'key': spec['key'], 'name': spec.get('name', f'dp{dp}'),
                   'kind': spec['kind'], 'writable': spec.get('writable') is True,
                   'zh': catalog.capability_zh(spec['key']), 'en': catalog.capability_en(spec['key'])}
            for field in ('range', 'unit', 'value_list'):
                if field in spec:
                    cap[field] = spec[field]
            result.append(cap)
        return result

    def _dp_for(self, capability, writable=False):
        matches = [(dp, spec) for dp, spec in self._schema() if spec['key'] == capability]
        if len(matches) != 1 or (writable and matches[0][1].get('writable') is not True):
            raise ValueError(f'Tuya capability not explicitly mapped: {capability}')
        return matches[0]

    async def read_capability(self, capability):
        dp, _ = self._dp_for(capability)
        dev = await self._device()
        result = _checked(await asyncio.to_thread(dev.status))
        if not isinstance(result.get('dps'), dict):
            raise RuntimeError('Tuya status has no DPS')
        return result['dps'].get(dp)

    async def write_capability(self, capability, value):
        dp, spec = self._dp_for(capability, writable=True)
        if spec['kind'] == 'bool':
            value = self._value_for_capability(capability, value)
        dev = await self._device()
        _checked(await asyncio.to_thread(dev.set_value, dp, value))
        return {'ok': True, 'capability': capability, 'value': value, 'dp': dp}

    async def read_all(self):
        dev = await self._device()
        result = _checked(await asyncio.to_thread(dev.status))
        dps = result.get('dps')
        if not isinstance(dps, dict):
            raise RuntimeError('Tuya status has no DPS')
        return {spec['key']: dps[dp] for dp, spec in self._schema() if dp in dps}

    async def close(self):
        dev, self._dev = self._dev, None
        if dev is not None and callable(getattr(dev, 'close', None)):
            await asyncio.to_thread(dev.close)
