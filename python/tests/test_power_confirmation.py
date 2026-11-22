"""Cloud response confirmation must not manufacture device state (offline)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mihome_bridge as bridge
from app.core.service import MijiaService, ServiceError
from mijiaAPI import mijiaDevice
from mijiaAPI.devices import DevProp, DevAction


def cloud_service(response):
    """Keep upstream validation real; replace only the cloud transport."""
    calls = []
    api = SimpleNamespace(
        set_devices_prop=lambda data: (calls.append(data), response)[1],
        run_action=lambda data: (calls.append(data), response)[1],
    )
    dev = mijiaDevice.__new__(mijiaDevice)
    dev.api = api
    dev.did, dev.name, dev.sleep_time = 'lamp', 'Desk lamp', 0
    dev.prop_list = {'brightness': DevProp({
        'name': 'brightness', 'description': 'Brightness', 'type': 'uint',
        'rw': 'rw', 'range': [1, 100, 1], 'method': {'siid': 2, 'piid': 2},
    })}
    dev.action_list = {'toggle': DevAction({
        'name': 'toggle', 'description': 'Toggle', 'method': {'siid': 2, 'aiid': 1},
    })}
    svc = MijiaService.__new__(MijiaService)
    svc._get_device = lambda did: dev
    return svc, dev, api, calls

class PowerConfirmationTests(unittest.TestCase):
    def test_unknown_power_state_stays_unknown(self):
        svc = SimpleNamespace(power_state=lambda did: None)
        with patch.object(bridge, 'service', return_value=svc):
            self.assertEqual(bridge.op_power_states({'dids':['lamp']})['states'], {'lamp':None})

    def test_power_write_requires_nonempty_success_confirmation(self):
        for response in ([], None, [None], {}, [{'code':1}], [{'code':False}]):
            with self.subTest(response=response):
                svc = MijiaService.__new__(MijiaService)
                svc._api = SimpleNamespace(set_devices_prop=lambda data:response)
                with self.assertRaises(ServiceError):
                    svc._write_power('lamp', {'siid':2,'piid':1}, True)
        svc._api = SimpleNamespace(set_devices_prop=lambda data:[{'did':'lamp','siid':2,'piid':1,'code':0}])
        self.assertIsNone(svc._write_power('lamp', {'siid':2,'piid':1}, True))

    def test_property_and_action_require_device_confirmation(self):
        for operation in ('property', 'action'):
            for response in ({'code': 1}, {'code': False}, {}, [], None):
                with self.subTest(operation=operation, response=response):
                    svc, dev, api, calls = cloud_service(response)
                    with self.assertRaises(ServiceError):
                        if operation == 'property':
                            svc.write_prop('lamp', 'brightness', 42)
                        else:
                            svc.run_action('lamp', 'toggle')
                    self.assertEqual(len(calls), 1)
                    self.assertIs(dev.api, api)

    def test_property_validation_and_action_payload_are_preserved(self):
        svc, dev, api, calls = cloud_service({'code': 0})
        svc.write_prop('lamp', 'brightness', '42')
        self.assertEqual(calls, [{'did': 'lamp', 'siid': 2, 'piid': 2, 'value': 42}])
        with self.assertRaises(ServiceError):
            svc.write_prop('lamp', 'brightness', 101)
        self.assertEqual(len(calls), 1)
        svc.run_action('lamp', 'toggle', [3, False])
        self.assertEqual(calls[-1], {'did': 'lamp', 'siid': 2, 'aiid': 1, 'value': [3, False]})
        self.assertIs(dev.api, api)

    def test_power_toggle_reports_the_state_confirmed_by_service(self):
        svc = MijiaService.__new__(MijiaService)
        svc.power_state = Mock(side_effect=[None, True])
        svc.set_power_state = Mock()
        with patch.object(bridge, 'service', return_value=svc):
            result = bridge.op_power({'did': 'lamp'})
        self.assertEqual(result, {'on': False})
        svc.set_power_state.assert_called_once_with('lamp', False)

    def test_explicit_power_state_must_be_boolean(self):
        for state in ('off', None, 0, {}, []):
            with self.subTest(state=state):
                svc = Mock()
                with patch.object(bridge, 'service', return_value=svc):
                    with self.assertRaises(ValueError):
                        bridge.op_power({'did': 'lamp', 'state': state})
                svc.set_power_state.assert_not_called()
                svc.toggle_power.assert_not_called()
                svc.power_state.assert_not_called()

if __name__ == '__main__': unittest.main()
