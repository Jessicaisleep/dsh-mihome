#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regressions for interface parsing, translation, classification and IEEE OUI.

Run: .venv\\Scripts\\python.exe -B python/smart_home/test_localization_repairs.py
All fixtures are synthetic; no discovery or device/network calls are made.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from smart_home import catalog, localize, util


class InterfaceAddressTests(unittest.TestCase):
    def addresses(self, output):
        # UDP source-address selection and command execution are the only external edges.
        with patch.object(util.socket, "socket", side_effect=OSError), patch.object(util, "_run", return_value=output):
            return util.own_ipv4_addresses()

    def test_windows_only_interface_fields_are_addresses(self):
        output = """
   IPv4 Address. . . . . . . . . . . : 192.0.2.10(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.0.2.1
   DNS Servers . . . . . . . . . . . : 8.8.8.8
                                       1.1.1.1
   IPv4 地址 . . . . . . . . . . . . : 198.51.100.9(首选)
   子网掩码  . . . . . . . . . . . . : 255.255.0.0
"""
        self.assertEqual(self.addresses(output), ["192.0.2.10", "198.51.100.9"])

    def test_linux_excludes_broadcast_and_invalid_addresses(self):
        output = """
2: eth0: <BROADCAST,MULTICAST,UP>
    inet 192.0.2.25/24 brd 192.0.2.255 scope global eth0
    inet 127.0.0.2/8 scope host lo
    inet 169.254.3.4/16 scope link eth1
    inet 0.0.0.0/0 scope global eth2
    inet 224.0.0.1/4 scope global eth3
    inet 999.0.0.1/24 scope global eth4
"""
        self.assertEqual(self.addresses(output), ["192.0.2.25"])


class CategoryTests(unittest.TestCase):
    def test_explicit_network_switch_beats_electrical_switch(self):
        for text in ("network switch", "Ethernet switch", "managed switch", "Gigabit PoE switch", "交换机"):
            with self.subTest(text=text):
                self.assertEqual(catalog.guess_category(text), "net_switch")

    def test_electrical_and_word_boundaries_are_preserved(self):
        for text, expected in (("wall switch", "switch"), ("smart switch", "switch"), ("开关", "switch"),
                               ('<meta content="font-family">', "other"), ("ONT GPON", "onu")):
            with self.subTest(text=text):
                self.assertEqual(catalog.guess_category(text), expected)


class TranslationTests(unittest.TestCase):
    def sample(self):
        return {
            "props": [{"name": "mode", "type": "uint8", "readable": True, "writable": True,
                       "range": [0, 4, 1], "value_list": [
                           {"value": 0, "description": "Auto", "extra": {"unit": "raw"}},
                           {"value": 1, "description": "Cool"},
                           {"value": "1", "description": "自定义模式"},
                       ]}],
            "actions": [{"name": "start", "in": [1], "desc": "开始清扫"}],
        }

    def test_device_localization_does_not_mutate_input(self):
        source = self.sample()
        before = copy.deepcopy(source)
        result = localize.localize_device(source, "mihome")
        self.assertEqual(source, before)
        result["props"][0]["value_list"][0]["extra"]["unit"] = "changed"
        self.assertEqual(source, before)

    def test_device_localization_is_idempotent_and_preserves_enum_values(self):
        once = localize.localize_device(self.sample(), "mihome")
        self.assertEqual(localize.localize_device(once, "mihome"), once)
        enums = once["props"][0]["value_list"]
        self.assertEqual([e["value"] for e in enums], [0, 1, "1"])
        self.assertEqual([type(e["value"]) for e in enums], [int, int, str])
        self.assertEqual([e["zh"] for e in enums], ["自动", "制冷", "自定义模式"])
        self.assertEqual(enums[0]["description"], "Auto")
        self.assertEqual(once["props"][0]["range"], [0, 4, 1])

    def test_scalar_enum_translation_preserves_wire_types(self):
        values = [0, 1, "0", "1", True, False]
        translated = localize.translate_value_list(values, "mode")
        self.assertEqual([entry["value"] for entry in translated], values)
        self.assertEqual([type(entry["value"]) for entry in translated], [int, int, str, str, bool, bool])
        self.assertEqual([entry["zh"] for entry in translated], ["0", "1", "0", "1", "开", "关"])

    def test_boolean_numeric_translation_requires_context(self):
        self.assertEqual(localize.translate_value(0, "mode"), "0")
        self.assertEqual(localize.translate_value(1, "mode"), "1")
        self.assertEqual(localize.translate_value("false", "mode"), "false")
        self.assertEqual(localize.translate_value(0, "power"), "关")
        self.assertEqual(localize.translate_value(True), "开")

    def test_official_description_and_generic_fallback_are_reached(self):
        result = localize.localize_device({"props": [
            {"name": "power", "desc": "实际耗电功率"},
            {"name": "wifi_signal"}, {"name": "color-temperature"},
            {"name": "target-temperature"},
        ]}, "mihome")
        self.assertEqual([p["zh"] for p in result["props"]],
                         ["实际耗电功率", "无线 信号", "色温", "设定温度"])
        self.assertEqual(localize.translate_name("unknown", "厂家中文名称"), "厂家中文名称")
        self.assertEqual(localize.translate_name("targetTemperature"), "目标温度")
        self.assertEqual(localize.translate_name("unrecognisedThing"), "unrecognisedThing")

    def test_controls_normalize_common_schema_without_discarding_specs(self):
        controls = [
            {"key": "color_temp", "name": "color-temperature", "type": "uint16", "kind": "range",
             "range": [1700, 6500, 1], "unit": "K", "writable": True, "readable": False,
             "vendor": {"siid": 2, "piid": 3}},
            {"key": "mode", "kind": "enum", "values": [{"value": 1, "description": "Auto"}], "writable": False},
            {"name": "brightness", "kind": "range", "min": 1, "max": 1000, "step": 1},
            {"name": "wifi_signal", "type": "int8", "readable": True},
        ]
        before = copy.deepcopy(controls)
        out = localize.localize_controls(controls, "mihome")
        self.assertEqual(controls, before)
        for entry in out:
            self.assertTrue({"key", "name", "kind", "range", "value_list", "writable"} <= entry.keys())
        self.assertEqual(out[0]["range"], [1700, 6500, 1])
        self.assertEqual(out[0]["unit"], "K")
        self.assertIs(out[0]["readable"], False)
        self.assertEqual(out[0]["vendor"], {"siid": 2, "piid": 3})
        self.assertEqual(out[1]["name"], "mode")
        self.assertEqual(out[1]["value_list"][0]["value"], 1)
        self.assertEqual(out[1]["value_list"][0]["zh"], "自动")
        self.assertIs(out[1]["writable"], False)
        self.assertEqual(out[2]["range"], [1, 1000, 1])
        self.assertIs(out[2]["writable"], False)
        self.assertEqual(out[3]["zh"], "无线 信号")
        self.assertEqual(localize.localize_controls(out, "mihome"), out)
        out[0]["vendor"]["siid"] = 99
        self.assertEqual(controls, before)


class OuiTests(unittest.TestCase):
    def test_audited_prefixes_use_ieee_organizations(self):
        expected = {
            "000862": "NEC Eluminant Technologies, Inc.", "0016E8": "Lumissil Microsystems",
            "0022FE": "Advanced Illumination", "00234F": "Luminous Power Technologies Pvt. Ltd.",
            "18C23C": "Lumi United Technology Co., Ltd", "284992": "Luminator Technology Group Global LLC",
            "284D92": "Luminator Technology Group Global LLC", "3042A1": "ilumisys Inc. DBA Toggled",
            "54EF44": "Lumi United Technology Co., Ltd", "80435E": "Luminys Systems Corporation",
            "84F883": "Luminar Technologies", "AC40B5": "Lumiva Inc.", "B461FF": "Lumigon A/S",
            "D0699E": "LUMINEX Lighting Control Equipment", "E85D6B": "Luminate Wireless",
        }
        for prefix, organization in expected.items():
            with self.subTest(prefix=prefix):
                self.assertEqual(util.mac_vendor(prefix + "000001"), organization)

    def test_mac_lookup_rejects_non_global_or_incomplete_addresses(self):
        for mac in ("000862", "00:08:62:00:00", "00:08:62:00:00:GG", "00:08-62:00:00:01",
                    "00:08:62:00:00:01:02", "FF:FF:FF:FF:FF:FF", "02:08:62:00:00:01",
                    "01:08:62:00:00:01", "00:00:00:00:00:00"):
            with self.subTest(mac=mac):
                self.assertEqual(util.mac_vendor(mac), "")
        for mac in ("00:08:62:00:00:01", "00-08-62-00-00-01", "0008.6200.0001", "000862000001"):
            with self.subTest(mac=mac):
                self.assertEqual(util.mac_vendor(mac), "NEC Eluminant Technologies, Inc.")

    def test_generator_excludes_ambiguous_assignments_and_supports_csv(self):
        script = HERE.parent.parent / "scripts" / "update-oui.py"
        spec = importlib.util.spec_from_file_location("update_oui_ambiguity_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = ("Registry,Assignment,Organization Name,Organization Address\n"
               'MA-L,000862,"NEC Eluminant Technologies, Inc.",US\n'
               "MA-L,080030,NETWORK RESEARCH CORPORATION,US\n"
               "MA-L,080030,CERN,CH\n"
               "MA-S,ABCDEF,Outside this registry,US\n").encode()
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "oui.csv", Path(tmp) / "table.py"
            source.write_bytes(raw)
            self.assertEqual(module.main(["--input", str(source), "--output", str(output),
                                         "--snapshot-date", "2026-11-22", "--sha256", hashlib.sha256(raw).hexdigest()]), 0)
            namespace = {}
            exec(compile(output.read_bytes(), str(output), "exec"), namespace)
            self.assertEqual(namespace["OUI_TABLE"], {"000862": "NEC Eluminant Technologies, Inc."})
            self.assertEqual(namespace["AMBIGUOUS_ASSIGNMENTS"], {"080030": ["CERN", "NETWORK RESEARCH CORPORATION"]})

    def test_generator_reproduces_real_names_and_rejects_unverified_source(self):
        script = HERE.parent.parent / "scripts" / "update-oui.py"
        self.assertTrue(script.is_file(), "a reproducible generator must ship with the table")
        spec = importlib.util.spec_from_file_location("update_oui_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = ("OUI/MA-L Organization\n"
               "00-08-62   (hex)\tNEC Eluminant Technologies, Inc.\r\n"
               "000862     (base 16)\tNEC Eluminant Technologies, Inc.\r\n"
               "0016E8     (base 16)\tLumissil Microsystems\r\n"
               "000862     (base 16)\tNEC Eluminant Technologies, Inc.\r\n").encode()
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "ieee.txt", Path(tmp) / "table.py"
            source.write_bytes(raw)
            args = ["--input", str(source), "--output", str(output), "--snapshot-date", "2026-11-22",
                    "--sha256", hashlib.sha256(raw).hexdigest()]
            self.assertEqual(module.main(args), 0)
            namespace = {}
            first = output.read_bytes()
            exec(compile(first, str(output), "exec"), namespace)
            self.assertEqual(namespace["OUI_TABLE"], {"000862": "NEC Eluminant Technologies, Inc.", "0016E8": "Lumissil Microsystems"})
            self.assertEqual(namespace["SOURCE_SHA256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(module.main(args), 0)
            self.assertEqual(output.read_bytes(), first)
            with self.assertRaises(ValueError):
                module.main(args[:-1] + ["0" * 64])
            self.assertEqual(output.read_bytes(), first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
