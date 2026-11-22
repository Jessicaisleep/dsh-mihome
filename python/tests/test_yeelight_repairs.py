import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from smart_home.adapters.kasa import YeelightAdapter, KasaAdapter, _yeelight_call, _kasa_call

class YeelightTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_and_metadata(self):
        async def call(ip,method,params):
            self.assertEqual(method,'get_prop')
            return {'result':['on','50','4000','16711680']}
        a=YeelightAdapter(SimpleNamespace(ip='test',fields={'support':'get_prop set_power set_bright set_ct_abx set_rgb'}), {'color_temp_range':[2700,6500]})
        with patch('smart_home.adapters.kasa._yeelight_call',call):
            caps={c['key']:c for c in await a.list_capabilities()}
            values=await a.read_all()
        self.assertEqual(values,{'power':True,'brightness':50,'color_temp':4000,'color':16711680})
        self.assertEqual(caps['brightness']['range'],[1,100,1]); self.assertEqual(caps['color']['range'],[0,16777215,1]); self.assertEqual(caps['color_temp']['range'],[2700,6500,1])
        self.assertTrue(all(c['writable'] for c in caps.values()))

    async def test_unknown_properties_and_no_guessed_writes(self):
        async def call(ip,method,params): return {'result':['on','', '', '']}
        a=YeelightAdapter(SimpleNamespace(ip='test',fields={}))
        with patch('smart_home.adapters.kasa._yeelight_call',call):
            caps=await a.list_capabilities()
        self.assertEqual([x['key'] for x in caps],['power']); self.assertFalse(caps[0]['writable'])
        with patch('smart_home.adapters.kasa._yeelight_call',call):
            with self.assertRaises(ValueError): await a.write_capability('power',True)

    async def test_ct_success_payload(self):
        a=YeelightAdapter(SimpleNamespace(ip='test', fields={'support':'get_prop set_ct_abx'}), {'color_temp_range':[2000,6000]})
        calls=[]
        async def call(ip,method,params):
            calls.append((method,params))
            return {'result':['on','50','4000','1']} if method == 'get_prop' else {'result':['ok']}
        with patch('smart_home.adapters.kasa._yeelight_call',call):
            await a.write_capability('color_temp',4000)
        self.assertEqual(calls[-1],('set_ct_abx',[4000,'smooth',300]))

    async def test_reject_invalid_values_and_bad_ack(self):
        a=YeelightAdapter(SimpleNamespace(ip='test',fields={'support':'get_prop set_power set_bright set_ct_abx set_rgb'}), {'color_temp_range':[2700,6500]})
        async def call(ip,method,params): return {'result':['on','50','4000','1']} if method=='get_prop' else {'result':['bad']}
        with patch('smart_home.adapters.kasa._yeelight_call',call):
            for key,value in [('color',-1),('color',16777216),('color','bad'),('brightness',0),('brightness',1.5),('color_temp',2000)]:
                with self.assertRaises(ValueError): await a.write_capability(key,value)
            with self.assertRaises(RuntimeError): await a.write_capability('power',True)
            with self.assertRaises((NotImplementedError, ValueError)): await a.run_action('arbitrary')

class KasaTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_relay_no_fake_power(self):
        a=KasaAdapter(SimpleNamespace(ip='test'))
        async def info(): return {'model':'strip'}
        with patch.object(a,'_sysinfo',info):
            self.assertEqual(await a.list_capabilities(),[])
            self.assertIsNone(await a.read_capability('power'))
            with self.assertRaises(ValueError): await a.write_capability('power',True)

    async def test_missing_or_error_ack_rejected(self):
        a=KasaAdapter(SimpleNamespace(ip='test'))
        async def info(): return {'relay_state':0}
        async def call(ip,payload): return {'system': {'set_relay_state': {}}}
        with patch.object(a,'_sysinfo',info), patch('smart_home.adapters.kasa._kasa_call',call):
            with self.assertRaises(RuntimeError): await a.write_capability('power',True)

    async def test_connection_timeout(self):
        async def blocked(*args): await asyncio.Future()
        with patch('smart_home.adapters.kasa.asyncio.open_connection',blocked):
            with self.assertRaises(TimeoutError): await _kasa_call('test',{},timeout=.01)
            with self.assertRaises(TimeoutError): await _yeelight_call('test','get_prop',[],timeout=.01)

if __name__=='__main__': unittest.main()
