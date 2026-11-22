"""Offline Home Assistant REST contract tests; no Home Assistant is contacted."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    HA = importlib.import_module('smart_home.adapters.home_assistant').HomeAssistantAdapter
except ModuleNotFoundError:
    HA = None

TOKEN = 'offline-test-secret-never-echo'
SERVICE_NAMES = {
    'light': ['turn_on', 'turn_off'], 'switch': ['turn_on', 'turn_off'],
    'fan': ['turn_on', 'turn_off', 'set_percentage', 'oscillate', 'set_direction', 'set_preset_mode'],
    'climate': ['turn_on', 'turn_off', 'set_temperature', 'set_hvac_mode', 'set_humidity', 'set_fan_mode', 'set_preset_mode', 'set_swing_mode', 'set_swing_horizontal_mode'],
    'cover': ['open_cover', 'close_cover', 'stop_cover', 'set_cover_position', 'set_cover_tilt_position'],
    'media_player': ['turn_on', 'turn_off', 'volume_set', 'volume_mute', 'select_source', 'media_play', 'media_pause', 'media_stop', 'media_next_track', 'media_previous_track', 'play_media'],
    'vacuum': ['start', 'pause', 'stop', 'return_to_base', 'locate', 'clean_spot', 'set_fan_speed'],
    'lock': ['lock', 'unlock', 'open'], 'humidifier': ['turn_on', 'turn_off', 'set_humidity', 'set_mode'],
    'select': ['select_option'], 'number': ['set_value'], 'button': ['press'],
    'scene': ['turn_on'], 'script': ['turn_on', 'turn_off'],
}


def services():
    data = [{'domain': domain, 'services': {name: {} for name in names}} for domain, names in SERVICE_NAMES.items()]
    next(x for x in data if x['domain'] == 'lock')['services']['unlock'] = {'fields': {'code': {'selector': {'text': {}}, 'required': False}}}
    next(x for x in data if x['domain'] == 'media_player')['services']['play_media'] = {'fields': {
        'media_content_id': {'required': True, 'selector': {'text': {}}},
        'media_content_type': {'required': True, 'selector': {'text': {}}},
    }}
    return data


def state(entity_id, value='on', **attributes):
    return {'entity_id': entity_id, 'state': value, 'attributes': attributes}


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(HA, 'Home Assistant adapter has not been implemented')

    def make(self, item=None, service_data=None):
        entity = item['entity_id'] if item else ''
        device = SimpleNamespace(id='home_assistant:' + (entity or 'main'), ip='', mac='',
                                 fields={'entity_id': entity} if entity else {}, credentials={'token': TOKEN})
        adapter = HA(device, {'base_url': 'http://ha.invalid:8123'})
        async def response(method, path, payload=None):
            if path == '/api/services':
                return copy.deepcopy(service_data if service_data is not None else services())
            if path == '/api/config':
                return {'unit_system': {'temperature': '°F'}}
            if path == '/api/states':
                return [copy.deepcopy(item)] if item else []
            if path.startswith('/api/states/') and item:
                return copy.deepcopy(item)
            if path.startswith('/api/services/') and method == 'POST':
                return []
            raise AssertionError((method, path, payload))
        adapter._request = AsyncMock(side_effect=response)
        return adapter

    async def caps(self, adapter):
        return {item['key']: item for item in await adapter.list_capabilities()}

    async def posted(self, adapter):
        calls = [c.args for c in adapter._request.await_args_list if c.args[0] == 'POST']
        return calls[-1]

    async def test_entity_identity_overrides_inherited_hub_configuration(self):
        a = self.make(state('light.kitchen', supported_color_modes=['onoff']))
        a.config['entity_id'] = 'light.desk'
        self.assertEqual(a._entity_id(), 'light.kitchen')
        await a.write_capability('power', True)
        self.assertEqual((await self.posted(a))[2]['entity_id'], 'light.kitchen')

    async def test_hub_discovery_has_only_public_identity_and_reads(self):
        adapter = self.make()
        rows = [state('light.desk', friendly_name='Desk', supported_color_modes=['onoff']),
                state('sensor.outdoor', '12.5', friendly_name='Outside', device_class='temperature', unit_of_measurement='°C'),
                state('custom.unknown', 'idle'), state('switch.offline', 'unavailable')]
        async def response(method, path, payload=None):
            self.assertEqual(method, 'GET')
            return services() if path == '/api/services' else rows
        adapter._request.side_effect = response
        self.assertEqual(await adapter.list_capabilities(), [])
        await adapter.probe()
        result = await adapter.discover_entities()
        self.assertEqual({x['entity_id'] for x in result}, {x['entity_id'] for x in rows})
        lamp = next(x for x in result if x['entity_id'] == 'light.desk')
        self.assertEqual(lamp['id'], 'home_assistant:light.desk')
        self.assertEqual(lamp['name'], 'Desk')
        self.assertEqual(lamp['category'], 'light')
        self.assertEqual(lamp['fields']['entity_id'], 'light.desk')
        self.assertFalse(next(x for x in result if x['entity_id'] == 'switch.offline')['online'])
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn('base_url', json.dumps(result))

    async def test_light_percent_kelvin_rgb_and_exact_target(self):
        a = self.make(state('light.desk', supported_color_modes=['rgb', 'color_temp'], brightness=128,
                            rgb_color=[1, 2, 3], color_temp_kelvin=3000, min_color_temp_kelvin=2200, max_color_temp_kelvin=6500))
        caps = await self.caps(a)
        self.assertEqual(caps['brightness']['range'], [0, 100, 1])
        self.assertEqual(caps['color_temp']['range'], [2200, 6500, 1])
        self.assertEqual(caps['color_temp']['unit'], 'K')
        values = await a.read_all()
        self.assertAlmostEqual(values['brightness'], 128 / 255 * 100, places=2)
        self.assertEqual(values['color_temp'], 3000)
        self.assertEqual(set(values), {key for key, cap in caps.items() if cap['readable']})
        for percent, raw in [(100, 255), (50, 128), (0, 0)]:
            self.assertTrue((await a.write_capability('brightness', percent))['ok'])
            self.assertEqual(await self.posted(a), ('POST', '/api/services/light/turn_on', {'entity_id': 'light.desk', 'brightness': raw}))
        await a.write_capability('color_temp', 4000)
        self.assertEqual((await self.posted(a))[2]['color_temp_kelvin'], 4000)
        await a.write_capability('color', '10,20,30')
        self.assertEqual((await self.posted(a))[2]['rgb_color'], [10, 20, 30])
        with self.assertRaises(ValueError):
            await a.write_capability('color_temp', 100)

    async def test_entity_features_and_service_directory_both_gate_writes(self):
        data = [{'domain': 'light', 'services': {'turn_off': {}}}]
        a = self.make(state('light.simple', brightness=100, supported_color_modes=['onoff']), data)
        caps = await self.caps(a)
        self.assertFalse(caps['power']['writable'])
        self.assertFalse(caps['brightness']['writable'])
        with self.assertRaises((ValueError, NotImplementedError)):
            await a.write_capability('brightness', 80)
        self.assertFalse(any(c.args[0] == 'POST' for c in a._request.await_args_list))

    async def test_fan_percentage_modes_oscillation_and_feature_gate(self):
        a = self.make(state('fan.office', supported_features=63, percentage=50, percentage_step=25,
                            oscillating=True, direction='forward', preset_mode='auto', preset_modes=['auto', 'sleep']))
        caps = await self.caps(a)
        self.assertEqual(caps['fan_speed']['range'], [0, 100, 25])
        self.assertEqual((await a.read_all())['swing'], True)
        await a.write_capability('fan_speed', 75)
        self.assertEqual((await self.posted(a))[1:], ('/api/services/fan/set_percentage', {'entity_id': 'fan.office', 'percentage': 75}))
        await a.write_capability('swing', 'false')
        self.assertIs((await self.posted(a))[2]['oscillating'], False)
        await a.write_capability('mode', 'sleep')
        self.assertEqual((await self.posted(a))[2]['preset_mode'], 'sleep')
        b = self.make(state('fan.fixed', supported_features=0, percentage=50))
        self.assertFalse((await self.caps(b))['fan_speed']['writable'])

    async def test_climate_native_ranges_modes_and_paired_temperature(self):
        a = self.make(state('climate.room', 'heat', supported_features=1 | 2 | 4 | 8 | 16 | 32 | 128 | 256,
                            temperature=70, current_temperature=68, min_temp=50, max_temp=90, target_temp_step=0.5,
                            temperature_unit='°F', target_temp_low=65, target_temp_high=75,
                            hvac_modes=['off', 'heat', 'cool', 'heat_cool'], fan_mode='auto', fan_modes=['auto', 'low'],
                            preset_mode='eco', preset_modes=['eco', 'away'], swing_mode='off', swing_modes=['off', 'on'],
                            humidity=45, min_humidity=30, max_humidity=80, current_humidity=42))
        caps = await self.caps(a)
        self.assertEqual(caps['target_temp']['range'], [50, 90, 0.5])
        self.assertEqual(caps['target_temp']['unit'], '°F')
        self.assertEqual((await a.read_all())['mode'], 'heat')
        await a.write_capability('target_temp', 72.5)
        self.assertEqual((await self.posted(a))[2]['temperature'], 72.5)
        await a.write_capability('target_temp_low', 66)
        self.assertEqual((await self.posted(a))[2], {'entity_id': 'climate.room', 'target_temp_low': 66, 'target_temp_high': 75})
        with self.assertRaises(ValueError):
            await a.write_capability('mode', 'invented')
        with self.assertRaises(ValueError):
            await a.write_capability('target_temp_low', 80)

    async def test_cover_position_and_actions_need_features(self):
        a = self.make(state('cover.blind', 'open', supported_features=15, current_position=35))
        caps = await self.caps(a)
        self.assertEqual(caps['position']['range'], [0, 100, 1])
        self.assertEqual(caps['open']['kind'], 'action')
        self.assertFalse(caps['open']['readable'])
        await a.write_capability('position', 80)
        self.assertEqual((await self.posted(a))[2], {'entity_id': 'cover.blind', 'position': 80})
        self.assertTrue((await a.run_action('stop'))['ok'])
        self.assertEqual((await self.posted(a))[1], '/api/services/cover/stop_cover')
        b = self.make(state('cover.basic', supported_features=0, current_position=30))
        self.assertNotIn('open', await self.caps(b))
        self.assertFalse((await self.caps(b))['position']['writable'])

    async def test_media_player_volume_source_and_parameterized_action(self):
        a = self.make(state('media_player.tv', 'playing', supported_features=4 | 8 | 16 | 32 | 128 | 256 | 512 | 2048 | 4096 | 16384 | 1,
                            volume_level=0.25, is_volume_muted=False, source='HDMI 1', source_list=['HDMI 1', 'TV']))
        caps = await self.caps(a)
        self.assertEqual((await a.read_all())['volume'], 25)
        self.assertEqual(caps['play_media']['kind'], 'action')
        self.assertIn('media_content_id', caps['play_media']['params_schema']['properties'])
        await a.write_capability('volume', 50)
        self.assertEqual((await self.posted(a))[2]['volume_level'], 0.5)
        await a.write_capability('input_source', 'TV')
        self.assertEqual((await self.posted(a))[2]['source'], 'TV')
        with self.assertRaises(ValueError):
            await a.run_action('play_media', {})
        await a.run_action('play_media', {'media_content_id': 'demo', 'media_content_type': 'music'})
        self.assertEqual((await self.posted(a))[2]['entity_id'], 'media_player.tv')
        with self.assertRaises(ValueError):
            await a.run_action('play', {'entity_id': 'media_player.other'})

    async def test_vacuum_actions_and_string_fan_speed(self):
        a = self.make(state('vacuum.robot', 'docked', supported_features=8192 | 4 | 8 | 16 | 32 | 512 | 1024,
                            fan_speed='quiet', fan_speed_list=['quiet', 'turbo']))
        caps = await self.caps(a)
        self.assertEqual(caps['clean']['kind'], 'action')
        self.assertEqual(caps['charge']['kind'], 'action')
        self.assertNotIn('power', caps)
        await a.run_action('charge')
        self.assertEqual((await self.posted(a))[1], '/api/services/vacuum/return_to_base')
        await a.write_capability('fan_speed', 'turbo')
        self.assertEqual((await self.posted(a))[2]['fan_speed'], 'turbo')

    async def test_lock_actions_use_declared_code_without_exposing_it(self):
        a = self.make(state('lock.front', 'locked', supported_features=1, code_format='^\\d{4}$'))
        caps = await self.caps(a)
        self.assertEqual(caps['unlock']['kind'], 'action')
        self.assertEqual((await a.read_all())['status'], 'locked')
        result = await a.run_action('unlock', {'code': '1234'})
        self.assertEqual((await self.posted(a))[2], {'entity_id': 'lock.front', 'code': '1234'})
        self.assertNotIn('1234', json.dumps(result))

    async def test_humidifier_native_humidity_and_mode(self):
        a = self.make(state('humidifier.room', supported_features=1, humidity=50, current_humidity=45,
                            min_humidity=35, max_humidity=75, target_humidity_step=5, mode='auto', available_modes=['auto', 'sleep']))
        caps = await self.caps(a)
        self.assertEqual(caps['target_humidity']['range'], [35, 75, 5])
        await a.write_capability('target_humidity', 60)
        self.assertEqual((await self.posted(a))[1], '/api/services/humidifier/set_humidity')
        await a.write_capability('mode', 'sleep')
        self.assertEqual((await self.posted(a))[2]['mode'], 'sleep')

    async def test_select_number_and_trigger_entities(self):
        a = self.make(state('select.program', 'eco', options=['eco', 'normal']))
        self.assertEqual((await self.caps(a))['mode']['value_list'][0]['value'], 'eco')
        await a.write_capability('mode', 'normal')
        self.assertEqual((await self.posted(a))[2]['option'], 'normal')
        b = self.make(state('number.setpoint', '12.5', min=5, max=20, step=0.5, unit_of_measurement='V'))
        self.assertEqual((await self.caps(b))['value']['range'], [5, 20, 0.5])
        self.assertEqual((await b.read_all())['value'], 12.5)
        await b.write_capability('value', 15)
        self.assertEqual((await self.posted(b))[1], '/api/services/number/set_value')
        for domain, action, service in [('button', 'press', 'press'), ('scene', 'activate', 'turn_on'), ('script', 'run', 'turn_on')]:
            with self.subTest(domain=domain):
                c = self.make(state(domain + '.test', 'unknown'))
                self.assertEqual((await self.caps(c))[action]['kind'], 'action')
                await c.run_action(action)
                self.assertEqual((await self.posted(c))[1], f'/api/services/{domain}/{service}')

    async def test_sensors_unknown_and_unavailable_never_guess_writes(self):
        a = self.make(state('sensor.temp', '21.6', device_class='temperature', unit_of_measurement='°C'))
        self.assertEqual((await a.read_all())['temperature'], 21.6)
        self.assertEqual((await self.caps(a))['temperature']['unit'], '°C')
        for item in [state('custom.foo', 'idle', supported_features=65535), state('light.offline', 'unavailable', supported_color_modes=['brightness'])]:
            b = self.make(item)
            self.assertFalse(any(x['writable'] for x in await b.list_capabilities()))
            with self.assertRaises((ValueError, NotImplementedError)):
                await b.write_capability('power', True)
        c = self.make(state('binary_sensor.door', 'off', device_class='door'))
        self.assertIs((await c.read_all())['value'], False)


    async def test_discovery_uses_known_categories_and_unavailable_trigger_is_read_only(self):
        a = self.make()
        a._request.return_value = [state('lock.front'), state('media_player.audio', device_class='speaker')]
        a._request.side_effect = None
        rows = await a.discover_entities()
        self.assertEqual(rows[0]['category'], 'lock')
        self.assertEqual(rows[1]['category'], 'speaker')
        trigger = self.make(state('scene.first', 'unavailable'))
        self.assertNotIn('activate', await self.caps(trigger))

    async def test_kelvin_bounds_and_step_and_unknown_values_are_not_guessed(self):
        a = self.make(state('light.no_bounds', color_temp_kelvin=3000, supported_color_modes=['color_temp']))
        self.assertFalse((await self.caps(a))['color_temp']['writable'])
        number = self.make(state('number.stepped', '10', min=5, max=20, step=0.5))
        with self.assertRaises(ValueError):
            await number.write_capability('value', 10.3)
        self.assertFalse(any(c.args[0] == 'POST' for c in number._request.await_args_list))

    async def test_ha_temperature_unit_is_read_from_configuration_when_absent(self):
        a = self.make(state('climate.furnace', 'heat', supported_features=1, temperature=70, min_temp=45, max_temp=95))
        caps = await self.caps(a)
        self.assertEqual(caps['target_temp']['unit'], '°F')

    async def test_list_style_directory_is_supported_and_invalid_json_service_is_failure(self):
        a = self.make(state('switch.desk'), [{'domain': 'switch', 'services': ['turn_on', 'turn_off']}])
        self.assertTrue((await self.caps(a))['power']['writable'])
        original = a._request.side_effect
        async def response(method, path, payload=None):
            if method == 'POST':
                return [{'error': 'do not claim success'}]
            return await original(method, path, payload)
        a._request.side_effect = response
        with self.assertRaises(RuntimeError):
            await a.write_capability('power', True)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(HA, 'Home Assistant adapter has not been implemented')
        self.device = SimpleNamespace(id='home_assistant:light.desk', fields={'entity_id': 'light.desk'}, credentials={'token': TOKEN})
        self.adapter = HA(self.device, {'base_url': 'http://ha.invalid:8123/'})

    async def test_request_offloads_blocking_io_and_sets_bearer_once(self):
        thread = threading.get_ident()
        def request(method, url, **kwargs):
            self.assertNotEqual(thread, threading.get_ident())
            self.assertEqual(url, 'http://ha.invalid:8123/api/states')
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer ' + TOKEN)
            self.assertFalse(kwargs['allow_redirects'])
            return SimpleNamespace(status_code=200, json=lambda: [])
        with patch('requests.request', side_effect=request) as call:
            self.assertEqual(await self.adapter._request('GET', '/api/states'), [])
            self.assertEqual(call.call_count, 1)

    async def test_http_errors_json_errors_and_transport_errors_are_sanitized(self):
        responses = [SimpleNamespace(status_code=401, json=lambda: {'message': TOKEN}),
                     SimpleNamespace(status_code=404, json=lambda: {'error': TOKEN}),
                     SimpleNamespace(status_code=200, json=lambda: {'error': TOKEN}),
                     SimpleNamespace(status_code=200, json=lambda: (_ for _ in ()).throw(ValueError(TOKEN)))]
        for response in responses:
            with self.subTest(status=response.status_code), patch('requests.request', return_value=response):
                with self.assertRaises(RuntimeError) as caught:
                    await self.adapter._request('GET', '/api/states')
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertNotIn('Bearer', str(caught.exception))
        with patch('requests.request', side_effect=RuntimeError('Authorization: Bearer ' + TOKEN)):
            with self.assertRaises(RuntimeError) as caught:
                await self.adapter._request('GET', '/api/states')
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)

    async def test_configuration_and_entity_validation_never_send_unsafe_requests(self):
        for config in [{}, {'base_url': 'ftp://host'}, {'base_url': 'http://user:secret@host'}, {'base_url': 'http://host?token=secret'}]:
            with self.subTest(config=config), patch('requests.request') as call:
                a = HA(self.device, config)
                with self.assertRaises(ValueError):
                    await a._request('GET', '/api/states')
                call.assert_not_called()
        a = HA(SimpleNamespace(id='home_assistant:main', credentials={}), {'base_url': 'http://ha.invalid'})
        with patch('requests.request') as call:
            with self.assertRaises(ValueError):
                await a._request('GET', '/api/states')
            call.assert_not_called()

    async def test_empty_service_response_success_but_401_never_success(self):
        item = state('light.desk', supported_color_modes=['onoff'])
        def request(method, url, **kwargs):
            if method == 'GET':
                return SimpleNamespace(status_code=200, json=lambda: services() if url.endswith('/services') else item)
            return SimpleNamespace(status_code=200, json=lambda: [])
        with patch('requests.request', side_effect=request):
            self.assertTrue((await self.adapter.write_capability('power', True))['ok'])
        with patch('requests.request', return_value=SimpleNamespace(status_code=401, json=lambda: {'message': TOKEN})):
            with self.assertRaises(RuntimeError):
                await self.adapter.write_capability('power', False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
