"""GUI and optional headless scan entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ScanConfig
from .logging_setup import setup_logging
from .scanner import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RKA-3000 只读通信测试工具")
    parser.add_argument("--scan", action="store_true", help="按配置文件执行一次命令行扫描")
    parser.add_argument("--config", type=Path, help="扫描配置 JSON 路径")
    parser.add_argument("--log-file", type=Path, help="日志文件路径")
    args = parser.parse_args(argv)
    log_path = setup_logging(args.log_file, console=args.scan)
    if args.scan:
        if args.config is None:
            parser.error("--scan 需要 --config")
        try:
            cfg = ScanConfig.load(args.config)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            parser.error(f"配置文件无效: {exc}")
        results = list(scan(cfg))
        print(json.dumps([item.to_dict() for item in results], ensure_ascii=False, indent=2))
        return 0 if any(item.status == "online" for item in results) else 2

    from PySide6.QtWidgets import QApplication

    from .ui import MainWindow

    application = QApplication([])
    application.setApplicationName("RKA-3000 通信测试工具")
    window = MainWindow(log_path)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
