"""GUI and optional headless scan entry point."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from . import __version__
from .auth import AuthStore
from .config import ScanConfig
from .logging_setup import setup_logging
from .scanner import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RKA-3000 通信测试工具")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--self-test", action="store_true", help="检查打包入口与依赖后退出")
    parser.add_argument("--scan", action="store_true", help="按配置文件执行一次命令行扫描")
    parser.add_argument("--config", type=Path, help="扫描配置 JSON 路径")
    parser.add_argument("--log-file", type=Path, help="日志文件路径")
    args = parser.parse_args(argv)
    if args.self_test:
        from ._vendor.azbil_mfc.protocol import build_read_frame
        from ._vendor.taie_temp.modbus_rtu import append_crc
        from .auth import AdminSession

        assert build_read_frame(1, 0x04B6, 1)
        assert append_crc(b"\x01\x03\x00\x8a\x00\x01")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from .ui import MainWindow

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            window = MainWindow(setup_logging(root / "self-test.log"),
                                AuthStore(root / "auth.json"), AdminSession("self_test"))
            window.close()
            window.deleteLater()
            application.processEvents()
        return 0
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

    from PySide6.QtWidgets import QApplication, QDialog

    from .ui import LoginDialog, MainWindow

    application = QApplication([])
    application.setApplicationName("RKA-3000 通信测试工具")
    auth_store = AuthStore()
    login = LoginDialog(auth_store)
    if login.exec() != QDialog.Accepted or login.session is None:
        return 1
    window = MainWindow(log_path, auth_store, login.session)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
