# SPDX-License-Identifier: GPL-3.0-or-later
"""Home Assistant REST bridge, with entity and service verified capabilities.

Connection: flat config ``base_url`` / optional ``entity_id`` / ``timeout``;
private ``device.credentials`` supplies ``token`` (and may supply base_url).
A hub has no entity_id. ``discover_entities`` returns public records suitable for
manager expansion; manager must inherit the connection and credential reference.

References: https://developers.home-assistant.io/docs/api/rest/
Feature flags: home-assistant/core homeassistant/components/*/const.py.
No POST /api/states: that endpoint cannot control a physical device.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math
import re
from urllib.parse import urlsplit, urlunsplit

from .. import catalog
from .base import BaseAdapter, register

_ENTITY = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_CATEGORIES = {
    'light': 'light', 'switch': 'switch', 'input_boolean': 'switch', 'fan': 'fan',
    'climate': 'air_conditioner', 'cover': 'curtain', 'media_player': 'tv',
    'vacuum': 'vacuum', 'lock': 'lock', 'humidifier': 'humidifier',
    'sensor': 'sensor', 'binary_sensor': 'sensor',
}


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Home Assistant value must be a finite number')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError('Home Assistant value must be a finite number') from None
    if not math.isfinite(result):
        raise ValueError('Home Assistant value must be a finite number')
    return int(result) if result.is_integer() else result


def _numeric_read(value):
    if value is None:
        return None
    try:
        return _number(value)
    except ValueError:
        return None


@register
class HomeAssistantAdapter(BaseAdapter):
    protocol = 'home_assistant'
    platform = 'home_assistant'

    def _entity_id(self) -> str:
        fields = getattr(self.device, 'fields', {}) or {}
        entity = fields.get('entity_id') or getattr(self.device, 'entity_id', '')
        if not entity:
            explicit = getattr(self.device, 'id', '') or getattr(self.device, 'did', '')
            suffix = str(explicit).removeprefix('home_assistant:')
            entity = suffix if _ENTITY.fullmatch(suffix) else ''
        # A child's identity is authoritative over inherited hub configuration.
        entity = entity or self.config.get('entity_id') or ''
        if entity and not _ENTITY.fullmatch(str(entity)):
            raise ValueError('Invalid Home Assistant entity_id')
        return str(entity or '')

    def _connection(self) -> tuple[str, str, float]:
        credentials = getattr(self.device, 'credentials', {}) or {}
        base = self.config.get('base_url') or credentials.get('base_url') or ''
        token = credentials.get('token') or self.config.get('token') or ''
        try:
            parts = urlsplit(str(base))
            valid = parts.scheme in ('http', 'https') and parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment)
            _ = parts.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('Home Assistant requires an http(s) base_url without credentials or query')
        if not isinstance(token, str) or not token.strip() or '\n' in token or '\r' in token:
            raise ValueError('Home Assistant requires a token in private credentials')
        timeout = _number(self.config.get('timeout', 10))
        if not 0 < timeout <= 120:
            raise ValueError('Home Assistant timeout must be between 0 and 120 seconds')
        path = parts.path.rstrip('/')
        # Accept either the frontend base or a copied /api/ base.
        if path.endswith('/api'):
            path = path[:-4]
        return urlunsplit((parts.scheme, parts.netloc, path, '', '')), token, float(timeout)

    async def _request(self, method: str, path: str, payload: dict | None = None):
        """Run blocking requests off-loop; never include URL/body/headers in errors."""
        import requests
        base, token, timeout = self._connection()
        if method not in ('GET', 'POST') or not path.startswith('/api/') or '?' in path or '#' in path:
            raise ValueError('Invalid Home Assistant REST request')
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        def call():
            try:
                response = requests.request(method, base + path, headers=headers,
                                            json=payload, timeout=timeout, allow_redirects=False)
            except Exception:
                raise RuntimeError('Home Assistant REST transport failed') from None
            if response.status_code not in (200, 201):
                raise RuntimeError(f'Home Assistant REST HTTP {response.status_code}')
            try:
                data = response.json()
            except Exception:
                raise RuntimeError('Home Assistant REST returned invalid JSON') from None
            if not isinstance(data, (dict, list)):
                raise RuntimeError('Home Assistant REST returned an invalid JSON structure')
            if isinstance(data, dict) and ('error' in data or data.get('success') is False):
                raise RuntimeError('Home Assistant REST reported an error')
            return data
        return await asyncio.to_thread(call)

    async def _services(self) -> dict[str, dict]:
        data = await self._request('GET', '/api/services')
        if not isinstance(data, list):
            raise RuntimeError('Home Assistant returned an invalid service directory')
        result = {}
        for entry in data:
            if not isinstance(entry, dict) or not isinstance(entry.get('domain'), str):
                raise RuntimeError('Home Assistant returned an invalid service directory')
            names = entry.get('services', {})
            if isinstance(names, list) and all(isinstance(name, str) for name in names):
                names = {name: {} for name in names}
            if not isinstance(names, dict):
                raise RuntimeError('Home Assistant returned an invalid service directory')
            result[entry['domain']] = names
        return result

    async def _state(self) -> dict:
        entity = self._entity_id()
        if not entity:
            raise ValueError('Select a Home Assistant entity before reading or controlling')
        item = await self._request('GET', '/api/states/' + entity)
        if not isinstance(item, dict) or item.get('entity_id') != entity or not isinstance(item.get('attributes'), dict) or 'state' not in item:
            raise RuntimeError('Home Assistant returned an invalid entity state')
        return item

    async def probe(self) -> dict:
        if self._entity_id():
            item = await self._state()
            return {'entity_id': item['entity_id'], 'domain': item['entity_id'].split('.')[0],
                    'online': item['state'] != 'unavailable'}
        rows = await self.discover_entities()
        return {'platform': 'home_assistant', 'entity_count': len(rows)}

    async def discover_entities(self) -> list[dict]:
        """GET-only hub expansion; no credentials, URL or arbitrary attrs escape."""
        data = await self._request('GET', '/api/states')
        if not isinstance(data, list):
            raise RuntimeError('Home Assistant returned an invalid state list')
        rows = []
        for item in data:
            if not isinstance(item, dict) or not _ENTITY.fullmatch(str(item.get('entity_id', ''))):
                continue
            entity = item['entity_id']
            domain = entity.split('.')[0]
            attrs = item.get('attributes') or {}
            if not isinstance(attrs, dict):
                continue
            rows.append({'entity_id': entity, 'id': 'home_assistant:' + entity,
                         'protocol': self.protocol, 'platform': self.platform,
                         'name': attrs.get('friendly_name') or entity,
                         'model': 'Home Assistant ' + domain,
                         'category': (attrs.get('device_class') if domain == 'media_player' and attrs.get('device_class') in ('tv', 'speaker', 'projector') else _CATEGORIES.get(domain, 'other')),
                         'online': item.get('state') != 'unavailable',
                         'fields': {'entity_id': entity, 'domain': domain}})
        return rows

    @staticmethod
    def _action_schema(description: dict) -> dict:
        properties, required = {}, []
        fields = description.get('fields', {}) if isinstance(description, dict) else {}
        for name, field in fields.items():
            if name in ('entity_id', 'device_id', 'area_id', 'floor_id', 'label_id') or not isinstance(field, dict):
                continue
            selector = field.get('selector') or {}
            spec = {'type': 'string'}
            if 'number' in selector:
                spec = {'type': 'number'}
                for source, target in (('min', 'minimum'), ('max', 'maximum'), ('step', 'multipleOf')):
                    if source in (selector['number'] or {}):
                        spec[target] = selector['number'][source]
            elif 'boolean' in selector:
                spec = {'type': 'boolean'}
            elif 'object' in selector:
                spec = {'type': 'object'}
            elif 'select' in selector:
                options = (selector['select'] or {}).get('options') or []
                if options:
                    spec['enum'] = [option.get('value') if isinstance(option, dict) else option for option in options]
            if field.get('description'):
                spec['description'] = str(field['description'])
            properties[name] = spec
            if field.get('required'):
                required.append(name)
        return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}

    def _plans(self, item: dict, directory: dict) -> tuple[list[dict], dict, dict]:
        """Build public capabilities plus private read/service bindings."""
        entity = item['entity_id']
        domain = entity.split('.')[0]
        attrs = item['attributes']
        state = item['state']
        services = directory.get(domain, {})
        try:
            features = int(attrs.get('supported_features', 0) or 0)
        except (TypeError, ValueError):
            features = 0
        available = state != 'unavailable' and (state != 'unknown' or domain in ('button', 'scene', 'script', 'input_button'))
        caps, values, writes = [], {}, {}

        def has(service, feature=None):
            return available and service in services and (feature is None or bool(features & feature))

        def add(key, value, kind='text', service='', param='', supported=True, range_=None, unit='', options=None, readable=True, transform=''):
            writable = bool(supported and service and has(service))
            cap = {'key': key, 'name': key, 'zh': catalog.capability_zh(key),
                   'en': catalog.capability_en(key), 'kind': kind,
                   'readable': readable, 'writable': writable}
            if range_ is not None:
                cap['range'] = list(range_)
                cap.update(zip(('min', 'max', 'step'), range_))
            if unit:
                cap['unit'] = unit
            if options is not None:
                cap['value_list'] = [{'value': option, 'description': str(option)} for option in options]
            caps.append(cap)
            if readable:
                values[key] = value
            if writable:
                writes[key] = {'service': service, 'param': param, 'transform': transform, 'cap': cap}

        def attr(key, name, kind='text', **kwargs):
            if name in attrs or kwargs.get('supported') and kwargs.get('service'):
                add(key, attrs.get(name), kind, **kwargs)

        def numeric(key, name, low=None, high=None, step=1, unit='', service='', param='', supported=True, transform=''):
            if name not in attrs and not (service and supported):
                return
            bounds = [low, high, step] if low is not None and high is not None else None
            add(key, _numeric_read(attrs.get(name)), 'range', service, param, supported, bounds, unit, transform=transform)

        def enum(key, name, options, service, param='', feature=None):
            choices = attrs.get(options) or []
            if not isinstance(choices, list):
                choices = []
            if name in attrs or choices:
                add(key, state if name == '$state' else attrs.get(name), 'enum', service,
                    param or name, bool(choices) and (feature is None or bool(features & feature)), options=choices)

        def action(key, service, feature=None):
            if has(service, feature):
                add(key, None, 'action', service, readable=False)
                caps[-1]['params_schema'] = self._action_schema(services[service])
                writes[key]['schema'] = caps[-1]['params_schema']

        def power(on_feature=None, off_feature=None):
            readable = None if state in ('unknown', 'unavailable') else state != 'off'
            supported = has('turn_on', on_feature) and has('turn_off', off_feature)
            add('power', readable, 'bool', 'turn_on', supported=supported, transform='power')

        add('status', state)
        if domain in ('light', 'switch', 'input_boolean', 'humidifier', 'fan', 'media_player', 'climate'):
            if domain == 'fan':
                power(32, 16)
            elif domain == 'media_player':
                power(128, 256)
            elif domain == 'climate':
                power(256, 128)
            else:
                power()

        if domain == 'light':
            modes = attrs.get('supported_color_modes') or []
            brightness = any(mode not in ('onoff', 'unknown') for mode in modes)
            numeric('brightness', 'brightness', 0, 100, unit='%', service='turn_on', param='brightness', supported=brightness, transform='brightness')
            if 'brightness' in values and values['brightness'] is not None:
                values['brightness'] = round(values['brightness'] * 100 / 255, 4)
            numeric('color_temp', 'color_temp_kelvin', attrs.get('min_color_temp_kelvin'), attrs.get('max_color_temp_kelvin'), unit='K', service='turn_on', param='color_temp_kelvin', supported='color_temp' in modes and attrs.get('min_color_temp_kelvin') is not None and attrs.get('max_color_temp_kelvin') is not None)
            attr('color', 'rgb_color', service='turn_on', param='rgb_color', supported=bool(set(modes) & {'rgb', 'rgbw', 'rgbww', 'hs', 'xy'}), transform='rgb')
            enum('effect', 'effect', 'effect_list', 'turn_on')
        elif domain == 'fan':
            numeric('fan_speed', 'percentage', 0, 100, attrs.get('percentage_step', 1), '%', 'set_percentage', 'percentage', bool(features & 1))
            attr('swing', 'oscillating', 'bool', service='oscillate', param='oscillating', supported=bool(features & 2))
            if 'direction' in attrs or features & 4:
                add('direction', attrs.get('direction'), 'enum', 'set_direction', 'direction', bool(features & 4), options=['forward', 'reverse'])
            enum('mode', 'preset_mode', 'preset_modes', 'set_preset_mode', feature=8)
        elif domain == 'climate':
            unit = attrs.get('temperature_unit') or attrs.get('unit_of_measurement') or self.config.get('temperature_unit') or ''
            numeric('temperature', 'current_temperature', unit=unit, supported=False)
            numeric('humidity', 'current_humidity', unit='%', supported=False)
            for key, name, bit in [('target_temp', 'temperature', 1), ('target_temp_low', 'target_temp_low', 2), ('target_temp_high', 'target_temp_high', 2)]:
                numeric(key, name, attrs.get('min_temp'), attrs.get('max_temp'), attrs.get('target_temp_step', 1), unit, 'set_temperature', name, bool(features & bit), transform='temperature_pair' if bit == 2 else '')
            enum('mode', '$state', 'hvac_modes', 'set_hvac_mode', 'hvac_mode')
            enum('fan_speed', 'fan_mode', 'fan_modes', 'set_fan_mode', feature=8)
            enum('preset', 'preset_mode', 'preset_modes', 'set_preset_mode', feature=16)
            enum('swing', 'swing_mode', 'swing_modes', 'set_swing_mode', feature=32)
            enum('swing_horizontal', 'swing_horizontal_mode', 'swing_horizontal_modes', 'set_swing_horizontal_mode', feature=512)
            numeric('target_humidity', 'humidity', attrs.get('min_humidity', 0), attrs.get('max_humidity', 100), attrs.get('target_humidity_step', 1), '%', 'set_humidity', 'humidity', bool(features & 4))
        elif domain == 'cover':
            numeric('position', 'current_position', 0, 100, unit='%', service='set_cover_position', param='position', supported=bool(features & 4))
            numeric('tilt_position', 'current_tilt_position', 0, 100, unit='%', service='set_cover_tilt_position', param='tilt_position', supported=bool(features & 128))
            for key, service, bit in [('open', 'open_cover', 1), ('close', 'close_cover', 2), ('stop', 'stop_cover', 8), ('open_tilt', 'open_cover_tilt', 16), ('close_tilt', 'close_cover_tilt', 32), ('stop_tilt', 'stop_cover_tilt', 64)]:
                action(key, service, bit)
        elif domain == 'media_player':
            numeric('volume', 'volume_level', 0, 100, unit='%', service='volume_set', param='volume_level', supported=bool(features & 4), transform='volume')
            if 'volume' in values and values['volume'] is not None:
                values['volume'] = round(values['volume'] * 100, 4)
            attr('mute', 'is_volume_muted', 'bool', service='volume_mute', param='is_volume_muted', supported=bool(features & 8))
            enum('input_source', 'source', 'source_list', 'select_source', feature=2048)
            for key, service, bit in [('play', 'media_play', 16384), ('pause', 'media_pause', 1), ('stop', 'media_stop', 4096), ('next', 'media_next_track', 32), ('previous', 'media_previous_track', 16), ('play_media', 'play_media', 512)]:
                action(key, service, bit)
        elif domain == 'vacuum':
            enum('fan_speed', 'fan_speed', 'fan_speed_list', 'set_fan_speed', feature=32)
            for key, service, bit in [('clean', 'start', 8192), ('pause', 'pause', 4), ('stop', 'stop', 8), ('charge', 'return_to_base', 16), ('locate', 'locate', 512), ('clean_spot', 'clean_spot', 1024)]:
                action(key, service, bit)
            numeric('battery', 'battery_level', 0, 100, unit='%', supported=False)
        elif domain == 'lock':
            action('lock', 'lock')
            action('unlock', 'unlock')
            action('open', 'open', 1)
        elif domain == 'humidifier':
            numeric('humidity', 'current_humidity', unit='%', supported=False)
            numeric('target_humidity', 'humidity', attrs.get('min_humidity', 0), attrs.get('max_humidity', 100), attrs.get('target_humidity_step', 1), '%', 'set_humidity', 'humidity')
            enum('mode', 'mode', 'available_modes', 'set_mode', feature=1)
        elif domain in ('select', 'input_select'):
            enum('mode', '$state', 'options', 'select_option', 'option')
        elif domain in ('number', 'input_number'):
            bounds = [attrs['min'], attrs['max'], attrs.get('step', 1)] if 'min' in attrs and 'max' in attrs else None
            add('value', _numeric_read(state), 'range', 'set_value', 'value', bounds is not None, bounds, attrs.get('unit_of_measurement', ''))
        elif domain in ('button', 'input_button', 'scene', 'script'):
            action({'button': 'press', 'input_button': 'press', 'scene': 'activate', 'script': 'run'}[domain], 'press' if domain in ('button', 'input_button') else 'turn_on')
            if domain == 'script':
                action('stop', 'turn_off')
        elif domain == 'sensor':
            key = {'temperature': 'temperature', 'humidity': 'humidity', 'battery': 'battery', 'power': 'power', 'energy': 'energy'}.get(attrs.get('device_class'), 'value')
            numeric_value = _numeric_read(state)
            add(key, numeric_value if numeric_value is not None else state, 'range' if numeric_value is not None else 'text', unit=attrs.get('unit_of_measurement', ''))
        elif domain == 'binary_sensor':
            add('value', {'on': True, 'off': False}.get(state), 'bool')
        return caps, values, writes

    async def _snapshot(self) -> tuple[dict, list[dict], dict, dict]:
        item = await self._state()
        if item['entity_id'].startswith('climate.') and not (item['attributes'].get('temperature_unit') or item['attributes'].get('unit_of_measurement') or self.config.get('temperature_unit')):
            config = await self._request('GET', '/api/config')
            if not isinstance(config, dict):
                raise RuntimeError('Home Assistant returned invalid configuration')
            item = deepcopy(item)
            item['attributes']['temperature_unit'] = (config.get('unit_system') or {}).get('temperature', '')
        caps, values, writes = self._plans(item, await self._services())
        return item, caps, values, writes

    async def list_capabilities(self) -> list[dict]:
        if not self._entity_id():
            return []
        return (await self._snapshot())[1]

    async def read_all(self) -> dict:
        if not self._entity_id():
            return {}
        return (await self._snapshot())[2]

    async def read_capability(self, capability: str):
        values = await self.read_all()
        if capability not in values:
            raise ValueError('Unknown or unreadable Home Assistant capability')
        return values[capability]

    async def _call_service(self, entity: str, service: str, payload: dict) -> dict:
        domain = entity.split('.')[0]
        data = await self._request('POST', f'/api/services/{domain}/{service}', {'entity_id': entity, **payload})
        if not isinstance(data, list) or any(not isinstance(row, dict) or 'entity_id' not in row or 'state' not in row or 'error' in row for row in data):
            raise RuntimeError('Home Assistant service returned an invalid response')
        return {'ok': True, 'entity_id': entity, 'service': domain + '.' + service}

    async def write_capability(self, capability: str, value) -> dict:
        item, _, _, bindings = await self._snapshot()
        binding = bindings.get(capability)
        if not binding or binding['cap']['kind'] == 'action':
            raise ValueError('Home Assistant capability is not writable')
        cap, service = binding['cap'], binding['service']
        if cap['kind'] == 'bool':
            value = self._value_for_capability('power', value)
        elif cap['kind'] == 'range':
            value = _number(value)
            if cap.get('range'):
                low, high, step = cap['range']
                if not low <= value <= high:
                    raise ValueError('Home Assistant value is outside the entity range')
                if step and not math.isclose((value - low) / step, round((value - low) / step), abs_tol=1e-7):
                    raise ValueError('Home Assistant value does not match the entity step')
        elif cap['kind'] == 'enum':
            if value not in [entry['value'] for entry in cap.get('value_list', [])]:
                raise ValueError('Home Assistant value is not an entity option')
        transform = binding['transform']
        if transform == 'power':
            service = 'turn_on' if value else 'turn_off'
            payload = {}
        elif transform == 'rgb':
            try:
                rgb = [_number(part) for part in (value.split(',') if isinstance(value, str) else value)]
            except (ValueError, TypeError):
                raise ValueError('Home Assistant color requires an RGB triple') from None
            if len(rgb) != 3 or any(not isinstance(part, int) or not 0 <= part <= 255 for part in rgb):
                raise ValueError('Home Assistant RGB values must be integers from 0 to 255')
            payload = {binding['param']: rgb}
        else:
            if transform == 'brightness':
                value = int(value * 255 / 100 + 0.5)
            elif transform == 'volume':
                value /= 100
            payload = {binding['param']: value}
            if transform == 'temperature_pair':
                other = 'target_temp_high' if binding['param'] == 'target_temp_low' else 'target_temp_low'
                paired = _numeric_read(item['attributes'].get(other))
                if paired is None:
                    raise ValueError('Home Assistant paired target temperature is unavailable')
                payload[other] = paired
                if payload['target_temp_low'] > payload['target_temp_high']:
                    raise ValueError('Home Assistant target temperature low exceeds high')
        return await self._call_service(item['entity_id'], service, payload)

    async def run_action(self, name: str, params: dict | None = None) -> dict:
        item, _, _, bindings = await self._snapshot()
        binding = bindings.get(name)
        if not binding or binding['cap']['kind'] != 'action':
            raise ValueError('Home Assistant action is not supported by the entity')
        params = {} if params is None else params
        if not isinstance(params, dict):
            raise ValueError('Home Assistant action parameters must be an object')
        schema = binding['schema']
        if set(params) - set(schema['properties']) or set(schema['required']) - set(params):
            raise ValueError('Home Assistant action parameters do not match the declared service')
        for key, value in params.items():
            spec = schema['properties'][key]
            kind = spec['type']
            valid = {'string': isinstance(value, str), 'boolean': isinstance(value, bool),
                     'object': isinstance(value, dict), 'number': isinstance(value, (int, float)) and not isinstance(value, bool)}[kind]
            if not valid or ('enum' in spec and value not in spec['enum']):
                raise ValueError('Invalid Home Assistant action parameter')
            if kind == 'number':
                number = _number(value)
                if ('minimum' in spec and number < spec['minimum']) or ('maximum' in spec and number > spec['maximum']):
                    raise ValueError('Home Assistant action parameter is outside the declared range')
        return await self._call_service(item['entity_id'], binding['service'], deepcopy(params))
