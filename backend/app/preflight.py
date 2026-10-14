from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from .config import CONFIG_PATH, get_config
from .services.runtime_service import diagnostics


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ports", nargs="*", type=int, default=[])
    args = parser.parse_args()
    get_config()
    report = diagnostics()
    failed = False
    resources = {
        "config": CONFIG_PATH,
        "model checkpoint": get_config().model.checkpoint,
        "SAR statistics": get_config().model.sar_stats,
    }
    for name, path in resources.items():
        exists = Path(path).is_file()
        print(f"[{'OK' if exists else 'ERROR'}] {name}: {path}")
        failed = failed or not exists
    for name, item in report["checks"]["paths"].items():
        state = "OK" if item["writable"] else "ERROR"
        print(f"[{state}] {name}: {item['path']}")
        failed = failed or not item["writable"]
    for port in args.ports:
        available = port_available(port)
        print(f"[{'OK' if available else 'ERROR'}] port {port}: {'available' if available else 'already in use'}")
        failed = failed or not available
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
