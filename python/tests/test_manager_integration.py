"""Offline integration checks for private connections and honest capabilities."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smart_home.manager import SmartHomeManager
from smart_home.adapters.base import BaseAdapter, register

@register
class ContractAdapter(BaseAdapter):
    protocol = 'contract'
    async def list_capabilities(self):
        return [{'key':'power','name':'power','kind':'bool','readable':True,'writable':True}]
    async def read_capability(self, key):
        return None
    async def write_capability(self, key, value):
        return {'ok':'false'}

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = SmartHomeManager({'data_dir':self.temp.name})
    async def asyncTearDown(self):
        await self.manager.close()
        self.temp.cleanup()
    def scanned(self, **kw):
        return SimpleNamespace(protocol='contract',ip='192.0.2.4',name='Lamp',category='light',
                               fields={'token':'secret','version':'3.5'},credentials={'local_key':'private'},**kw)
    async def test_private_scan_credentials_and_no_guessed_controls(self):
        device = self.manager._build_device(self.scanned())
        self.assertNotIn('secret', json.dumps(device))
        self.assertEqual(device['controls'], [])
        adapter = self.manager._adapter_for(device)
        self.assertEqual(adapter.device.credentials['local_key'], 'private')
        self.assertEqual(adapter.device.credentials['token'], 'secret')
    async def test_configured_connection_creates_device_and_flattens_config(self):
        await self.manager.set_connections({'contract:manual':{'ip':'192.0.2.8','token':'hidden','name':'Manual'}})
        with patch('smart_home.discovery.discover_all', new=AsyncMock(return_value=[])):
            devices = (await self.manager.scan())['devices']
        self.assertEqual(devices[0]['id'], 'contract:manual')
        self.assertNotIn('hidden', json.dumps(devices))
        self.assertEqual(self.manager._adapter_for(devices[0]).config['token'], 'hidden')
    async def test_string_false_is_not_success_and_unknown_toggle_is_refused(self):
        device = self.manager._build_device(self.scanned())
        self.manager._scan = {'devices':[device]}
        result = await self.manager.control(device['id'], 'write', name='power', value=True)
        self.assertIs(result['ok'], False)
        with self.assertRaisesRegex(ValueError, 'state'):
            await self.manager.control(device['id'], 'power')

    async def test_explicit_null_power_state_is_rejected_without_dispatch(self):
        device = self.manager._build_device(self.scanned())
        self.manager._scan = {'devices':[device]}
        with patch.object(ContractAdapter, 'read_capability', new=AsyncMock(return_value=False)), patch.object(ContractAdapter, 'write_capability', new=AsyncMock(return_value={'ok': True})) as write:
            with self.assertRaises(ValueError):
                await self.manager.control(device['id'], 'power', state=None)
            write.assert_not_awaited()

    async def test_scene_rejects_explicit_null_power_state(self):
        with self.assertRaisesRegex(ValueError, 'state'):
            self.manager.scene_save({'name':'null-state', 'actions':[{'device':'contract:lamp','mode':'power','state':None}]})

    async def test_scene_omitted_state_toggles_and_explicit_false_turns_off(self):
        device = self.manager._build_device(self.scanned())
        self.manager._scan = {'devices':[device]}
        scene = self.manager.scene_save({'name':'power-states', 'actions':[
            {'device':device['id'], 'mode':'power'},
            {'device':device['id'], 'mode':'power', 'state':False},
        ]})
        with patch.object(ContractAdapter, 'read_capability', new=AsyncMock(return_value=False)), patch.object(ContractAdapter, 'write_capability', new=AsyncMock(return_value={'ok':True})) as write:
            result = await self.manager.scene_run(scene['id'])
        self.assertIs(result['ok'], True)
        self.assertEqual([step['result']['on'] for step in result['results']], [True, False])
        self.assertEqual([call.args for call in write.await_args_list], [('power',True), ('power',False)])
    async def test_scan_accepts_string_ports_before_cache_key(self):
        with patch('smart_home.discovery.discover_all', new=AsyncMock(return_value=[])) as discover:
            await self.manager.scan(subnets='192.0.2.0/24', ports='80,443', scan_ping='false')
        self.assertEqual(discover.call_args.kwargs['ports'], [80,443])
        self.assertIs(discover.call_args.kwargs['scan_ping'], False)
    async def test_corrupt_scenes_do_not_get_silently_overwritten(self):
        path = self.manager._scenes_path()
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.manager.scene_save({'name':'x','actions':[{'device':'x','mode':'power','state':True}]})
        self.assertEqual(path.read_text('utf-8'), '{broken')
    async def test_base_has_no_guessed_writable_controls(self):
        self.assertEqual(await BaseAdapter(self.scanned()).list_capabilities(), [])
    async def test_ha_is_registered_via_normal_entry(self):
        from smart_home.adapters import get_adapter_class
        self.assertIsNotNone(get_adapter_class('home_assistant'))
    async def test_configured_ha_entity_is_not_expanded_as_hub(self):
        from smart_home.adapters.home_assistant import HomeAssistantAdapter
        await self.manager.set_connections({'home_assistant:light.kitchen':{'base_url':'http://192.0.2.3:8123','token':'t','entity_id':'light.kitchen'}})
        with patch('smart_home.discovery.discover_all', new=AsyncMock(return_value=[])), patch.object(HomeAssistantAdapter,'discover_entities',new=AsyncMock(side_effect=AssertionError('entity expanded as hub'))):
            rows = (await self.manager.scan())['devices']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],'home_assistant:light.kitchen')
        self.assertNotIn('connection_error',rows[0])
    async def test_rescan_drops_expired_discovered_credentials(self):
        first = self.scanned()
        second = self.scanned()
        second.fields = {'version':'3.5'}
        second.credentials = {}
        with patch('smart_home.discovery.discover_all', new=AsyncMock(side_effect=[[first], [second]])):
            row = (await self.manager.scan())['devices'][0]
            self.assertEqual(self.manager._adapter_for(row).device.credentials['local_key'], 'private')
            row = (await self.manager.scan(force=True))['devices'][0]
        self.assertEqual(self.manager._adapter_for(row).device.credentials, {})
    async def test_discovered_ha_connection_is_inherited_by_entities(self):
        from smart_home.adapters.home_assistant import HomeAssistantAdapter
        await self.manager.set_connections({'home_assistant:192.0.2.9':{'token':'private-token'}})
        found = SimpleNamespace(protocol='home_assistant',ip='192.0.2.9',name='HA',category='gateway',
                                fields={'base_url':'http://192.0.2.9:8123'},credentials={})
        async def entities(hub):
            self.assertEqual(hub._connection()[:2], ('http://192.0.2.9:8123','private-token'))
            return [{'entity_id':'light.desk','protocol':'home_assistant','category':'light','fields':{'entity_id':'light.desk'}}]
        with patch('smart_home.discovery.discover_all', new=AsyncMock(return_value=[found])), patch.object(HomeAssistantAdapter,'discover_entities',entities):
            rows = (await self.manager.scan())['devices']
        self.assertEqual(len(rows), 2)
        child = rows[1]
        self.assertEqual(self.manager._adapter_for(child)._connection()[:2], ('http://192.0.2.9:8123','private-token'))
        self.assertNotIn('private-token', json.dumps(rows))
    async def test_multiple_ha_hubs_keep_entity_identity_and_target(self):
        from smart_home.adapters.home_assistant import HomeAssistantAdapter
        await self.manager.set_connections({
            'home_assistant:one':{'base_url':'http://192.0.2.1:8123','token':'one'},
            'home_assistant:two':{'base_url':'http://192.0.2.2:8123','token':'two'},
        })
        async def entities(_hub):
            return [{'id':'home_assistant:light.desk','entity_id':'light.desk','protocol':'home_assistant',
                     'category':'light','name':'Desk','online':True,'fields':{'entity_id':'light.desk','domain':'light'}}]
        with patch('smart_home.discovery.discover_all', new=AsyncMock(return_value=[])), patch.object(HomeAssistantAdapter,'discover_entities',entities):
            rows = (await self.manager.scan())['devices']
        children = [d for d in rows if d.get('fields',{}).get('entity_id')]
        self.assertEqual(len(children), 2)
        self.assertEqual(len({d['id'] for d in children}), 2)
        self.assertEqual({self.manager._adapter_for(d).config['token'] for d in children}, {'one','two'})
        self.assertTrue(all(d['category_zh']=='灯具' and d['source']=='home_assistant' for d in children))
        for child in children:
            adapter = self.manager._adapter_for(child)
            self.assertEqual(adapter._entity_id(),'light.desk')
        with patch.object(HomeAssistantAdapter,'write_capability', new=AsyncMock(return_value={'ok':True})) as write:
            await self.manager.control(children[0]['id'], 'write', name='power', value=True)
        write.assert_awaited_once_with('power',True)

if __name__ == '__main__': unittest.main()
