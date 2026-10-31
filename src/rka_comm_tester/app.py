"""Application entry points for the layered desktop and CLI adapters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .logging_setup import setup_logging
from .profiles import FileProfileStore
from .scanner import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RKA-3000 只读通信测试工具")
    parser.add_argument("--api", action="store_true", help="启动供 Tauri 使用的本地 JSON API")
    parser.add_argument("--legacy-ui", action="store_true", help="启动可选的旧 Qt 界面")
    parser.add_argument("--host", default="127.0.0.1", help="API 监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8765, help="API 监听端口")
    parser.add_argument("--scan", action="store_true", help="按配置文件执行一次命令行扫描")
    parser.add_argument("--config", type=Path, help="扫描配置 JSON 路径")
    parser.add_argument("--log-file", type=Path, help="日志文件路径")
    args = parser.parse_args(argv)
    if args.api and args.scan:
        parser.error("--api 与 --scan 不能同时使用")
    if args.api:
        from .api import run_server

        run_server(args.host, args.port, args.log_file)
        return 0
    if not args.scan and not args.legacy_ui:
        print("桌面窗口请运行 npm run tauri dev；命令行扫描使用 --scan --config <路径>。")
        return 0
    log_path = setup_logging(args.log_file, console=args.scan)
    if args.scan:
        if args.config is None:
            parser.error("--scan 需要 --config")
        try:
            cfg = FileProfileStore(args.config).load()
            if cfg is None:
                raise ValueError("配置文件不存在")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            parser.error(f"配置文件无效: {exc}")
        results = list(scan(cfg))
        print(json.dumps([item.to_dict() for item in results], ensure_ascii=False, indent=2))
        return 0 if any(item.status == "online" for item in results) else 2

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        parser.error('旧 Qt 界面需要安装：python -m pip install -e ".[legacy-ui]"')

    from .ui import MainWindow

    application = QApplication([])
    application.setApplicationName("RKA-3000 通信测试工具")
    window = MainWindow(log_path)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
