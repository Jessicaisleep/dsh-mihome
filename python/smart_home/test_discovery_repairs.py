#!/usr/bin/env python
"""离线发现层回归；所有网络边界均为假实现。"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from smart_home import discovery
from smart_home.adapters import ble


class FakeDevice:
    def __init__(self, address, name="BLE watch", uuids=None):
        self.address = address
        self.name = name
        self.uuids = uuids or ["180f"]


class FakeAdvertisement:
    def __init__(self, rssi=-42, service_uuids=None, local_name=""):
        self.rssi = rssi
        self.service_uuids = service_uuids or ["180f"]
        self.local_name = local_name


class FakeScanner:
    @staticmethod
    async def discover(**_kwargs):
        return {
            "AA:BB:CC:00:00:01": (FakeDevice("AA:BB:CC:00:00:01"), FakeAdvertisement()),
            "AA:BB:CC:00:00:02": (FakeDevice("AA:BB:CC:00:00:02", "BLE lock"), FakeAdvertisement(-55)),
        }


async def test_ble_return_adv_dict_is_supported():
    fake_bleak = types.SimpleNamespace(BleakScanner=FakeScanner)
    with patch.dict(sys.modules, {"bleak": fake_bleak}):
        found = await ble.ble_scan(timeout=0.01)
    assert {item["address"] for item in found} == {
        "AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02"
    }, found
    assert found[0]["rssi"] == -42, found
    assert found[0]["uuids"] == ["180f"], found


async def test_mdns_uses_service_browser_and_resolved_ip():
    class FakeInfo:
        server = "cast.local."
        name = "Living Room._googlecast._tcp.local."
        properties = {b"model": b"Nest Audio", b"fn": b"Living Room"}

        def parsed_addresses(self):
            return ["192.0.2.55"]

    class FakeAsyncZeroconf:
        created = 0
        closed = 0
        def __init__(self, **_kwargs):
            type(self).created += 1
            self.zeroconf = object()

        async def async_get_service_info(self, type_, name, timeout=3000):
            assert type_ == "_googlecast._tcp.local."
            assert name.endswith("._googlecast._tcp.local.")
            assert timeout >= 100
            return FakeInfo()

        async def async_close(self):
            type(self).closed += 1

    class FakeBrowser:
        def __init__(self, _zc, service_type, listener=None, **_kwargs):
            self.listener = listener
            if service_type == "_googlecast._tcp.local." and listener is not None:
                listener.add_service(_zc, service_type, "Living Room._googlecast._tcp.local.")

        async def async_cancel(self):
            return None

    fake_zeroconf = types.SimpleNamespace(
        IPVersion=types.SimpleNamespace(V4Only="v4"),
        InterfaceChoice=types.SimpleNamespace(All="all"),
    )
    fake_asyncio = types.SimpleNamespace(
        AsyncZeroconf=FakeAsyncZeroconf,
        AsyncServiceBrowser=FakeBrowser,
    )
    with patch.dict(sys.modules, {
        "zeroconf": fake_zeroconf,
        "zeroconf.asyncio": fake_asyncio,
    }):
        found = await discovery.mdns_discover(timeout=0.25)
    assert found == [{
        "ip": "192.0.2.55",
        "service": "_googlecast._tcp.local",
        "name": "Living Room._googlecast._tcp.local.",
        "model": "Nest Audio",
    }], found
    assert FakeAsyncZeroconf.created == FakeAsyncZeroconf.closed == 1


def test_scanned_device_never_serializes_credentials():
    device = discovery.ScannedDevice(
        ip="192.0.2.5", protocol="tuya", requires_credentials=True,
        credentials={"local_key": "must-not-leak"},
        fields={"device_id": "gw-1", "nested": {"password": "no", "safe": "yes"}},
    )
    public = device.to_dict()
    assert public["requires_credentials"] is True
    assert "local_key" not in repr(public)
    assert "password" not in repr(public)
    assert public["fields"]["nested"] == {"safe": "yes"}


async def test_discover_all_parses_string_lists():
    calls = []

    async def fake_scan(subnet, **kwargs):
        calls.append((subnet, kwargs))
        return []

    with patch.object(discovery, "scan_subnet", fake_scan), \
         patch.object(discovery.util, "local_subnets", return_value=[]):
        await discovery.discover_all(
            subnets="192.0.2.0/24, 198.51.100.0/24",
            prefix="24",
            extra_subnets="203.0.113.0/24",
            ports="80,443",
            scan_ping="false",
        )
    assert [item[0] for item in calls] == ["192.0.2.0/24", "198.51.100.0/24"], calls
    assert calls[0][1]["ports"] == [80, 443], calls
    assert calls[0][1]["scan_ping"] is False, calls


async def test_scan_keeps_broadcast_and_ble_when_no_tcp_candidates():
    async def fake_extra(_subnet):
        return [{"ip": "", "address": "AA:BB:CC:00:00:09", "protocol": "ble"}]

    with patch.object(discovery.util, "arp_table", return_value={}), \
         patch.object(discovery.util, "ping_sweep", return_value=[]), \
         patch.object(discovery, "_extra_discovery", fake_extra):
        found = await discovery.scan_subnet("192.0.2.0/30", ports=[80], scan_ping=True)
    assert any(device.fields.get("address") == "AA:BB:CC:00:00:09" for device in found), found


async def test_scan_merges_protocol_and_keeps_online_unknown():
    async def fake_ports(*_args):
        return []

    async def fake_extra(_subnet):
        return [{"ip": "192.0.2.1", "protocol": "tuya", "category": "plug",
                 "confidence": 0.9, "gw_id": "gw-1", "version": "3.3",
                 "local_key": "private-value", "source": "tuya-udp"}]

    with patch.object(discovery.util, "arp_table", return_value={"192.0.2.1": "00:11:22:33:44:55", "192.0.2.2": ""}), \
         patch.object(discovery.util, "scan_ports", fake_ports), \
         patch.object(discovery, "_extra_discovery", fake_extra):
        found = await discovery.scan_subnet("192.0.2.0/24", ports=[80], scan_ping=False)
    assert len(found) == 2, found
    target = next(d for d in found if d.ip == "192.0.2.1")
    assert target.protocol == "tuya" and target.category == "plug", target
    assert target.fields["gw_id"] == "gw-1" and target.fields["version"] == "3.3", target
    assert target.credentials["local_key"] == "private-value"
    assert "private-value" not in repr(target.to_dict())


async def main():
    test_scanned_device_never_serializes_credentials()
    await test_ble_return_adv_dict_is_supported()
    await test_mdns_uses_service_browser_and_resolved_ip()
    await test_discover_all_parses_string_lists()
    await test_scan_keeps_broadcast_and_ble_when_no_tcp_candidates()
    await test_scan_merges_protocol_and_keeps_online_unknown()
    print("discovery repair tests passed")


if __name__ == "__main__":
    asyncio.run(main())
