# SPDX-License-Identifier: GPL-3.0-or-later
"""Unify discovered and configured home-device adapters.

The manager exposes device identity, observed capabilities, private connection
injection, read/write operations and manually run user-defined scenes. It does
not infer writable controls from a device category. Scene steps are recorded
individually and later steps continue after an earlier step fails.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from . import catalog, discovery, localize
from .adapters import get_adapter_class


_STATE_UNSET = object()


def _now() -> float:
    return time.time()


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


# 指纹识别出的「更细的厂商协议」映射到实际能用的适配器。
#
# 扫描层出于展示需要会给出 tplink / h3c / openwrt / synology / qnap 这类
# 更具体的协议名（用户一眼能看出是什么牌子），但它们没有独立适配器——
# 它们全都靠「扒管理页」工作，走 _PageAdapter 那一族即可。少了这张表，
# 扫到的路由器点开会报「没有 tplink 协议的适配器」。
_ADAPTER_ALIASES: dict[str, str] = {
    "tplink": "router",
    "h3c": "router",
    "openwrt": "router",
    "mirouter": "router",
    "synology": "nas",
    "qnap": "nas",
    "truenas": "nas",
    # 海信 / TCL 电视：目前没有专门的本地控制适配器（各家协议不统一），
    # 先按「能开管理页的 HTTP 设备」展示，不假装能控制。
    "hisense_tv": "http",
    "tcl_tv": "http",
    "airplay": "cast",
    "mdns": "cast",
    "matter": "http",
    "homekit": "http",
    "mqtt": "http",
}


class SmartHomeManager:
    """全屋智能家居统一管理器。"""

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.data_dir = Path(self.config.get("data_dir") or "data")
        self.cloud = self.config.get("cloud")
        self._scan: dict = {}
        self._scan_at: float = 0.0
        self._scan_key: tuple = ()
        self._adapters: dict[str, object] = {}
        self._connections: dict[str, dict] = {}
        self._discovered_credentials: dict[str, dict] = {}
        self._connection_refs: dict[str, str] = {}

    def _connection_for(self, device_id: str) -> dict:
        key = self._connection_refs.get(device_id, device_id)
        return dict(self._connections.get(key, {}))

    async def set_connections(self, connections: dict) -> None:
        if not isinstance(connections, dict) or any(not isinstance(v, dict) for v in connections.values()):
            raise ValueError("连接配置必须是设备 ID 到对象的映射")
        if connections == self._connections:
            return
        await self.close()
        self._connections = {str(k): dict(v) for k, v in connections.items()}
        self._connection_refs.clear()
        self._scan = {}
        self._scan_at = 0

    # ------------------------------------------------------------------ 扫描

    async def scan(
        self,
        subnets: list[str] | None = None,
        prefix: int = 24,
        extra_subnets: list[str] | None = None,
        ports: list[int] | None = None,
        scan_ping: bool = True,
        force: bool = False,
    ) -> dict:
        """扫描网段，返回统一设备列表。

        默认缓存 60 秒，避免面板反复点刷新就反复扫网。``force=True`` 强制
        重新扫描。
        """
        options = discovery.normalize_scan_options(subnets, prefix, extra_subnets, ports, scan_ping)
        subnets, prefix, extra_subnets, ports, scan_ping = options
        scan_key = (
            tuple(sorted(str(x) for x in (subnets or []))),
            int(prefix),
            tuple(sorted(str(x) for x in (extra_subnets or []))),
            tuple(sorted(int(x) for x in ports)),
            bool(scan_ping),
        )
        if not force and self._scan and self._scan_key == scan_key and (_now() - self._scan_at) < 60:
            return {"devices": self._scan["devices"], "cached": True, "scanned_at": self._scan_at}

        found = await discovery.discover_all(
            subnets=subnets,
            prefix=prefix,
            extra_subnets=extra_subnets,
            ports=ports,
            scan_ping=scan_ping,
        )
        self._discovered_credentials.clear()
        devices = [self._build_device(d) for d in found]

        # 叠加米家云端设备（只加未被局域网识别到的，避免重复）
        try:
            cloud_devices = await self._cloud_devices()
        except Exception as exc:  # noqa: BLE001 - 云端没登录不阻断局域网扫描
            print(f"[smart-home] 米家云端设备读取失败：{exc}")
            cloud_devices = []
        seen_ids = {d["id"] for d in devices}
        for device in cloud_devices:
            if device["id"] in seen_ids:
                continue
            devices.append(device)

        old_adapters = self._adapters
        self._adapters = {}
        for adapter in old_adapters.values():
            try:
                await adapter.close()
            except Exception:  # noqa: BLE001 - one adapter must not block a rescan
                pass
        # Configured endpoints also work without multicast or ping discovery.
        for device_id, connection in self._connections.items():
            if any(d["id"] == device_id for d in devices):
                continue
            protocol = device_id.split(":", 1)[0]
            devices.append({"id": device_id, "protocol": protocol,
                            "name": str(connection.get("name") or device_id),
                            "ip": str(connection.get("ip") or ""), "ports": [],
                            "category": str(connection.get("category") or "other"),
                            "category_zh": catalog.category_zh(connection.get("category") or "other"),
                            "category_en": catalog.category_en(connection.get("category") or "other"),
                            "source": "configured", "fields": {}, "controls": [], "online": None})
        expanded = []
        self._connection_refs.clear()
        ids = {d["id"] for d in devices}
        for device in devices:
            if device["protocol"] != "home_assistant":
                continue
            # Explicitly configured entities are leaves, not hubs to expand.
            if self._connection_for(device["id"]).get("entity_id"):
                continue
            try:
                hub = self._adapter_for(device)
                for raw in await hub.discover_entities():
                    entity_id = str(raw.get("entity_id") or raw.get("fields", {}).get("entity_id") or "")
                    if not entity_id:
                        continue
                    # A HA entity ID is unique only inside its own instance.
                    entity = dict(raw)
                    entity["id"] = f"{device['id']}/{entity_id}"
                    if entity["id"] in ids:
                        continue
                    ids.add(entity["id"])
                    category = entity.get("category") or "other"
                    entity.update(category=category, category_zh=catalog.category_zh(category),
                                  category_en=catalog.category_en(category), source="home_assistant",
                                  ip=device.get("ip", ""), ports=[], controls=[],
                                  fields={**entity.get("fields", {}), "entity_id": entity_id})
                    self._connection_refs[entity["id"]] = device["id"]
                    expanded.append(entity)
            except Exception:
                device["connection_error"] = "Home Assistant 连接失败，请检查地址、访问令牌与网络"
        devices.extend(expanded)
        for device in devices:
            device["configured"] = self._connection_refs.get(device["id"], device["id"]) in self._connections
        self._scan = {"devices": devices}
        self._scan_key = scan_key
        self._scan_at = _now()
        return {"devices": devices, "cached": False, "scanned_at": self._scan_at,
                "scan_time": _ts()}

    async def devices(self, refresh: bool = False) -> list[dict]:
        result = await self.scan(force=refresh)
        return result["devices"]

    # ------------------------------------------------------------------ 设备

    def _build_device(self, scanned) -> dict:
        """把扫描结果整理成统一设备结构。"""
        protocol = str(getattr(scanned, "protocol", "unknown") or "unknown")
        ip = str(getattr(scanned, "ip", "") or "")
        mac = str(getattr(scanned, "mac", "") or "")
        raw_fields = dict(getattr(scanned, "fields", {}) or {})
        fields = discovery._public_value(raw_fields)
        address = str(getattr(scanned, "address", "") or fields.get("address") or mac)
        identity = (
            ip
            or address
            or str(fields.get("device_id") or fields.get("uuid") or fields.get("gw_id") or "")
            or str(getattr(scanned, "name", "") or "")
        )
        device_id = f"{protocol}:{identity}" if identity else f"{protocol}:unknown"
        private = dict(getattr(scanned, "credentials", {}) or {})
        private.update({k: v for k, v in raw_fields.items() if discovery._SENSITIVE_FIELD_RE.search(str(k))})
        if private:
            self._discovered_credentials[device_id] = private
        category = getattr(scanned, "category", "other") or "other"
        name = getattr(scanned, "name", "") or ""
        if not name:
            name = getattr(scanned, "http_title", "") or device_id
        if address and "address" not in fields:
            fields["address"] = address
        return {
            "id": device_id,
            "name": name,
            "model": getattr(scanned, "model", "") or "",
            "category": category,
            "category_zh": catalog.category_zh(category),
            "category_en": catalog.category_en(category),
            "protocol": protocol,
            "platform": getattr(scanned, "platform", "") or "",
            "ip": ip,
            "mac": mac,
            "vendor": getattr(scanned, "vendor", "") or "",
            "ports": list(getattr(scanned, "ports", []) or []),
            "online": True,
            "source": "lan",
            "controls": [],
            "fields": fields,
        }

    async def _cloud_devices(self) -> list[dict]:
        """读取米家云端设备，映射成统一结构。"""
        if self.cloud is None:
            return []
        devices = self.cloud.list_devices()
        out: list[dict] = []
        for item in devices or []:
            did = str(item.get("did") or "")
            if not did:
                continue
            category = catalog.guess_category(str(item.get("model") or ""), str(item.get("name") or ""))
            out.append({
                "id": f"mihome:{did}",
                "name": str(item.get("name") or ""),
                "model": str(item.get("model") or ""),
                "category": category,
                "category_zh": catalog.category_zh(category),
                "category_en": catalog.category_en(category),
                "protocol": "mihome",
                "platform": "mihome",
                "ip": str(item.get("ip") or ""),
                "mac": "",
                "vendor": "",
                "ports": [],
                "online": bool(item.get("online")),
                "source": "cloud",
                "room": str(item.get("room_name") or ""),
                "home": str(item.get("home_name") or ""),
                "controls": [],
                "fields": {},
            })
        return out

    def _find_device(self, device_id: str) -> dict | None:
        requested = str(device_id or "").strip()
        if not requested:
            return None
        devices = self._scan.get("devices", [])
        for device in devices:
            if str(device.get("id") or "") == requested:
                return device
        # 米家工具历史上接受裸 did；只对明确的 mihome:<did> 做前缀兼容，
        # 避免 endswith 把任意设备误匹配到另一个设备。
        for device in devices:
            current = str(device.get("id") or "")
            if current.startswith("mihome:") and current.split(":", 1)[1] == requested:
                return device
            if current.startswith("ble:") and current.split(":", 1)[1] == requested:
                return device
        return None

    async def device(self, device_id: str, refresh: bool = False) -> dict:
        """设备详情：能力项 + 当前值 + 动作。"""
        if refresh or not self._scan.get("devices"):
            await self.scan(force=refresh)
        found = self._find_device(device_id)
        if found is None:
            raise ValueError(f"找不到设备：{device_id}（先扫描，或检查设备 id）")
        adapter = self._adapter_for(found)
        detail = dict(found)
        try:
            controls = localize.localize_controls(await adapter.list_capabilities(), found["protocol"])
        except Exception:
            detail.update(controls=[], values={}, capability_error="无法读取设备能力，请检查连接配置或配对状态")
            return detail
        values: dict = {}
        try:
            values = await adapter.read_all()
        except Exception:  # noqa: BLE001 - 读失败也要把能力项还给前端
            detail["read_error"] = "设备状态读取失败，请检查连接配置或网络"
        detail["controls"] = controls
        detail["values"] = values
        detail["adapter"] = adapter.protocol
        return detail

    def _adapter_for(self, device: dict):
        key = device["id"]
        if key in self._adapters:
            return self._adapters[key]
        protocol = device["protocol"]
        cls = get_adapter_class(protocol)
        if cls is None:
            cls = get_adapter_class(_ADAPTER_ALIASES.get(protocol, ""))
        if cls is None:
            raise ValueError(f"没有 {protocol} 协议的适配器")
        connection = self._connection_for(key)
        source_key = self._connection_refs.get(key, key)
        source_device = device if source_key == key else (self._find_device(source_key) or device)
        # Discovered HA base URLs are public transport metadata; explicit user
        # connection settings always win, and children inherit the hub endpoint.
        if protocol == "home_assistant" and not connection.get("base_url"):
            base_url = source_device.get("fields", {}).get("base_url")
            if base_url:
                connection["base_url"] = base_url
        credentials = {**self._discovered_credentials.get(source_key, {}), **connection}
        scanned = _ScannedView(device, credentials)
        adapter = cls(scanned, {**self.config, **connection, "cloud": self.cloud})
        self._adapters[key] = adapter
        return adapter

    async def read_states(self, device_ids: list[str]) -> dict:
        """批量读多台设备的当前值（面板首屏用）。"""
        if not self._scan.get("devices"):
            await self.scan()
        out: dict = {}
        for device_id in device_ids:
            try:
                adapter = self._adapter_for(self._require(device_id))
                out[device_id] = await adapter.read_all()
            except Exception as exc:  # noqa: BLE001 - 单台失败不影响其余
                print(f"[smart-home] 读取 {device_id} 失败：{exc}")
                out[device_id] = {}
        return out

    def _require(self, device_id: str) -> dict:
        found = self._find_device(device_id)
        if found is None:
            raise ValueError(f"找不到设备：{device_id}")
        return found

    # ------------------------------------------------------------------ 控制

    async def control(
        self,
        device_id: str,
        mode: str,
        name: str = "",
        value=None,
        params: dict | None = None,
        state=_STATE_UNSET,
    ) -> dict:
        """统一控制入口。

        mode:
        - ``power``：开关；``state`` 省略时取反。
        - ``write``：写某个能力项（``name`` 是能力项 key，如 ``brightness``）。
        - ``action``：执行动作（``name`` 是动作名）。
        - ``scrape``：扒设置页。
        """
        if mode == "power" and state is None:
            raise ValueError("state 不能为 null；省略 state 才表示切换电源")
        if mode == "scrape":
            return await self.scrape(device_id)
        if not self._scan.get("devices"):
            await self.scan()
        device = self._require(device_id)
        adapter = self._adapter_for(device)

        if mode == "power":
            power_state = getattr(adapter, "power_state", None)
            if state is _STATE_UNSET:
                current = await power_state() if callable(power_state) else await adapter.read_capability("power")
                if current is None:
                    raise ValueError("当前电源状态未知，请明确传入 state=true 或 false")
                state = not adapter._value_for_capability("power", current)
            else:
                state = adapter._value_for_capability("power", state)
            # 优先走专用电源方法（云端/米家），否则用能力项写入。
            power_method = getattr(adapter, "power", None)
            if callable(power_method):
                result = await power_method(state)
            else:
                result = await adapter.write_capability("power", state)
            if not isinstance(result, dict):
                result = {"ok": False, "error": "适配器未返回有效结果"}
            return {
                **result,
                "ok": result.get("ok") is True,
                "device": device_id,
                "mode": mode,
                "on": adapter._value_for_capability("power", result.get("on", state)) if result.get("ok") is True else None,
            }

        if mode == "write":
            if not name:
                raise ValueError("mode=write 需要 name（能力项，如 brightness）")
            result = await adapter.write_capability(name, value)
            if not isinstance(result, dict):
                result = {"ok": False, "error": "适配器未返回有效结果"}
            return {"device": device_id, "mode": mode, "capability": name, "value": value, **result,
                    "ok": result.get("ok") is True}

        if mode == "action":
            if not name:
                raise ValueError("mode=action 需要 name（动作名）")
            result = await adapter.run_action(name, params)
            if not isinstance(result, dict):
                result = {"ok": False, "error": "适配器未返回有效结果"}
            return {"device": device_id, "mode": mode, "action": name, **result,
                    "ok": result.get("ok") is True}

        raise ValueError(f"未知 mode：{mode}（可选 power / write / action / scrape）")

    async def scrape(self, device_id: str) -> dict:
        """扒一台管理类设备的设置页。"""
        if not self._scan.get("devices"):
            await self.scan()
        device = self._require(device_id)
        adapter = self._adapter_for(device)
        try:
            data = await adapter.scrape()
        except NotImplementedError as exc:
            return {"ok": False, "device": device_id, "error": str(exc), "note": "该设备不支持设置页扒取"}
        data.setdefault("device", device_id)
        data.setdefault("protocol", device["protocol"])
        return data

    # ------------------------------------------------------------------ 场景

    def _scenes_path(self) -> Path:
        path = self.data_dir / "smart_home" / "scenes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def scenes_list(self) -> list[dict]:
        try:
            data = json.loads(self._scenes_path().read_text("utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise ValueError("场景文件无法读取，已保留原文件；请修复后重试") from exc
        if not isinstance(data, dict) or not isinstance(data.get("scenes"), list) or any(not isinstance(s, dict) for s in data["scenes"]):
            raise ValueError("场景文件格式无效，已保留原文件")
        return data["scenes"]

    def _write_scenes(self, data: dict) -> None:
        """原子替换场景文件，避免写到一半断电后损坏整个场景库。"""
        path = self._scenes_path()
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix="scenes-", suffix=".tmp", delete=False
            ) as stream:
                temp_path = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _validate_scene_actions(actions) -> list[dict]:
        if not isinstance(actions, list) or not actions:
            raise ValueError("场景至少需要一个动作")
        normalized = []
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                raise ValueError(f"场景动作 {index + 1} 必须是对象")
            device = str(action.get("device") or "").strip()
            mode = str(action.get("mode") or "write").strip()
            if not device:
                raise ValueError(f"场景动作 {index + 1} 缺少 device")
            if mode not in {"power", "write", "action", "scrape"}:
                raise ValueError(f"场景动作 {index + 1} 的 mode 无效：{mode}")
            if mode in {"write", "action"} and not str(action.get("name") or action.get("capability") or "").strip():
                raise ValueError(f"场景动作 {index + 1} 的 {mode} 模式需要 name")
            if mode == "power" and "state" in action and not isinstance(action["state"], bool):
                raise ValueError(f"场景动作 {index + 1} 的 state 必须是布尔值")
            normalized.append(dict(action))
        return normalized

    def scene_save(self, scene: dict) -> dict:
        """新增或更新一个场景。``scene`` 至少要有 ``name`` 和 ``actions``。"""
        if not isinstance(scene, dict):
            raise ValueError("scene 必须是对象")
        if not str(scene.get("name") or "").strip():
            raise ValueError("场景需要 name")
        actions = self._validate_scene_actions(scene.get("actions"))
        data = {"scenes": self.scenes_list()}
        if scene.get("id"):
            for index, item in enumerate(data["scenes"]):
                if item.get("id") == scene["id"]:
                    merged = {**item, **scene, "actions": actions}
                    data["scenes"][index] = merged
                    self._write_scenes(data)
                    return merged
        # 新增
        new = {
            "id": str(uuid.uuid4()),
            "name": scene["name"],
            "actions": actions,
            "enabled": bool(scene.get("enabled", True)),
            "created_at": _ts(),
        }
        data["scenes"].append(new)
        self._write_scenes(data)
        return new

    def scene_delete(self, scene_id: str) -> bool:
        data = {"scenes": [s for s in self.scenes_list() if s.get("id") != scene_id]}
        existed = len(data["scenes"]) != len(self.scenes_list())
        if existed:
            self._write_scenes(data)
        return existed

    async def scene_run(self, scene_id: str) -> dict:
        """执行一个场景：逐个动作执行，记录每步结果。"""
        scene = next((s for s in self.scenes_list() if s.get("id") == scene_id), None)
        if scene is None:
            raise ValueError(f"找不到场景：{scene_id}")
        if not scene.get("enabled", True):
            raise ValueError("该场景已停用")
        results: list[dict] = []
        success = 0
        actions = scene.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                results.append({"ok": False, "error": f"场景动作 {index + 1} 必须是对象"})
                continue
            device_id = str(action.get("device") or "")
            mode = str(action.get("mode") or "write")
            try:
                out = await self.control(
                    device_id,
                    mode,
                    name=str(action.get("name") or action.get("capability") or ""),
                    value=action.get("value"),
                    params=action.get("params"),
                    **({"state": action["state"]} if "state" in action else {}),
                )
                action_ok = isinstance(out, dict) and out.get("ok") is True
                if action_ok:
                    success += 1
                results.append({"device": device_id, "ok": action_ok, "mode": mode, "result": out})
            except Exception as exc:  # noqa: BLE001 - 单步失败不中断整条场景
                print(f"[smart-home] 场景 {scene_id} 动作失败 {device_id} {mode}: {exc}")
                results.append({"device": device_id, "ok": False, "mode": mode, "error": str(exc)})
        return {"ok": bool(results) and success == len(results), "scene": scene.get("name"), "success": success,
                "total": len(results), "results": results}

    # ------------------------------------------------------------------ 目录

    def catalog(self) -> dict:
        """返回能力项目录（中英对照），供模型与前端展示。"""
        return {
            "categories": [
                {"key": key, "zh": catalog.category_zh(key), "en": catalog.category_en(key)}
                for key in catalog.CATEGORY_LABELS
            ],
            "capabilities": [catalog.capability_info(key) for key in catalog.CAPABILITY_LABELS],
            "enums": {
                name: catalog.enum_values(name) for name in catalog.ENUM_LABELS
            },
        }

    async def close(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.close()
            except Exception:  # noqa: BLE001
                pass
        self._adapters.clear()


class _ScannedView:
    """把统一设备字典包装成适配器需要的 ``device`` 对象。

    适配器基类按属性访问 ``device.ip`` / ``device.mac`` / ``device.category`` /
    ``device.name`` / ``device.did``，这里统一提供。
    """

    def __init__(self, device: dict, credentials: dict | None = None):
        self._device = device
        self.credentials = dict(credentials or {})

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        return self._device.get(item, "")

    @property
    def ip(self) -> str:
        return self._device.get("ip", "")

    @property
    def mac(self) -> str:
        return self._device.get("mac", "")

    @property
    def category(self) -> str:
        return self._device.get("category", "other")

    @property
    def name(self) -> str:
        return self._device.get("name", "")

    @property
    def did(self) -> str:
        value = self._device.get("id", "")
        return value.split(":", 1)[1] if value.startswith("mihome:") else ""

    @property
    def fields(self) -> dict:
        return self._device.get("fields", {})

    @property
    def ports(self) -> list[int]:
        return self._device.get("ports", [])

    @property
    def platform(self) -> str:
        return self._device.get("platform", "")

    @property
    def local_key(self) -> str:
        return self.credentials.get("local_key", "")

    @property
    def version(self) -> str:
        return self._device.get("fields", {}).get("version", "")

    @property
    def gw_id(self) -> str:
        return self._device.get("fields", {}).get("gw_id", "")

    @property
    def dps(self) -> dict:
        return self._device.get("fields", {}).get("dps", {})
