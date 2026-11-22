import asyncio
import hashlib
import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smart_home.adapters.mihome import MiHomeCloudAdapter, MiIOAdapter


class CloudTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_spec_and_dispatch(self):
        class Cloud:
            def device_detail(self, did):
                return {'props': [
                    {'name':'a.on','type':'bool','readable':True,'writable':True},
                    {'name':'b.on','type':'bool','readable':True,'writable':False},
                    {'name':'mode','type':'uint8','readable':True,'writable':True,'range':[1,3,1], 'value_list':[{'value':1,'description':'auto'}], 'unit':'x'},
                    {'name':'unknown','type':'float','readable':True,'writable':False,'range':[0,10,0.5]}],
                    'actions':[{'name':'fan.start','desc':'start'}]}
            def read_props(self,did,names): return {n:i for i,n in enumerate(names)}
            def write_prop(self,did,name,value): self.write=(name,value)
            def run_action(self,did,name,params): self.action=(name,params)
        svc=Cloud(); a=MiHomeCloudAdapter(SimpleNamespace(did='123'), {'cloud':svc})
        caps=await a.list_capabilities(); by={c['key']:c for c in caps}
        self.assertEqual(set(by), {'a.on','b.on','mode','unknown','fan.start'})
        self.assertEqual(by['mode']['kind'], 'enum')
        self.assertEqual(by['mode']['range'], [1,3,1])
        self.assertEqual(by['mode']['unit'], 'x')
        self.assertEqual(by['fan.start']['kind'], 'action')
        self.assertEqual((await a.read_all())['b.on'],1)
        await a.write_capability('mode', 1)
        self.assertEqual(svc.write, ('mode',1))
        await a.run_action('fan.start', {'v':[1,2]})
        self.assertEqual(svc.action, ('fan.start', {'v':[1,2]}))
        with self.assertRaises(ValueError): await a.write_capability('b.on',True)
        with self.assertRaises(ValueError): await a.run_action('bogus')

    async def test_manager_power_uses_confirmed_cloud_power_path(self):
        from smart_home.manager import SmartHomeManager
        class Cloud:
            def power_state(self, did):
                self.read_did = did
                return False
            def set_power_state(self, did, state):
                self.write = (did, state)
        cloud = Cloud()
        manager = SmartHomeManager({'cloud': cloud})
        manager._scan = {'devices': [{'id':'mihome:123','did':'123','protocol':'mihome','fields':{}}]}
        result = await manager.control('mihome:123', 'power')
        self.assertIs(result['ok'], True)
        self.assertIs(result['on'], True)
        self.assertEqual(cloud.write, ('123', True))
        cloud.power_state = lambda did: None
        with self.assertRaises(ValueError):
            await manager.control('mihome:123', 'power')
        self.assertEqual(cloud.write, ('123', True))

    async def test_no_guessed_controls(self):
        a=MiHomeCloudAdapter(SimpleNamespace(did='1',category='light'))
        self.assertEqual(await a.list_capabilities(), [])

class LocalTests(unittest.IsolatedAsyncioTestCase):
    async def test_corrupt_checksum_rejected(self):
        from smart_home.adapters.mihome import _miio_unpack
        token=bytes(range(16))
        header=struct.pack('>HHIII',0x2131,48,0,42,10)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            _miio_unpack(header+b'\x00'*16+b'\x00'*16,token,42)

    async def test_explicit_mappings_only(self):
        device=SimpleNamespace(ip='127.0.0.1', credentials={'token':'00'*16})
        a=MiIOAdapter(device, {'miio_mapping': {'power': {'kind':'bool','readable':True,'writable':True,'read_method':'get_prop','read_params':['power'],'write_method':'set_power','write_params':['$value']}}})
        self.assertEqual([x['key'] for x in await a.list_capabilities()], ['power'])
        calls=[]
        async def call(method,params):
            calls.append((method,params)); return {'id':1,'result':[True]}
        with patch.object(a,'_call',call):
            self.assertTrue(await a.read_capability('power'))
            await a.write_capability('power',False)
        self.assertEqual(calls, [('get_prop',['power']),('set_power',[False])])
        self.assertEqual(await MiIOAdapter(device).list_capabilities(), [])
        with self.assertRaises(ValueError): await a.write_capability('brightness',50)

    async def test_udp_handshake_encryption_response_validation(self):
        from smart_home.adapters.mihome import _miio_send, _MIIO_NOOP
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad, unpad
        token=bytes(range(16)); key=hashlib.md5(token).digest(); iv=hashlib.md5(key+token).digest()
        did=0x12345678; stamp=123
        hello=struct.pack('>HHIII',0x2131,32,0,did,stamp)+b'\xff'*16
        class Socket:
            def __init__(self): self.sent=[]
            def setblocking(self,v): pass
            def connect(self,addr): self.addr=addr
            def close(self): pass
        sock=Socket()
        async def send(s,data):
            s.sent.append(data)
            if len(s.sent)==2:
                p=data; self.assertEqual(hashlib.md5(p[:16]+token+p[32:]).digest(),p[16:32])
                body=unpad(AES.new(key,AES.MODE_CBC,iv).decrypt(p[32:]),16)
                self.assertEqual(json.loads(body), {'id':1,'method':'get_prop','params':['power']})
        async def recv(s,n):
            if len(s.sent)==1: return hello
            body=AES.new(key,AES.MODE_CBC,iv).encrypt(pad(b'{"id":1,"result":["on"]}',16))
            header=struct.pack('>HHIII',0x2131,32+len(body),0,did,stamp)
            return header+hashlib.md5(header+token+body).digest()+body
        loop=asyncio.get_running_loop()
        with patch('smart_home.adapters.mihome.socket.socket',return_value=sock), patch.object(loop,'sock_sendall',side_effect=send), patch.object(loop,'sock_recv',side_effect=recv):
            result=await _miio_send('127.0.0.1',token.hex(),{'id':1,'method':'get_prop','params':['power']})
        self.assertEqual(sock.sent[0],_MIIO_NOOP)
        self.assertEqual(sock.addr,('127.0.0.1',54321))
        self.assertEqual(result['result'],['on'])

if __name__=='__main__': unittest.main()
