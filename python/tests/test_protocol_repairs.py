"""Offline regression tests for smart-home protocol adapters."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smart_home.adapters.kasa import _kasa_encode, _kasa_decode, _kasa_call, _yeelight_call, YeelightAdapter


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_kasa_rolling_xor_packet(self):
        self.assertEqual(_kasa_encode(b'{}'), bytes.fromhex('00000002d0ad'))
        self.assertEqual(_kasa_decode(bytes.fromhex('00000002d0ad')), b'{}')

    async def test_kasa_split_response(self):
        response = b'{"system":{"get_sysinfo":{"err_code":0}}}'
        packet = bytearray(len(response).to_bytes(4, 'big'))
        key = 0xab
        for byte in response:
            key ^= byte
            packet.append(key)
        async def handle(reader, writer):
            await reader.read(4096)
            for chunk in (packet[:2], packet[2:5], packet[5:]):
                writer.write(chunk)
                await writer.drain()
                await asyncio.sleep(0.005)
            writer.close()
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        original = asyncio.open_connection
        async def connect(ip, ignored):
            return await original(ip, port)
        try:
            with patch('smart_home.adapters.kasa.asyncio.open_connection', connect):
                result = await _kasa_call('127.0.0.1', {'system': {'get_sysinfo': {}}})
            self.assertEqual(result['system']['get_sysinfo']['err_code'], 0)
        finally:
            server.close()
            await server.wait_closed()

    async def test_yeelight_notification_and_error(self):
        async def handle(reader, writer):
            await reader.readline()
            writer.write(b'{"method":"props","params":{"power":"on"}}\r\n')
            writer.write(b'{"id":1,"error":{"code":-1,"message":"bad"}}\r\n')
            await writer.drain()
            writer.close()
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        original = asyncio.open_connection
        async def connect(ip, ignored):
            return await original(ip, port)
        try:
            with patch('smart_home.adapters.kasa.asyncio.open_connection', connect):
                with self.assertRaises(RuntimeError):
                    await _yeelight_call('127.0.0.1', 'set_power', ['on', 'smooth', 300])
        finally:
            server.close()
            await server.wait_closed()

    async def test_yeelight_color_temperature_payload(self):
        calls = []
        async def fake(ip, method, params):
            calls.append((method, params))
            return {'id': 1, 'result': ['on', '50', '4000', '1'] if method == 'get_prop' else ['ok']}
        adapter = YeelightAdapter(SimpleNamespace(ip='127.0.0.1', fields={'support': 'get_prop set_ct_abx'}), {'color_temp_range': [2000, 6000]})
        with patch('smart_home.adapters.kasa._yeelight_call', fake):
            await adapter.write_capability('color_temp', 4000)
        self.assertEqual(calls[-1], ('set_ct_abx', [4000, 'smooth', 300]))

class TuyaTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_schema_never_assumes_dp1(self):
        from smart_home.adapters.tuya import TuyaAdapter
        adapter = TuyaAdapter(SimpleNamespace(ip='127.0.0.1', credentials={}))
        self.assertEqual(await adapter.list_capabilities(), [])
        with self.assertRaises((ValueError, RuntimeError)):
            await adapter.write_capability('power', True)

    async def test_explicit_schema_and_private_credentials(self):
        from smart_home.adapters.tuya import TuyaAdapter
        class Device:
            def status(self):
                return {'dps': {'20': True}}
            def set_value(self, dp, value):
                self.sent = (dp, value)
                return {'dps': {dp: value}}
        fake = Device()
        dev = SimpleNamespace(ip='127.0.0.1', credentials={'local_key':'secret','device_id':'did'})
        adapter = TuyaAdapter(dev, {'dps': {'20': {'key': 'power', 'kind': 'bool', 'writable': True}}})
        with patch('smart_home.adapters.tuya._make_device', return_value=fake) as factory:
            self.assertTrue(await adapter.read_capability('power'))
            await adapter.write_capability('power', False)
        self.assertEqual(fake.sent, ('20', False))
        self.assertEqual(factory.call_args.args[:3], ('127.0.0.1', 'secret', '3.3'))
        self.assertEqual(factory.call_args.args[3], 'did')

    async def test_tuya_error_is_not_success(self):
        from smart_home.adapters.tuya import TuyaAdapter
        fake = SimpleNamespace(set_value=lambda dp, value: {'Error': 'offline', 'Err': '905'})
        adapter = TuyaAdapter(SimpleNamespace(ip='127.0.0.1'), {'dps': {'20': {'key':'power', 'kind':'bool', 'writable':True}}})
        adapter._dev = fake
        with self.assertRaises(RuntimeError):
            await adapter.write_capability('power', True)

class HttpFirmwareTests(unittest.IsolatedAsyncioTestCase):
    async def test_shelly_gen2_switch_and_position(self):
        from smart_home.adapters.shelly import ShellyAdapter
        class Response:
            status_code = 200
            def __init__(self, data): self.data = data
            def json(self): return self.data
        sent = []
        def post(url, payload):
            sent.append(payload)
            return Response({'id': 1, 'result': {'switch:0': {'output': True}, 'cover:0': {'current_pos': 50}} if payload['method'] == 'Shelly.GetStatus' else {}})
        a = ShellyAdapter(SimpleNamespace(ip='127.0.0.1'))
        with patch('smart_home.adapters.shelly._post_json', post):
            self.assertTrue(await a.read_capability('power'))
            await a.write_capability('position', 50)
        self.assertEqual(sent[-1]['params'], {'id': 0, 'pos': 50})

    async def test_wled_brightness_and_color(self):
        from smart_home.adapters.shelly import WledAdapter
        class Response:
            status_code = 200
            def json(self): return {'on': True, 'bri': 255, 'seg': [{'col': [[255,160,0,0], [0,0,0]]}]}
        sent = []
        def post(url, payload): sent.append(payload); return Response()
        a = WledAdapter(SimpleNamespace(ip='127.0.0.1'))
        with patch('smart_home.adapters.shelly._get', return_value=Response()), patch('smart_home.adapters.shelly._post_json', post):
            self.assertEqual(await a.read_capability('brightness'), 100)
            self.assertEqual(await a.read_capability('color'), '#ffa000')
            await a.write_capability('brightness', 100)
        self.assertEqual(sent[-1], {'bri': 255})

    async def test_tasmota_status_sts(self):
        from smart_home.adapters.shelly import TasmotaAdapter
        a = TasmotaAdapter(SimpleNamespace(ip='127.0.0.1'))
        async def cmd(command):
            self.assertEqual(command, 'Status 11')
            return {'StatusSTS': {'POWER': 'ON', 'Dimmer': 50}}
        with patch.object(a, '_cmd', cmd):
            self.assertEqual(await a.read_all(), {'power': True, 'brightness': 50})

class NetworkSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_router_and_nas_never_guess_reboot(self):
        from smart_home.adapters.network import RouterAdapter, NasAdapter
        for cls in (RouterAdapter, NasAdapter):
            adapter = cls(SimpleNamespace(ip='127.0.0.1'))
            caps = await adapter.list_capabilities()
            self.assertFalse(any(c['key'] == 'reboot' and c['writable'] for c in caps))
            with self.assertRaises((NotImplementedError, ValueError, RuntimeError)):
                await adapter.write_capability('reboot', True)

    def test_hidden_values_and_passwords_are_not_exposed(self):
        from smart_home.adapters.network import _parse_html
        data = _parse_html('<form><input name="password" type="password" value="secret"><input type="hidden" name="csrf" value="private"></form>')
        self.assertNotIn('secret', str(data))
        self.assertNotIn('private', str(data))

    async def test_onu_does_not_submit_unknown_login(self):
        from smart_home.adapters.network import OnuAdapter
        a = OnuAdapter(SimpleNamespace(ip='127.0.0.1'), {'onu_credentials': {'username':'name','password':'secret'}})
        async def scrape(): return {'status': 200}
        calls = []
        def post(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(status_code=200)
        with patch.object(a, '_scrape', scrape), patch.dict('sys.modules', {'requests': SimpleNamespace(post=post)}):
            result = await a.scrape()
        self.assertEqual(calls, [])
        self.assertNotIn('logged_in', result)

class UnsupportedTests(unittest.IsolatedAsyncioTestCase):
    async def test_televisions_do_not_expose_unimplemented_writes(self):
        from smart_home.adapters.tv import SamsungTvAdapter, LgTvAdapter, VizioTvAdapter
        for cls in (SamsungTvAdapter, LgTvAdapter, VizioTvAdapter):
            adapter = cls(SimpleNamespace(ip='127.0.0.1'))
            self.assertFalse(any(c.get('writable') for c in await adapter.list_capabilities()))
            with self.assertRaises((ValueError, NotImplementedError)):
                await adapter.write_capability('power', True)

    async def test_lifx_does_not_send_unverified_packet(self):
        from smart_home.adapters.hue import LifxAdapter
        adapter = LifxAdapter(SimpleNamespace(ip='127.0.0.1'))
        self.assertFalse(any(c.get('writable') for c in await adapter.list_capabilities()))
        with self.assertRaises((ValueError, NotImplementedError)):
            await adapter.write_capability('power', True)

    async def test_onvif_unverified_reboot_unavailable(self):
        from smart_home.adapters.dlna import OnvifAdapter
        adapter = OnvifAdapter(SimpleNamespace(ip='127.0.0.1', fields={}))
        self.assertFalse(any(c['key'] == 'reboot' for c in await adapter.list_capabilities()))
        with self.assertRaises((ValueError, NotImplementedError)):
            await adapter.reboot()

class HueTests(unittest.IsolatedAsyncioTestCase):
    async def test_pair_generated_username_and_api_error(self):
        from smart_home.adapters.hue import HueAdapter
        class Response:
            status_code = 200
            text = ''
            def __init__(self, data): self.data = data
            def json(self): return self.data
        sent = []
        def post(url, json, **kwargs):
            sent.append(json)
            return Response([{'success': {'username': 'generated'}}])
        def put(url, json, **kwargs): return Response([{'error': {'type': 7}}])
        a = HueAdapter(SimpleNamespace(ip='127.0.0.1'), {'hue_light_id': '1'})
        with patch.dict('sys.modules', {'requests': SimpleNamespace(post=post, put=put, get=lambda *args, **kwargs: Response({'1': {'state': {'on': False}}}))}):
            with self.assertRaises(RuntimeError):
                await a.write_capability('power', True)
        self.assertEqual(a.config['hue_username'], 'generated')
        self.assertNotIn('username', sent[0])

class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_hue_reads_and_writes_same_configured_light(self):
        from smart_home.adapters.hue import HueAdapter
        a = HueAdapter(SimpleNamespace(ip='127.0.0.1'), {'hue_light_id':'7'})
        calls = []
        async def api(method, path, payload=None):
            calls.append((method, path, payload))
            if method == 'GET': return {'1': {'state': {'on': False}}, '7': {'state': {'on': True}}}
            return [{'success': {'/lights/7/state/on': False}}]
        with patch.object(a, '_api', api):
            self.assertTrue(await a.read_capability('power'))
            await a.write_capability('power', False)
        self.assertEqual(calls[-1][1], '/lights/7/state')
        self.assertEqual(calls[-1][2], {'on': False})

    async def test_wled_color_text_write(self):
        from smart_home.adapters.shelly import WledAdapter
        class Response:
            status_code=200
        captured=[]
        def post(url, payload): captured.append(payload); return Response()
        a=WledAdapter(SimpleNamespace(ip='127.0.0.1'))
        with patch('smart_home.adapters.shelly._post_json', post):
            await a.write_capability('color', '#ffa000')
        self.assertEqual(captured[0], {'seg':[{'col':[[255,160,0]]}]})

    async def test_tasmota_read_capability_status(self):
        from smart_home.adapters.shelly import TasmotaAdapter
        a=TasmotaAdapter(SimpleNamespace(ip='127.0.0.1'))
        async def cmd(command): return {'StatusSTS': {'POWER':'ON', 'Dimmer':50}}
        with patch.object(a, '_cmd', cmd):
            self.assertTrue(await a.read_capability('power'))
            self.assertEqual(await a.read_capability('brightness'), 50)

class HueContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_hue_selects_same_discovered_light_and_validates_xy(self):
        from smart_home.adapters.hue import HueAdapter
        a=HueAdapter(SimpleNamespace(ip='127.0.0.1'))
        calls=[]
        async def api(method, path, payload=None):
            calls.append((method,path,payload))
            return {'7': {'state': {'on': True, 'xy': [0.2, 0.3]}}} if method == 'GET' else [{'success': {path: True}}]
        with patch.object(a, '_api', api):
            await a.write_capability('color', '0.2,0.3')
        self.assertEqual(calls[-1], ('PUT','/lights/7/state',{'xy':[0.2,0.3]}))

class MetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_wled_capabilities_match_values(self):
        from smart_home.adapters.shelly import WledAdapter
        class Response:
            status_code=200
            def json(self): return {'on':True,'bri':255,'seg':[{'col':[[255,160,0]]}]}
        a=WledAdapter(SimpleNamespace(ip='127.0.0.1'))
        with patch('smart_home.adapters.shelly._get', return_value=Response()):
            caps={c['key']: c for c in await a.list_capabilities()}
            result=await a.read_all()
        self.assertEqual(caps['brightness']['range'], [0,100,1])
        self.assertEqual(caps['color']['kind'], 'text')
        self.assertEqual(result, {'power':True,'brightness':100,'color':'#ffa000'})

    async def test_shelly_gen1_relay_write(self):
        from smart_home.adapters.shelly import ShellyAdapter
        class Response:
            status_code=200
            def json(self): return {'ison': False}
        sent=[]
        async def rpc(*args): raise RuntimeError('RPC unavailable')
        def get(url): sent.append(url); return Response()
        a=ShellyAdapter(SimpleNamespace(ip='127.0.0.1'), {'shelly_gen':1})
        with patch.object(a, '_rpc', rpc), patch('smart_home.adapters.shelly._get', get):
            await a.write_capability('power', False)
        self.assertEqual(sent, ['http://127.0.0.1/relay/0?turn=off'])

    async def test_kasa_read_all_uses_capability_keys(self):
        from smart_home.adapters.kasa import KasaAdapter
        a=KasaAdapter(SimpleNamespace(ip='127.0.0.1'))
        async def sysinfo(): return {'relay_state':1}
        with patch.object(a, '_sysinfo', sysinfo):
            self.assertEqual(await a.read_all(), {'power':True})

class FinalContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_shelly_gen2_advertises_observed_components_and_reads(self):
        from smart_home.adapters.shelly import ShellyAdapter
        a=ShellyAdapter(SimpleNamespace(ip='127.0.0.1'))
        async def rpc(method, params=None): return {'switch:0': {'output': True}, 'cover:0': {'current_pos': 73}}
        with patch.object(a, '_rpc', rpc):
            caps={c['key']:c for c in await a.list_capabilities()}
            values=await a.read_all()
        self.assertEqual(set(caps), {'power','position'})
        self.assertEqual(values, {'power':True,'position':73})

    async def test_shelly_action_sends_dict(self):
        from smart_home.adapters.shelly import ShellyAdapter
        a=ShellyAdapter(SimpleNamespace(ip='127.0.0.1')); calls=[]
        async def rpc(method, params=None): calls.append((method, params))
        with patch.object(a, '_rpc', rpc): await a.run_action('Switch.Set', {'id':0,'on':True})
        self.assertEqual(calls, [('Switch.Set', {'id':0,'on':True})])

    async def test_tasmota_only_advertises_observed(self):
        from smart_home.adapters.shelly import TasmotaAdapter
        a=TasmotaAdapter(SimpleNamespace(ip='127.0.0.1'))
        async def cmd(command): return {'StatusSTS': {'POWER':'ON', 'Dimmer':50}}
        with patch.object(a, '_cmd', cmd):
            caps={c['key']:c for c in await a.list_capabilities()}
        self.assertEqual(set(caps), {'power','brightness'})
        self.assertTrue(caps['brightness']['writable'])

    async def test_hue_light_only_advertises_supported_state(self):
        from smart_home.adapters.hue import HueAdapter
        a=HueAdapter(SimpleNamespace(ip='127.0.0.1'), {'hue_light_id':'2'})
        async def api(method,path,payload=None):
            return {'2': {'state': {'on':True}, 'type':'On/Off plug-in unit'}}
        with patch.object(a,'_api',api):
            self.assertEqual([c['key'] for c in await a.list_capabilities()],['power'])
            with self.assertRaises(ValueError): await a.write_capability('brightness',10)

    async def test_tasmota_rejects_unconfirmed_write(self):
        from smart_home.adapters.shelly import TasmotaAdapter
        a=TasmotaAdapter(SimpleNamespace(ip='127.0.0.1'))
        async def cmd(command): return {}
        with patch.object(a,'_cmd',cmd):
            with self.assertRaises(RuntimeError): await a.write_capability('power',True)

    async def test_tuya_close_closes_client(self):
        from smart_home.adapters.tuya import TuyaAdapter
        a=TuyaAdapter(SimpleNamespace(ip='127.0.0.1'))
        class Dev:
            closed=False
            def close(self): self.closed=True
        dev=Dev(); a._dev=dev
        await a.close()
        self.assertTrue(dev.closed)
        self.assertIsNone(a._dev)

    async def test_esphome_is_explicitly_unsupported(self):
        from smart_home.adapters.shelly import EspHomeAdapter
        a=EspHomeAdapter(SimpleNamespace(ip='127.0.0.1', category='light'))
        self.assertEqual(await a.list_capabilities(), [])
        with self.assertRaises((ValueError, NotImplementedError)): await a.write_capability('power',True)

    async def test_onvif_uses_discovered_address_and_detects_fault(self):
        from smart_home.adapters.dlna import OnvifAdapter
        a=OnvifAdapter(SimpleNamespace(ip='127.0.0.1'))
        async def probe(ip): return {'service':'http://127.0.0.1/onvif/device_service'}
        async def soap(service, action, body):
            self.assertIn('GetDeviceInformation', body)
            return '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><s:Fault/></s:Body></s:Envelope>'
        with patch('smart_home.adapters.dlna._onvif_probe',probe), patch.object(a,'_soap',soap):
            with self.assertRaises(RuntimeError): await a.scrape()

if __name__ == '__main__':
    unittest.main()
