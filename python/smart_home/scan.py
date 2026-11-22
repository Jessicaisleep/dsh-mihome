#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""全屋扫描 CLI。

独立运行，便于排查「插件扫不到设备」的问题：

    .venv\\Scripts\\python.exe python\\smart_home\\scan.py
    .venv\\Scripts\\python.exe python\\smart_home\\scan.py --subnet 192.168.1.0/24 --json

输出每台设备的协议、类别、地址、端口与指纹，不连接云端。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))


async def _run(args) -> int:
    from smart_home import discovery

    extra = []
    if args.subnet:
        extra.append(args.subnet)
    devices = await discovery.discover_all(
        subnets=[args.subnet] if args.subnet else None,
        prefix=args.prefix,
        extra_subnets=extra,
        ports=args.ports,
        scan_ping=not args.no_ping,
    )
    payload = [d.to_dict() for d in devices]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not payload:
            print("没有发现设备。")
        for device in payload:
            print(
                f"{device['ip'] or device['mac']:<18} {device['protocol']:<12} "
                f"{device['category']:<16} {device['name'] or '-':<24} "
                f"ports={device['ports']} mac={device['mac'] or '-'}"
            )
        print(f"\n共 {len(payload)} 台。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="dsh-mihome 全屋设备扫描")
    parser.add_argument("--subnet", help="要扫描的子网，如 192.168.1.0/24（默认扫描本机所有 /24）")
    parser.add_argument("--prefix", type=int, default=24, help="子网前缀，默认 24")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-ping", action="store_true", help="跳过 ping 扫描（只依赖 ARP）")
    parser.add_argument("--ports", help="逗号分隔的端口列表，默认使用内置常见端口")
    args = parser.parse_args()
    if args.ports:
        args.ports = [int(x) for x in args.ports.split(",") if x.strip().isdigit()]
    else:
        args.ports = None
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
