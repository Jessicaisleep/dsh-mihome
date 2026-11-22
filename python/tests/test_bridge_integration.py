"""Offline checks for bridge cancellation and private connection handoff."""
import asyncio
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mihome_bridge as bridge
from smart_home.manager import SmartHomeManager

class BridgeTests(unittest.TestCase):
    def test_timeout_cancels_pending_coroutine_before_delayed_write(self):
        completed = threading.Event()
        cancelled = threading.Event()
        async def delayed():
            try:
                await asyncio.sleep(.12)
                completed.set()
            finally:
                cancelled.set()
        with self.assertRaises(TimeoutError):
            bridge._run_async(delayed(), timeout=.01)
        self.assertTrue(cancelled.wait(.3))
        self.assertFalse(completed.is_set())
    def test_smart_operations_apply_private_connections_and_scene_contract(self):
        with TemporaryDirectory() as folder:
            manager = SmartHomeManager({'data_dir':folder})
            with patch.object(bridge, 'smart_home_manager', return_value=manager):
                result = bridge.handle({'id':1,'op':'smart_home_scenes','args':{
                    'mode':'save','scene':{'name':'demo','actions':[{'device':'x','mode':'power','state':True}]},
                    '_connections':{'contract:manual':{'token':'hidden'}}}})
                self.assertIs(result['result']['ok'], True)
                self.assertEqual(manager._connections['contract:manual']['token'], 'hidden')
                self.assertNotIn('hidden', str(result))
    def test_cold_start_device_operations_allow_a_full_scan_budget(self):
        manager = SimpleNamespace(device=AsyncMock(), control=AsyncMock(), scrape=AsyncMock())
        budgets = []
        def run(coro, timeout):
            budgets.append(timeout)
            coro.close()
            return {}
        with patch.object(bridge, 'smart_home_manager', return_value=manager), patch.object(bridge, '_run_async', side_effect=run):
            bridge.op_smart_home_device({'id':'contract:one'})
            bridge.op_smart_home_control({'device_id':'contract:one'})
            bridge.op_smart_home_scrape({'device_id':'contract:one'})
        self.assertEqual(budgets, [180, 180, 180])

    def test_action_keeps_explicit_empty_parameter_containers(self):
        from smart_home.adapters.base import BaseAdapter, register
        @register
        class ParamsAdapter(BaseAdapter):
            protocol = 'bridge-params-contract'
            async def run_action(self, name, params=None):
                return {'ok': True, 'received': params}
        with TemporaryDirectory() as folder:
            manager = SmartHomeManager({'data_dir': folder})
            manager._scan = {'devices': [{'id': 'bridge-params-contract:one',
                                         'protocol': ParamsAdapter.protocol, 'fields': {}}]}
            with patch.object(bridge, 'smart_home_manager', return_value=manager):
                for supplied in ({}, [], False, 0):
                    with self.subTest(params=supplied):
                        result = bridge.handle({'id': 1, 'op': 'smart_home_control', 'args': {
                            'device_id': 'bridge-params-contract:one', 'mode': 'action',
                            'name': 'run', 'params': supplied}})
                        self.assertIs(result['ok'], True)
                        self.assertEqual(result['result']['received'], supplied)
                        self.assertIs(type(result['result']['received']), type(supplied))
            bridge._run_async(manager.close(), timeout=1)

    def test_power_distinguishes_omitted_state_from_explicit_null(self):
        from smart_home.adapters.base import BaseAdapter, register
        writes = []
        @register
        class PowerAdapter(BaseAdapter):
            protocol = 'bridge-state-contract'
            async def read_capability(self, key):
                return False
            async def write_capability(self, key, value):
                writes.append((key, value))
                return {'ok':True, 'on':value}
        with TemporaryDirectory() as folder:
            manager = SmartHomeManager({'data_dir':folder})
            manager._scan = {'devices':[{'id':'bridge-state-contract:one', 'protocol':PowerAdapter.protocol, 'fields':{}}]}
            with patch.object(bridge, 'smart_home_manager', return_value=manager):
                result = bridge.op_smart_home_control({'device_id':'bridge-state-contract:one', 'mode':'power'})
                self.assertIs(result['on'], True)
                self.assertEqual(writes, [('power',True)])
                with self.assertRaises(ValueError):
                    bridge.op_smart_home_control({'device_id':'bridge-state-contract:one', 'mode':'power', 'state':None})
                self.assertEqual(writes, [('power',True)])
            bridge._run_async(manager.close(), timeout=1)

    def test_manager_cloud_proxy_updates_when_service_changes(self):
        old = bridge._manager
        bridge._manager = None
        try:
            with TemporaryDirectory() as folder, patch.dict('os.environ', {'MIHOME_DATA_DIR':folder}), patch.object(bridge, 'service', side_effect=[SimpleNamespace(), SimpleNamespace()]):
                manager = bridge.smart_home_manager()
                original = manager.cloud._svc
                self.assertIs(bridge.smart_home_manager(), manager)
                self.assertIsNot(manager.cloud._svc, original)
        finally:
            bridge._manager = old

if __name__ == '__main__': unittest.main()
