import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from rka_comm_tester.auth import AdminSession, AuthStore  # noqa: E402
from rka_comm_tester.logging_setup import setup_logging  # noqa: E402
from rka_comm_tester.scanner import ScanResult  # noqa: E402
from rka_comm_tester.ui import MainWindow  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def test_window_starts_without_device_or_port(tmp_path, monkeypatch):
    monkeypatch.setattr("rka_comm_tester.ui.available_ports", lambda: [])
    window = MainWindow(setup_logging(tmp_path / "communication.log"),
                        AuthStore(tmp_path / "auth.json"), AdminSession("admin"))
    assert window.port.currentText() == ""
    assert window.scan_button.isEnabled()
    assert window.table.rowCount() == 0
    window._show_result(ScanResult("taie", "COM7", 1, "online", {"pv_c": 100.0}))
    window._show_result(ScanResult("taie", "COM7", 1, "offline", detail="timeout"))
    assert window.table.rowCount() == 1
    assert window.table.item(0, 4).text() == "offline"
    window.close()
    window.deleteLater()
    _APP.processEvents()
