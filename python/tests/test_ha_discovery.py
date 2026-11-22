#!/usr/bin/env python
"""HA mDNS/HTTP and miIO hello regressions; network boundaries are always mocked."""
from __future__ import annotations

import asyncio
import socket
import struct
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smart_home import discovery, util


HA_TYPE = '_home-assistant._tcp.local.'
HA_UUID = '0123456789abcdef0123456789abcdef'


def mdns_modules(port=8123):
    class Info:
        server = HA_UUID + '.local.'
        name = 'Test Home.' + HA_TYPE
        properties = {
            b'location_name': b'Test Home', b'uuid': HA_UUID.encode(), b'version': b'2026.11.0',
            b'internal_url': b'http://user:private@wrong.example:9999', b'token': b'must-not-leak',
        }
        def parsed_addresses(self):
            return ['192.0.2.20']
    Info.port = port

    class Zeroconf:
        closed = 0
        def __init__(self, **kwargs):
            self.zeroconf = object()
        async def async_get_service_info(self, type_, name, timeout=3000):
            assert type_ == HA_TYPE and name == Info.name
            return Info()
        async def async_close(self):
            Zeroconf.closed += 1

    class Browser:
        seen = []
        cancelled = 0
        def __init__(self, zc, type_, listener=None, **kwargs):
            Browser.seen.append(type_)
            if type_ == HA_TYPE:
                listener.add_service(zc, type_, Info.name)
        async def async_cancel(self):
            Browser.cancelled += 1

    return {
        'zeroconf': types.SimpleNamespace(IPVersion=types.SimpleNamespace(V4Only='v4')),
        'zeroconf.asyncio': types.SimpleNamespace(AsyncZeroconf=Zeroconf, AsyncServiceBrowser=Browser),
    }, Browser, Zeroconf


class HaDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_mdns_type_preserves_resolved_ip_port_uuid(self):
        modules, browser, zc = mdns_modules()
        with patch.dict(sys.modules, modules):
            rows = await discovery.mdns_discover(timeout=0.1)
        self.assertIn(HA_TYPE, browser.seen)
        self.assertEqual(zc.closed, 1)
        self.assertEqual(browser.cancelled, len(browser.seen))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['ip'], '192.0.2.20')
        self.assertEqual(rows[0]['port'], 8123)
        self.assertEqual(rows[0]['uuid'], HA_UUID)
        self.assertEqual(rows[0]['version'], '2026.11.0')
        self.assertEqual(rows[0]['location_name'], 'Test Home')
        self.assertNotIn('private', repr(rows))
        self.assertNotIn('must-not-leak', repr(rows))

    async def test_empty_arp_and_ssdp_still_find_ha_without_tcp(self):
        modules, _, _ = mdns_modules()
        with patch.dict(sys.modules, modules), \
             patch.object(util, 'arp_table', return_value={}), \
             patch.object(util, 'ping_sweep', return_value=[]), \
             patch.object(util, 'scan_ports', AsyncMock(return_value=[])) as scan_ports, \
             patch.object(discovery, 'ssdp_discover', AsyncMock(return_value=[])), \
             patch.object(discovery, 'tuya_discover', AsyncMock(return_value=[])), \
             patch('smart_home.adapters.ble.ble_scan', AsyncMock(return_value=[])):
            rows = await discovery.scan_subnet('192.0.2.0/24', scan_ping=False)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row.ip, row.protocol, row.category), ('192.0.2.20', 'home_assistant', 'gateway'))
        self.assertEqual(row.name, 'Test Home')
        self.assertEqual(row.ports, [8123])
        self.assertEqual(row.fields['uuid'], HA_UUID)
        self.assertEqual(row.fields['base_url'], 'http://192.0.2.20:8123')
        self.assertEqual(row.fields['port'], 8123)
        self.assertTrue(row.requires_credentials)
        self.assertNotIn('private', repr(row.to_dict()))
        scan_ports.assert_not_awaited()

    async def test_default_ports_and_http_fingerprint_include_8123(self):
        self.assertIn(8123, util.DEFAULT_PORTS)
        row = discovery.ScannedDevice(ip='192.0.2.20', ports=[8123])
        get = AsyncMock(return_value=('Home Assistant', 'aiohttp', '<title>Home Assistant</title>', 'http://192.0.2.20:8123/'))
        with patch.object(discovery, 'fetch_http', get):
            await discovery._fingerprint(row)
        get.assert_awaited_once_with('192.0.2.20', 8123, use_tls=False)
        self.assertEqual((row.protocol, row.category), ('home_assistant', 'gateway'))
        self.assertEqual(row.fields['base_url'], 'http://192.0.2.20:8123')
        self.assertEqual(row.fields['port'], 8123)
        self.assertTrue(row.requires_credentials)

    async def test_http_tls_keeps_actual_scheme_and_non_ha_is_not_guessed(self):
        row = discovery.ScannedDevice(ip='192.0.2.20', ports=[8123])
        get = AsyncMock(side_effect=[None, ('Home Assistant', '', '', 'https://192.0.2.20:8123/')])
        with patch.object(discovery, 'fetch_http', get):
            await discovery._fingerprint(row)
        self.assertEqual(row.protocol, 'home_assistant')
        self.assertEqual(row.fields['base_url'], 'https://192.0.2.20:8123')
        other = discovery.fingerprint_http('Example Dashboard', 'aiohttp', '', '192.0.2.20', 8123)
        self.assertNotEqual(other.protocol, 'home_assistant')

    async def test_invalid_ha_srv_port_never_creates_endpoint(self):
        mdns = AsyncMock(return_value=[
            {'ip': '192.0.2.20', 'port': port, 'service': HA_TYPE.rstrip('.')}
            for port in (0, 65536, 'invalid', None)
        ])
        with patch.object(discovery, 'mdns_discover', mdns), \
             patch.object(discovery, 'ssdp_discover', AsyncMock(return_value=[])), \
             patch.object(discovery, 'tuya_discover', AsyncMock(return_value=[])), \
             patch('smart_home.adapters.ble.ble_scan', AsyncMock(return_value=[])):
            rows = await discovery._extra_discovery('192.0.2.0/24')
        self.assertEqual(rows, [])

    async def test_mdns_does_not_downgrade_confirmed_https_endpoint(self):
        row = discovery.ScannedDevice(ip='192.0.2.20', protocol='home_assistant',
                                      confidence=.95, fields={'base_url': 'https://192.0.2.20:8123'})
        discovery._merge_item(row, {
            'protocol': 'home_assistant', 'source': 'mdns', 'confidence': .95,
            'fields': {'base_url': 'http://192.0.2.20:8123', 'uuid': HA_UUID},
        })
        self.assertEqual(row.fields['base_url'], 'https://192.0.2.20:8123')
        self.assertEqual(row.fields['uuid'], HA_UUID)

    async def test_ha_os_port80_and_subnet_boundary(self):
        mdns = AsyncMock(return_value=[
            {'ip': '192.0.2.20', 'port': 80, 'service': HA_TYPE.rstrip('.'), 'uuid': HA_UUID,
             'location_name': 'HA OS', 'name': 'HA OS.' + HA_TYPE, 'version': '2026.11.0'},
            {'ip': '198.51.100.9', 'port': 8123, 'service': HA_TYPE.rstrip('.'), 'uuid': 'other'},
        ])
        with patch.object(discovery, 'mdns_discover', mdns), \
             patch.object(discovery, 'ssdp_discover', AsyncMock(return_value=[])), \
             patch.object(discovery, 'tuya_discover', AsyncMock(return_value=[])), \
             patch('smart_home.adapters.ble.ble_scan', AsyncMock(return_value=[])):
            rows = await discovery._extra_discovery('192.0.2.0/24')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['protocol'], 'home_assistant')
        self.assertEqual(rows[0]['fields']['base_url'], 'http://192.0.2.20:80')
        self.assertEqual(rows[0]['ports'], [80])


class MiioHelloTests(unittest.IsolatedAsyncioTestCase):
    def packet(self, magic=0x2131, length=32, unknown=0, did=0x12345678, stamp=1234567):
        return struct.pack('>HHIII', magic, length, unknown, did, stamp) + b'secret-token1234'

    async def hello(self, data=None, error=None, ip='192.0.2.30', timeout=0.2):
        sock = Mock()
        # Handshake checksum/token is exactly 16 bytes and must never be exposed.
        sock.recv.return_value = data if data is not None else self.packet()
        if error:
            sock.recv.side_effect = error
        fake_socket = types.SimpleNamespace(AF_INET=socket.AF_INET, SOCK_DGRAM=socket.SOCK_DGRAM,
                                            socket=Mock(return_value=sock))
        # Replacing the global socket.socket type breaks Windows Proactor's own
        # isinstance checks; inject only the module imported by the function.
        with patch.dict(sys.modules, {'socket': fake_socket}), \
             patch('asyncio.open_connection', AsyncMock(side_effect=OSError('TCP forbidden'))) as tcp:
            result = await discovery.miio_hello(ip, timeout=timeout)
        create = fake_socket.socket
        return result, sock, create, tcp

    async def test_hello_uses_udp_big_endian_header_without_leaking_token(self):
        result, sock, create, tcp = await self.hello()
        create.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        tcp.assert_not_awaited()
        sock.connect.assert_called_once_with(('192.0.2.30', 54321))
        sock.send.assert_called_once_with(discovery.MIIO_HELLO)
        sock.settimeout.assert_called_once_with(0.2)
        sock.close.assert_called_once()
        self.assertEqual(result, {'device_id': str(0x12345678), 'timestamp': 1234567})
        self.assertNotIn('secret', repr(result))
        self.assertNotIn('model', result)

    async def test_malformed_and_echoed_hello_never_identify_device(self):
        for data in (b'bad', self.packet(magic=0x3121), self.packet(length=33),
                     self.packet() + b'{}', discovery.MIIO_HELLO,
                     self.packet(did=0xffffffff), self.packet(stamp=0xffffffff)):
            with self.subTest(data=data[:16].hex()):
                result, sock, _, _ = await self.hello(data=data)
                self.assertEqual(result, {})
                sock.close.assert_called_once()

    async def test_udp_timeout_and_oserror_close_socket(self):
        for error in (socket.timeout(), OSError('unreachable')):
            result, sock, _, _ = await self.hello(error=error)
            self.assertEqual(result, {})
            sock.close.assert_called_once()

    async def test_invalid_address_or_timeout_creates_no_socket(self):
        for ip, timeout in (('not-a-host', .2), ('::1', .2), ('192.0.2.30', 0), ('192.0.2.30', -1)):
            result, _, create, _ = await self.hello(ip=ip, timeout=timeout)
            self.assertEqual(result, {})
            create.assert_not_called()

    async def test_udp_only_arp_candidate_does_not_require_open_tcp54321(self):
        hello = AsyncMock(return_value={'device_id': '12345', 'timestamp': 10})
        with patch.object(util, 'arp_table', return_value={'192.0.2.30': ''}), \
             patch.object(util, 'scan_ports', AsyncMock(return_value=[])), \
             patch.object(discovery, '_extra_discovery', AsyncMock(return_value=[])), \
             patch.object(discovery, 'miio_hello', hello):
            rows = await discovery.scan_subnet('192.0.2.0/24', ports=[54321], scan_ping=False)
        hello.assert_awaited_once_with('192.0.2.30', timeout=1.2)
        self.assertEqual(rows[0].protocol, 'miio')
        self.assertEqual(rows[0].fields['miio'], {'device_id': '12345', 'timestamp': 10})
        self.assertEqual(rows[0].ports, [])

    async def test_udp_candidates_use_bounded_concurrency(self):
        started = 0
        active = 0
        peak = 0
        ready = asyncio.Event()
        async def hello(*args, **kwargs):
            nonlocal started, active, peak
            started += 1
            active += 1
            peak = max(peak, active)
            if started >= 2:
                ready.set()
            try:
                await asyncio.wait_for(ready.wait(), .1)
                return {'device_id': args[0].split('.')[-1], 'timestamp': 10}
            finally:
                active -= 1
        with patch.object(util, 'arp_table', return_value={f'192.0.2.{n}': '' for n in range(1, 5)}), \
             patch.object(util, 'scan_ports', AsyncMock(return_value=[])), \
             patch.object(discovery, '_extra_discovery', AsyncMock(return_value=[])), \
             patch.object(discovery, 'miio_hello', hello):
            rows = await discovery.scan_subnet('192.0.2.0/24', ports=[54321], scan_ping=False, concurrency=2)
        self.assertEqual(len(rows), 4)
        self.assertEqual(peak, 2)

    async def test_explicit_port_selection_can_skip_udp_probe(self):
        with patch.object(util, 'arp_table', return_value={'192.0.2.30': ''}), \
             patch.object(util, 'scan_ports', AsyncMock(return_value=[])), \
             patch.object(discovery, '_extra_discovery', AsyncMock(return_value=[])), \
             patch.object(discovery, 'miio_hello', AsyncMock()) as hello:
            await discovery.scan_subnet('192.0.2.0/24', ports=[80], scan_ping=False)
        hello.assert_not_awaited()


if __name__ == '__main__':
    unittest.main(verbosity=2)
