# SPDX-License-Identifier: GPL-3.0-or-later
"""MiHome-Windows <-> DSH 桥接进程。

DSH 插件本体是 Node，米家协议栈是 Python（mijiaAPI）。本文件是两者之间
唯一的通道：从 stdin 逐行读一个 JSON 请求，往 stdout 写一行 JSON 响应。
一行一问一答，请求带自增 id，调用方据此配对；进程常驻，MijiaService
在进程内只构造一次（上游客户端带 requests.Session 与本地缓存，反复
构造既慢又会重复读盘）。

为什么不复用 app/ui：整个 app.ui 与 app.core.jobs 都 import PySide6，
而米家控制本身并不需要 Qt。通过 app.core.service 调用已适配的米家能力，
也让插件不必为插件本体拖一份 200MB 的 Qt 运行时。

日志一律走 stderr——stdout 是协议通道，任何多余字符都会让 Node 侧
JSON.parse 失败。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path

# 源码模式下 app 包在 <插件>/python 下；本文件也在同一目录，直接可 import。
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# MIHOME_DATA_DIR 必须在 import app 之前设好：app.core._json_store.data_dir()
# 在模块内被调用时读取环境变量，晚了就会落到 python 源码目录，把配置写进
# 插件自身（升级/重装时被覆盖）。
if not os.environ.get("MIHOME_DATA_DIR"):
    os.environ["MIHOME_DATA_DIR"] = str(_HERE.parent / "data")

import logging  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("MIHOME_BRIDGE_LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

from app.core.service import MijiaService  # noqa: E402
from app.core import spec_zh_ext  # noqa: E402

_service: MijiaService | None = None
_service_auth_mtime: float = 0.0


def _auth_file() -> Path | None:
    """mijiaAPI 的凭据文件路径（与 mijiaAPI CLI 共用）。"""
    try:
        return Path.home() / ".config" / "mijia-api" / "auth.json"
    except Exception:  # noqa: BLE001 - 拿不到就以 mtime 0 处理，不阻断主流程
        return None


def service() -> MijiaService:
    """懒构造，并在凭据文件被别的进程改写后自动重建。

    为什么需要重建：登录成功后是**另一个进程**（mijiaAPI CLI / 我们的
    login_wait 子流程）把 auth.json 写盘的，而已经构造好的 MijiaService
    里缓存着旧 session 与空的 auth_data，不重建就永远"未登录"。
    重建很便宜（只读一次 json），但只在 mtime 变了时才做。
    """
    global _service, _service_auth_mtime
    auth = _auth_file()
    mtime = 0.0
    if auth is not None:
        try:
            mtime = auth.stat().st_mtime
        except OSError:
            mtime = 0.0
    if _service is None or mtime != _service_auth_mtime:
        _service = MijiaService()
        _service_auth_mtime = mtime
    return _service


def _plain(value):
    """dataclass / 元组 / 其他非 JSON 类型转成可序列化结构。"""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _err_code(exc: BaseException) -> str:
    """给异常一个稳定的机器可读分类，Node 侧据此决定要不要引导用户登录。"""
    if isinstance(exc, OperationTimeout):
        return "operation_timeout"
    name = type(exc).__name__
    text = str(exc)
    if "ServiceError" in name:
        if "登录" in text or "login" in text.lower() or "Token" in text:
            return "need_login"
        return "service_error"
    if isinstance(exc, ImportError):
        return "dependency_missing"
    if isinstance(exc, OSError):
        return "io_error"
    return "error"


# ---------------------------------------------------------------- 全屋智能家居

# 局域网扫描/控制是 asyncio 写的，而桥接主循环是同步逐行读 stdin。这里起一条
# 常驻后台事件循环线程，处理函数用 run_coroutine_threadsafe 投递进去等结果。
# 主循环仍串行等候请求完成；Node 取消/超时时终止桥接，避免继续处理排队请求。
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_manager: object | None = None


def _event_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, daemon=True,
                                      name="smart-home-loop")
            thread.start()
            _loop = loop
        return _loop


class OperationTimeout(TimeoutError):
    """The shared process must exit: cancellation cannot stop blocking threads."""


def _run_async(coro, timeout: float | None = None):
    async def capture_device_timeout():
        try:
            return await coro, None
        except TimeoutError as exc:
            # Future.result also raises TimeoutError for a finished device call.
            # Carry that exception as data so it cannot be confused with our budget.
            return None, exc

    future = asyncio.run_coroutine_threadsafe(capture_device_timeout(), _event_loop())
    try:
        result, device_timeout = future.result(timeout=timeout)
    except TimeoutError:
        # Cancellation cannot interrupt a blocking cloud call or to_thread worker.
        # The dispatcher marks this process terminal; main exits without joining
        # workers and Node invalidates every queued request. Remote effects remain unknown.
        future.cancel()
        raise OperationTimeout("操作超时，执行结果未知；请先读取设备状态，再决定是否重试") from None
    if device_timeout is not None:
        raise device_timeout
    return result


def smart_home_manager():
    """懒构造全屋智能家居管理器（米家云为可选，失败则仅走局域网）。"""
    global _manager
    if _manager is None:
        try:
            from smart_home.cloud import MiHomeCloudProxy
            from smart_home.manager import SmartHomeManager
        except ImportError as exc:
            raise RuntimeError(f"全屋智能家居依赖缺失：{exc}") from exc
        cloud = None
        try:
            cloud = MiHomeCloudProxy(service())
        except Exception as exc:  # noqa: BLE001 - 未登录/无凭据不该阻断局域网
            print(f"[smart-home] 米家云端不可用，本次仅用局域网：{exc}", file=sys.stderr)
        data_dir = os.environ.get("MIHOME_DATA_DIR") or str(_HERE.parent / "data")
        _manager = SmartHomeManager({"data_dir": data_dir, "cloud": cloud})
    else:
        from smart_home.cloud import MiHomeCloudProxy
        try:
            current = service()
            if getattr(_manager.cloud, "_svc", None) is not current:
                _manager.cloud = MiHomeCloudProxy(current)
                _manager.config["cloud"] = _manager.cloud
                # Invalidate adapters which still reference the previous session.
                _run_async(_manager.close(), timeout=5)
                _manager._scan = {}
        except OperationTimeout:
            raise  # A timed-out worker must not survive in a reusable bridge.
        except Exception:
            pass  # Local connections remain usable when Mi Home is unavailable.
    return _manager


# ---------------------------------------------------------------- handlers

def op_status(_args):
    svc = service()
    return {
        "logged_in": svc.login_status(),
        "api_version": svc.api_version(),
        "data_dir": os.environ.get("MIHOME_DATA_DIR", ""),
    }


def _qr_ascii(url: str) -> str:
    """把登录 URL 渲染成终端可直接看的方块二维码。

    自己按 matrix 拼字符串而不用 qrcode.print_ascii()：后者默认往 stdout
    打，而 stdout 是本进程的协议通道，多一个字都会让 Node 侧解析失败。
    两行一并（▀ 结合上半白下半黑）让二维码在等宽字体里保持方形比例。
    """
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    lines = []
    for row in range(0, len(matrix), 2):
        top = matrix[row]
        bottom = matrix[row + 1] if row + 1 < len(matrix) else [False] * len(top)
        buf = []
        for x in range(len(top)):
            t, b = top[x], bottom[x]
            buf.append("█" if t and b else "▀" if t else "▄" if b else " ")
        lines.append("".join(buf))
    return "\n".join(lines)


def _save_qr_png(data: dict) -> str:
    """把本次的二维码存成**唯一文件名**的 PNG，返回路径。

    两个坑，都是踩出来的：
    1. 每次都用同一个文件名 mihome-login.png 时，前端/图片查看器按路径缓存，
       用户扫到的还是上一次的旧码 —— 表现为"刚发的码就提示已过期"。所以文件名
       带 ticket 与毫秒时间戳，每次必然是新路径。
    2. 优先用上游返回的 `qr` 图片地址（account.xiaomi.com/pass/qr/login），
       它和小米 APP 内同源；自己把 `loginUrl` 渲染成二维码虽然也能扫，但一旦
       上游改写链接格式就会失效。`qr` 拿不到时才回退本地渲染。
    """
    png_dir = Path(os.environ.get("MIHOME_DATA_DIR", _HERE)) / "qr"
    png_dir.mkdir(parents=True, exist_ok=True)
    ticket = ""
    lp = data.get("lp") or ""
    if "k=" in lp:
        ticket = lp.rsplit("k=", 1)[-1].split("&")[0][:16]
    stamp = f"{int(time.time() * 1000)}"
    tag = f"{ticket}-{stamp}" if ticket else stamp
    png_path = png_dir / f"mihome-login-{tag}.png"

    qr_url = data.get("qr") or ""
    if qr_url:
        try:
            import requests

            resp = requests.get(
                qr_url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"},
                timeout=15,
            )
            if resp.status_code == 200 and resp.content[:8] == b"\x89PNG\r\n\x1a\n":
                # 官方图是 240x240，手机对着屏幕扫略小；用最近邻放大 2 倍保持
                # 像素边界锐利（不要用双线性，会把模块边缘糊掉导致识别率下降）。
                try:
                    import io

                    from PIL import Image

                    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                    if img.width < 480:
                        img = img.resize((img.width * 2, img.height * 2), Image.NEAREST)
                    img.save(png_path, format="PNG")
                except Exception:  # noqa: BLE001 - 放大失败就用原图
                    png_path.write_bytes(resp.content)
                return str(png_path)
            print(
                f"官方 QR 图片不可用（status={resp.status_code}, "
                f"len={len(resp.content)}），回退本地渲染",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 - 回退即可，不中断登录
            print(f"官方 QR 图片拉取失败：{exc}", file=sys.stderr)

    import qrcode

    url = data.get("loginUrl", "")
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(png_path)
    return str(png_path)


def op_login_qr(_args):
    """开始扫码登录，并把二维码一起渲染好（ASCII + PNG 文件）。

    PNG 落盘给前端展示用，ASCII 给纯文本会话用；两者都只是载体，
    真正的凭据是 login_data，必须原样回传给 op_login_wait。
    """
    svc = service()
    data = svc.qr_login_begin()
    if data is None or data.get("refreshed"):
        return {"logged_in": svc.login_status(), "need_scan": False, "login_data": None}

    url = data.get("loginUrl", "")
    png_path = ""
    try:
        png_path = _save_qr_png(data)
    except Exception as exc:  # noqa: BLE001 - PNG 只是附带产物，失败不影响登录
        print(f"QR PNG 生成失败（不影响登录）：{exc}", file=sys.stderr)
        png_path = ""

    return {
        "need_scan": True,
        "login_url": url,
        "qr_ascii": _qr_ascii(url),
        "qr_png": png_path,
        "expires_in": data.get("timeout"),
        "login_data": _plain(data),
    }


def op_login_wait(args):
    """长轮询扫码结果；阻塞到扫码完成或上游超时（最长约 2 分钟）。"""
    svc = service()
    data = args.get("login_data")
    if not data:
        raise ValueError("login_wait 需要 login_data（先调用 mihome_login_begin）")
    svc.qr_login_wait(data)
    return {"logged_in": svc.login_status()}


def op_devices(_args):
    svc = service()
    devices = svc.list_devices()
    # 未改名的国际版设备云端只给英文默认名（Mi Smart Laser Measure），
    # 米家 APP 显示的是产品库中文名。两级兜底：先看已缓存的 spec 名，
    # 再查产品页（结果按 model 落盘缓存，只有首次会花几秒）。
    zh = svc.localized_product_names(
        [str(d.did) for d in devices], {str(d.did): str(d.name) for d in devices})
    for device in devices:
        did, name = str(device.did), str(device.name)
        if did in zh or not name or spec_zh_ext.CJK.search(name):
            continue
        try:
            product = svc.product_page_name(str(device.model))
        except Exception as exc:  # noqa: BLE001 - 查不到就用英文原名
            print(f"查产品名失败 {device.model}: {exc}", file=sys.stderr)
            continue
        if product:
            zh[did] = product
    result = []
    for item in _plain(devices):
        if isinstance(item, dict) and item.get("did") in zh:
            item["cloud_name"] = item.get("name")
            item["name"] = zh[item["did"]]
        result.append(item)
    return {"devices": result}



def op_device_detail(args):
    did = _require(args, "did")
    detail = service().device_detail(did)
    payload = _plain(detail)
    for prop in payload.get("props", []):
        name = str(prop.get("name") or "")
        prop["desc"] = spec_zh_ext.prop_zh(name, str(prop.get("desc") or ""))
        prop["unit"] = spec_zh_ext.UNITS.get(name, "")
        prop["advanced"] = name in spec_zh_ext.ADVANCED_PROPS
        localized = spec_zh_ext.localize_values(
            prop.get("value_list"), prop.get("unit") or "", name)
        if localized is not None:
            prop["value_list"] = localized
    for action in payload.get("actions", []):
        action["desc"] = spec_zh_ext.action_zh(
            str(action.get("name") or ""), str(action.get("desc") or ""))
    return payload


def op_read_props(args):
    svc = service()
    did = _require(args, "did")
    names = args.get("names")
    if not names:
        raise ValueError("read_props 需要 names（字符串数组）")
    return {"values": _plain(svc.read_props(did, [str(n) for n in names]))}


def op_write_prop(args):
    svc = service()
    did = _require(args, "did")
    name = _require(args, "name")
    if "value" not in args:
        raise ValueError("write_prop 需要 value")
    svc.write_prop(did, name, args["value"])
    return {"ok": True}


def op_action(args):
    svc = service()
    did = _require(args, "did")
    name = _require(args, "name")
    svc.run_action(did, name, args.get("params"))
    return {"ok": True}


def op_power(args):
    """电源：不带 state 时按当前状态取反。"""
    svc = service()
    did = _require(args, "did")
    if "state" in args:
        if not isinstance(args["state"], bool):
            raise ValueError("state 必须为布尔值")
        state = args["state"]
        svc.set_power_state(did, state)
        return {"on": state}
    current = svc.power_state(did)
    state = svc.toggle_power(did, current)
    return {"on": state}


def op_power_states(args):
    """批量读电源状态，给面板首屏用。

    单台超时不该拖垮整张表，所以逐台 try：读失败的设备在返回值里缺席，
    前端按「未知」渲染，而不是整块报错。
    """
    svc = service()
    dids = args.get("dids") or []
    if not dids:
        return {"states": {}}
    states = {}
    for did in [str(d) for d in dids]:
        try:
            states[did] = svc.power_state(did)
        except Exception as exc:  # noqa: BLE001 - 单台失败不影响其余
            print(f"读电源状态失败 {did}: {exc}", file=sys.stderr)
    return {"states": states}


def op_scenes(args):
    return {"scenes": _plain(service().scene_list(args.get("home_name")))}


def op_run_scene(args):
    svc = service()
    scene_id = _require(args, "scene_id")
    home_id = _require(args, "home_id")
    name = str(args.get("name") or "")
    # 上游 run_scene 需要 SceneInfo 结构，这里按字段重建
    from app.core.models import SceneInfo

    svc.run_scene(SceneInfo(scene_id=scene_id, name=name, home_id=home_id))
    return {"ok": True}


def op_metrics(args):
    """读传感器外显值。

    read_metrics 返回的是 did -> 已格式化文案（如 "26.5°C 58%"，读不到的
    设备为 None），不是原始数值字典——量纲换算在 format_metric 里做过了。
    所以这里不要再调 format_metrics_text，那个函数收的是「属性名 -> 数值」。
    selections 可选：{did: [属性名, ...]}，不传则用 service 默认的温湿度。
    """
    svc = service()
    dids = args.get("dids") or []
    if not dids:
        raise ValueError("metrics 需要 dids（字符串数组）")
    selections = args.get("selections") or None
    values = svc.read_metrics([str(d) for d in dids], selections=_plain(selections) if selections else None)
    return {"values": _plain(values)}


def op_consumables(_args):
    svc = service()
    # consumable_low_count() 返回 (总项数, 偏低项数) 元组，不是单个数字
    total, low = svc.consumable_low_count()
    return {
        "items": _plain(svc.consumable_items()),
        "total": total,
        "low_count": low,
    }


def op_icons(args):
    """model → 本地图标文件名（已下载到 data/icons）。

    米家 APP 的首页靠产品图区分设备，只有 URL 没法渲染（CDN 要求签名
    且证书链在部分 Windows 上会报吊销检查失败），所以这里顺手落盘，
    由 Host 路由以二进制转发给前端。
    """
    models = args.get("models") or []
    if not models:
        raise ValueError("icons 需要 models（字符串数组）")
    svc = service()
    wanted = [str(m) for m in models]
    urls = svc.icon_urls(wanted)
    downloaded: dict[str, str] = {}
    for model in wanted:
        url = urls.get(model)
        if not url:
            continue
        try:
            path = svc.icon_file(model, url)
        except Exception as exc:  # noqa: BLE001 - 单型号失败不影响其它
            print(f"下载图标失败 {model}: {exc}", file=sys.stderr)
            continue
        if path is not None:
            downloaded[model] = Path(path).name
    return {"icons": downloaded}


def op_homes(_args):
    """家庭与房间树：首页按 家庭 → 房间 分区就靠它。

    设备接口里的 home/room 字段对共享设备和路由器并不可靠（实测有几台
    回来是「未知」），而 get_homes_list 是米家 APP 自己在用的权威结构，
    所以房间分区直接用它。
    """
    svc = service()
    try:
        homes = svc._api.get_homes_list()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"获取家庭列表失败: {exc}") from exc
    result = []
    for home in homes:
        rooms = []
        for room in home.get("roomlist") or []:
            rooms.append({
                "id": str(room.get("id") or ""),
                "name": str(room.get("name") or ""),
                "dids": [str(d) for d in (room.get("dids") or [])],
            })
        result.append({
            "id": str(home.get("id") or ""),
            "name": str(home.get("name") or ""),
            "dids": [str(d) for d in (home.get("dids") or [])],
            "rooms": rooms,
        })
    return {"homes": result}


def op_names(args):
    """model → 产品库中文名（home.miot-spec.com 产品页）。

    只用于展示：部分国际版设备云端名是纯英文（Xiaomi Smart Doorbell 3），
    米家 APP 显示的是「小米智能门铃3」。
    """
    models = args.get("models") or []
    if not models:
        raise ValueError("names 需要 models（字符串数组）")
    svc = service()
    names: dict[str, str] = {}
    for model in [str(m) for m in models]:
        try:
            name = svc.product_page_name(model)
        except Exception as exc:  # noqa: BLE001
            print(f"查产品名失败 {model}: {exc}", file=sys.stderr)
            continue
        if name:
            names[model] = name
    return {"names": names}


def op_smart_home_scan(args):
    """扫描本网段，识别全屋智能家居（含 BLE）并叠加米家云端设备。"""
    mgr = smart_home_manager()
    return _run_async(
        mgr.scan(
            subnets=args.get("subnets") or None,
            prefix=args.get("prefix", 24),
            extra_subnets=args.get("extra_subnets") or None,
            ports=args.get("ports") or None,
            scan_ping=args.get("scan_ping", True),
            force=bool(args.get("force", False)),
        ),
        timeout=180,
    )


def op_smart_home_devices(args):
    """全屋设备列表（默认 60 秒缓存，refresh=True 强制重扫）。"""
    mgr = smart_home_manager()
    return {"devices": _run_async(
        mgr.devices(refresh=bool(args.get("refresh", False))), timeout=180)}


def op_smart_home_device(args):
    """单台设备详情：能力项（已翻译）+ 当前值。"""
    mgr = smart_home_manager()
    return _run_async(
        mgr.device(_require(args, "id"), refresh=bool(args.get("refresh", False))),
        timeout=180,
    )


def op_smart_home_control(args):
    """统一控制：mode = power / write / action / scrape。"""
    mgr = smart_home_manager()
    return _run_async(
        mgr.control(
            _require(args, "device_id"),
            str(args.get("mode") or "power"),
            name=str(args.get("name") or ""),
            value=args.get("value"),
            params=args.get("params"),
            **({"state": args["state"]} if "state" in args else {}),
        ),
        timeout=180,
    )


def op_smart_home_scrape(args):
    """扒管理类设备（路由器 / 交换机 / 光猫 / NAS）的设置页。"""
    mgr = smart_home_manager()
    return _run_async(mgr.scrape(_require(args, "device_id")), timeout=180)


def op_smart_home_scenes(args):
    """自定义场景：mode = list / save / run / delete。

    之前只实现了 list，Node 侧却声明了四种模式——save/run/delete 会落到
    「没有返回」的静默路径。这里补齐，并让返回值结构与前端/渲染一致。
    """
    mode = str(args.get("mode") or "list")
    mgr = smart_home_manager()
    if mode == "list":
        return {"ok": True, "mode": mode, "scenes": mgr.scenes_list()}
    if mode == "save":
        scene = args.get("scene") or args
        if not isinstance(scene, dict):
            raise ValueError("mode=save 需要 scene 对象（含 name 与 actions）")
        saved = mgr.scene_save(scene)
        return {"ok": True, "mode": mode, "saved": saved, "scenes": mgr.scenes_list()}
    if mode == "delete":
        deleted = mgr.scene_delete(_require(args, "scene_id"))
        return {"ok": deleted, "mode": mode, "deleted": deleted, "scenes": mgr.scenes_list()}
    if mode == "run":
        result = _run_async(mgr.scene_run(_require(args, "scene_id")), timeout=180)
        return {"mode": mode, **result}
    raise ValueError(f"未知 mode：{mode}（可选 list / save / run / delete）")


def op_smart_home_scene_save(args):
    scene = args.get("scene") or args
    return smart_home_manager().scene_save(scene)


def op_smart_home_scene_delete(args):
    return {"deleted": smart_home_manager().scene_delete(_require(args, "scene_id"))}


def op_smart_home_scene_run(args):
    mgr = smart_home_manager()
    return _run_async(mgr.scene_run(_require(args, "scene_id")), timeout=180)


def op_smart_home_catalog(_args):
    """能力项目录（中英对照），供模型与前端使用。"""
    return smart_home_manager().catalog()


HANDLERS = {
    "status": op_status,
    "login_begin": op_login_qr,
    "login_wait": op_login_wait,
    "devices": op_devices,
    "device_detail": op_device_detail,
    "read_props": op_read_props,
    "write_prop": op_write_prop,
    "action": op_action,
    "power": op_power,
    "power_states": op_power_states,
    "scenes": op_scenes,
    "run_scene": op_run_scene,
    "metrics": op_metrics,
    "consumables": op_consumables,
    "icons": op_icons,
    "homes": op_homes,
    "names": op_names,
    "smart_home_scan": op_smart_home_scan,
    "smart_home_devices": op_smart_home_devices,
    "smart_home_device": op_smart_home_device,
    "smart_home_control": op_smart_home_control,
    "smart_home_scrape": op_smart_home_scrape,
    "smart_home_scenes": op_smart_home_scenes,
    "smart_home_scene_save": op_smart_home_scene_save,
    "smart_home_scene_delete": op_smart_home_scene_delete,
    "smart_home_scene_run": op_smart_home_scene_run,
    "smart_home_catalog": op_smart_home_catalog,
}


def _require(args: dict, key: str) -> str:
    value = args.get(key)
    if not value:
        raise ValueError(f"缺少参数 {key}")
    return str(value)


def handle(request: dict) -> dict:
    req_id = request.get("id")
    op = str(request.get("op") or "")
    handler = HANDLERS.get(op)
    if handler is None:
        return {"id": req_id, "ok": False, "error": f"未知 op：{op}", "code": "unknown_op"}
    try:
        args = dict(request.get("args") or {})
        connections = args.pop("_connections", {})
        if op.startswith("smart_home_"):
            _run_async(smart_home_manager().set_connections(connections), timeout=10)
        return {"id": req_id, "ok": True, "result": _plain(handler(args))}
    except BaseException as exc:  # noqa: BLE001 - 顶点不能崩，错误要回给调用方
        traceback.print_exc(file=sys.stderr)
        return {
            "id": req_id,
            "ok": False,
            "error": str(exc) or type(exc).__name__,
            "code": _err_code(exc),
        }


def main() -> int:
    # 行缓冲：不 flush 的话 Node 侧会一直等不到响应
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps(
                {"id": None, "ok": False, "error": f"请求不是合法 JSON：{exc}", "code": "bad_json"},
                ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        response = handle(request)
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if response.get("code") == "operation_timeout":
            # Normal interpreter shutdown waits for executor threads. Force this
            # dedicated bridge process to exit before accepting another request.
            os._exit(124)
    return 0


if __name__ == "__main__":
    sys.exit(main())
