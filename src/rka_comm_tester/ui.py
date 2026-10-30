"""Windows-oriented Qt diagnostic workbench; serial I/O stays off the UI thread."""

from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .auth import AdminSession, AuthStore
from .config import ScanConfig
from .operations import WritePreview, execute_write, preview_write
from .runs import RunStore, export_bundle
from .scanner import ScanResult, available_ports, scan
from .stress import StressConfig, run_stress
from .workers import PortWorkers

logger = logging.getLogger(__name__)


class _Bridge(QObject):
    result = Signal(object)
    progress = Signal(object)
    done = Signal(object)


class LoginDialog(QDialog):
    def __init__(self, store: AuthStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.session: AdminSession | None = None
        first_run = not store.initialized
        self.setWindowTitle("建立管理员账号" if first_run else "管理员登录")
        self.setMinimumWidth(360)
        form = QFormLayout(self)
        form.addRow(QLabel("首次使用请创建账号；没有默认密码。" if first_run else "请输入本地管理员账号和密码。"))
        self.username = QLineEdit()
        self.username.setPlaceholderText("3–32 位字母、数字或下划线")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("至少 12 位")
        form.addRow("账号", self.username)
        form.addRow("密码", self.password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        if first_run:
            form.addRow("确认密码", self.confirm)
        button = QPushButton("创建并进入" if first_run else "登录")
        button.setObjectName("primary")
        button.clicked.connect(self._submit)
        form.addRow(button)
        self.password.returnPressed.connect(self._submit)
        self.confirm.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        username, password = self.username.text().strip(), self.password.text()
        try:
            if not self.store.initialized:
                if password != self.confirm.text():
                    raise ValueError("两次密码不一致")
                self.store.initialize(username, password)
                logger.warning("admin account initialized: %s", username)
            elif not self.store.verify(username, password):
                logger.warning("admin login failed: %s", username)
                raise ValueError("账号或密码错误")
        except (ValueError, PermissionError, OSError) as exc:
            QMessageBox.warning(self, "无法登录", str(exc))
            self.password.clear()
            self.confirm.clear()
            return
        self.session = AdminSession(username)
        logger.info("admin login succeeded: %s", username)
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, log_path: Path, auth_store: AuthStore, session: AdminSession) -> None:
        super().__init__()
        self.log_path = log_path
        self.auth_store = auth_store
        self.session = session
        self.workers = PortWorkers()
        self.bridge = _Bridge()
        self.bridge.result.connect(self._show_result)
        self.bridge.progress.connect(self._show_progress)
        self.bridge.done.connect(self._task_done)
        self._callbacks: dict[Future, object] = {}
        self._result_rows: dict[tuple[str, str, int | None], int] = {}
        self._scan_cancel: Event | None = None
        self._stress_cancel: Event | None = None
        self._closing = False
        self.preview: WritePreview | None = None
        self.last_run: Path | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.start_scan)
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(30_000)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_timer.start()
        QApplication.instance().installEventFilter(self)
        self.setWindowTitle("RKA-3000 通信测试工具 · 管理员")
        self.resize(1120, 760)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 16)
        title = QLabel("设备通信测试")
        title.setObjectName("title")
        outer.addWidget(title)
        outer.addWidget(QLabel("Windows 现场调试 · 三协议扫描、单次写入与稳定性测试"))
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self._build_device_tab()
        self._build_write_tab()
        self._build_stress_tab()
        self._build_log_tab()
        self.setStyleSheet("""
            QMainWindow { background: #f5f7fa; color: #1f2937; }
            QGroupBox { border: 1px solid #d9e0e8; border-radius: 6px; margin-top: 10px; padding: 10px; background: white; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; font-weight: 600; }
            QLabel#title { font-size: 22px; font-weight: 700; color: #14283f; }
            QPushButton { padding: 6px 12px; border: 1px solid #c5ced9; border-radius: 4px; background: white; }
            QPushButton:hover { background: #e9f1fb; }
            QPushButton#primary { background: #1769aa; color: white; border-color: #1769aa; font-weight: 600; }
            QPushButton#primary:hover { background: #145b93; }
            QTableWidget, QPlainTextEdit { background: white; border: 1px solid #d9e0e8; }
        """)
        self.device.currentIndexChanged.connect(self._device_changed)
        self._device_changed()
        self.refresh_ports()

    def _build_device_tab(self) -> None:
        page = QWidget()
        self.tabs.addTab(page, "设备连接")
        layout = QVBoxLayout(page)
        form_box = QGroupBox("连接参数")
        form = QFormLayout(form_box)
        layout.addWidget(form_box)
        self.device = QComboBox()
        for label, value in (("TAIE 温控仪", "taie"), ("Azbil MFC", "azbil"), ("电子天平", "balance")):
            self.device.addItem(label, value)
        form.addRow("设备协议", self.device)
        port_row = QHBoxLayout()
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(260)
        port_row.addWidget(self.port)
        refresh = QPushButton("刷新串口")
        refresh.clicked.connect(self.refresh_ports)
        port_row.addWidget(refresh)
        port_row.addStretch()
        form.addRow("串口", port_row)
        serial_row = QHBoxLayout()
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"])
        self.baud.setCurrentText("19200")
        self.parity = QComboBox()
        self.parity.addItems(["N", "E", "O"])
        self.stopbits = QComboBox()
        self.stopbits.addItems(["1", "2"])
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0.05, 10)
        self.timeout.setDecimals(2)
        self.timeout.setValue(0.5)
        for label, widget in (("波特率", self.baud), ("校验", self.parity), ("停止位", self.stopbits), ("超时/秒", self.timeout)):
            serial_row.addWidget(QLabel(label))
            serial_row.addWidget(widget)
        serial_row.addStretch()
        form.addRow("串口参数", serial_row)
        range_row = QHBoxLayout()
        self.start_address = QSpinBox()
        self.end_address = QSpinBox()
        for widget in (self.start_address, self.end_address):
            widget.setRange(1, 247)
        self.end_address.setValue(6)
        for label, widget in (("起始", self.start_address), ("结束", self.end_address)):
            range_row.addWidget(QLabel(label))
            range_row.addWidget(widget)
        range_row.addStretch()
        form.addRow("站号/地址", range_row)
        balance_row = QHBoxLayout()
        self.balance_protocol = QComboBox()
        self.balance_protocol.addItem("GS-CPA", "gs_cpa")
        self.balance_protocol.addItem("龙腾", "longteng")
        self.balance_lines = QSpinBox()
        self.balance_lines.setRange(1, 100)
        self.balance_lines.setValue(10)
        self.balance_start = QCheckBox("发送 O1 启动连续输出（仅 GS-CPA）")
        balance_row.addWidget(self.balance_protocol)
        balance_row.addWidget(QLabel("最多读取帧数"))
        balance_row.addWidget(self.balance_lines)
        balance_row.addWidget(self.balance_start)
        balance_row.addStretch()
        form.addRow("天平选项", balance_row)
        limit_row = QHBoxLayout()
        self.temp_min = QDoubleSpinBox()
        self.temp_max = QDoubleSpinBox()
        for widget in (self.temp_min, self.temp_max):
            widget.setRange(-1000, 3000)
            widget.setSuffix(" °C")
        self.temp_max.setValue(1000)
        limit_row.addWidget(QLabel("最小"))
        limit_row.addWidget(self.temp_min)
        limit_row.addWidget(QLabel("最大"))
        limit_row.addWidget(self.temp_max)
        limit_row.addStretch()
        form.addRow("温控写入限值", limit_row)
        action_row = QHBoxLayout()
        self.scan_button = QPushButton("开始扫描")
        self.scan_button.setObjectName("primary")
        self.scan_button.clicked.connect(self.start_scan)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_scan)
        save_button = QPushButton("保存配置")
        save_button.clicked.connect(self.save_config)
        load_button = QPushButton("加载配置")
        load_button.clicked.connect(self.load_config)
        for widget in (self.scan_button, self.stop_button, save_button, load_button):
            action_row.addWidget(widget)
        self.auto_check = QCheckBox("自动复查")
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(5, 300)
        self.auto_interval.setValue(30)
        self.auto_interval.setSuffix(" 秒")
        action_row.addWidget(self.auto_check)
        action_row.addWidget(self.auto_interval)
        action_row.addStretch()
        layout.addLayout(action_row)
        self.status_label = QLabel("等待扫描。设备在线由有效协议响应判定。")
        layout.addWidget(self.status_label)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["时间", "设备", "串口", "地址", "状态", "读取值", "说明"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((155, 75, 130, 60, 85, 250)):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table, 1)

    def _build_write_tab(self) -> None:
        page = QWidget()
        self.tabs.addTab(page, "单次读写")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("使用“设备连接”页当前协议和串口。真机写入只允许人工单次触发，不自动重试。"))
        form = QFormLayout()
        self.write_address = QSpinBox()
        self.write_address.setRange(1, 247)
        form.addRow("目标地址（天平无地址）", self.write_address)
        self.target = QDoubleSpinBox()
        self.target.setRange(-1000, 3000)
        self.target.setDecimals(4)
        self.target.setSuffix(" °C")
        form.addRow("目标值（天平固定为 0）", self.target)
        layout.addLayout(form)
        self.offline_check = QCheckBox("我已按现场 SOP 确认装置脱机、气路断开，加热输出已隔离")
        layout.addWidget(self.offline_check)
        row = QHBoxLayout()
        self.preview_button = QPushButton("读取当前设定")
        self.preview_button.clicked.connect(self.start_preview)
        self.write_button = QPushButton("执行单次写入")
        self.write_button.setObjectName("primary")
        self.write_button.setEnabled(False)
        self.write_button.clicked.connect(self.start_write)
        row.addWidget(self.preview_button)
        row.addWidget(self.write_button)
        row.addStretch()
        layout.addLayout(row)
        self.preview_label = QLabel("尚未预览当前值。")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)
        self.write_label = QLabel("写入结果将显示在这里；请核对目标值和恢复原设定。")
        self.write_label.setWordWrap(True)
        layout.addWidget(self.write_label)
        layout.addStretch()
        self.device.currentIndexChanged.connect(self._write_kind_changed)
        self._write_kind_changed()

    def _build_stress_tab(self) -> None:
        page = QWidget()
        self.tabs.addTab(page, "稳定性测试")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("真机仅循环读取；模拟器可循环读写并校验协议帧。天平去皮不参加循环写入。"))
        form = QFormLayout()
        self.stress_mode = QComboBox()
        self.stress_mode.addItem("真机采集", "real_read")
        self.stress_mode.addItem("模拟采集", "sim_read")
        self.stress_mode.addItem("模拟写入", "sim_write")
        form.addRow("测试模式", self.stress_mode)
        self.stress_hz = QDoubleSpinBox()
        self.stress_hz.setRange(0.1, 20)
        self.stress_hz.setValue(1)
        self.stress_hz.setDecimals(1)
        self.stress_hz.setSuffix(" Hz")
        form.addRow("目标频率", self.stress_hz)
        self.stress_duration = QSpinBox()
        self.stress_duration.setRange(1, 86400)
        self.stress_duration.setValue(60)
        self.stress_duration.setSuffix(" 秒")
        form.addRow("持续时间", self.stress_duration)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.stress_button = QPushButton("开始测试")
        self.stress_button.setObjectName("primary")
        self.stress_button.clicked.connect(self.start_stress)
        self.stress_stop = QPushButton("停止测试")
        self.stress_stop.setEnabled(False)
        self.stress_stop.clicked.connect(self.stop_stress)
        row.addWidget(self.stress_button)
        row.addWidget(self.stress_stop)
        row.addStretch()
        layout.addLayout(row)
        self.stress_label = QLabel("建议门槛：温控 2 Hz、每路 MFC 1 Hz、天平 1 Hz，连续 8 小时；模拟写入 5 Hz，连续 2 小时。")
        self.stress_label.setWordWrap(True)
        layout.addWidget(self.stress_label)
        self.stress_detail = QPlainTextEdit()
        self.stress_detail.setReadOnly(True)
        layout.addWidget(self.stress_detail, 1)

    def _build_log_tab(self) -> None:
        page = QWidget()
        self.tabs.addTab(page, "日志与结果")
        layout = QVBoxLayout(page)
        self.log_label = QLabel(f"日志：{self.log_path}")
        self.log_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.log_label)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        open_button = QPushButton("打开日志目录")
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path.parent))))
        export_button = QPushButton("导出诊断包")
        export_button.clicked.connect(self.export_log)
        password_button = QPushButton("修改密码")
        password_button.clicked.connect(self.change_password)
        lock_button = QPushButton("锁定界面")
        lock_button.clicked.connect(self._lock_ui)
        for button in (open_button, export_button, password_button, lock_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.MouseButtonPress, QEvent.KeyPress) and self.session.active:
            self.session.last_activity = self.session.clock()
        return False

    def _check_idle(self) -> None:
        if not self.session.active and not self._closing:
            self._lock_ui()

    def _lock_ui(self) -> None:
        self._auto_timer.stop()
        self.auto_check.setChecked(False)
        self.stop_scan()
        self.stop_stress()
        self.session.lock()
        self.setEnabled(False)
        dialog = LoginDialog(self.auth_store, self)
        if dialog.exec() == QDialog.Accepted and dialog.session:
            self.session = dialog.session
            self.setEnabled(True)
            self.log_view.appendPlainText("管理员重新登录。")
        else:
            self._closing = True
            self.close()

    def _device_changed(self) -> None:
        kind = self.device.currentData()
        is_balance = kind == "balance"
        for widget in (self.start_address, self.end_address):
            widget.setEnabled(not is_balance)
            widget.setMaximum(127 if kind == "azbil" else 247)
        for widget in (self.balance_protocol, self.balance_lines, self.balance_start):
            widget.setEnabled(is_balance)
        for widget in (self.temp_min, self.temp_max):
            widget.setEnabled(kind == "taie")
        if kind == "azbil":
            self.parity.setCurrentText("E")
            self.stopbits.setCurrentText("1")
        elif is_balance:
            self.baud.setCurrentText("1200")
            self.parity.setCurrentText("N")
            self.stopbits.setCurrentText("1")
        if hasattr(self, "write_address"):
            self._write_kind_changed()

    def _write_kind_changed(self) -> None:
        kind = self.device.currentData()
        self.write_address.setEnabled(kind != "balance")
        self.target.setEnabled(kind != "balance")
        self.target.setSuffix(" L/min" if kind == "azbil" else (" g" if kind == "balance" else " °C"))
        if kind == "balance":
            self.target.setValue(0)
        self.preview = None
        self.write_button.setEnabled(False)
        self.preview_label.setText("设备或参数变化后请重新读取当前值。")

    def refresh_ports(self) -> None:
        current = self.port.currentText()
        self.port.clear()
        try:
            ports = available_ports()
        except Exception as exc:
            logger.exception("port enumeration failed")
            QMessageBox.warning(self, "串口枚举失败", str(exc))
            ports = []
        for item in ports:
            self.port.addItem(f"{item.device} — {item.description}", item.device)
        if current:
            match = self.port.findData(current)
            if match >= 0:
                self.port.setCurrentIndex(match)
            else:
                self.port.setCurrentText(current)
        else:
            self.port.setCurrentIndex(-1)
            self.port.setEditText("")
        self.status_label.setText(f"发现 {len(ports)} 个系统串口。选择端口后按协议扫描设备。")

    def _config(self, *, simulation: bool = False) -> ScanConfig:
        port = "SIM" if simulation else self.port.currentText().split(" — ", 1)[0].strip()
        return ScanConfig(
            device=self.device.currentData(), port=port,
            baudrate=int(self.baud.currentText()), parity=self.parity.currentText(),
            stopbits=float(self.stopbits.currentText()), timeout_s=self.timeout.value(),
            start_address=self.start_address.value(), end_address=self.end_address.value(),
            balance_protocol=self.balance_protocol.currentData(),
            balance_lines=self.balance_lines.value(), balance_start_output=self.balance_start.isChecked(),
            temp_sv_min_c=self.temp_min.value(), temp_sv_max_c=self.temp_max.value(),
        )

    def _submit(self, port: str, operation, callback) -> bool:
        try:
            future = self.workers.submit(port, operation)
        except RuntimeError as exc:
            QMessageBox.warning(self, "串口忙", str(exc))
            return False
        self._callbacks[future] = callback
        future.add_done_callback(self.bridge.done.emit)
        return True

    def _task_done(self, future: Future) -> None:
        callback = self._callbacks.pop(future, None)
        try:
            result = future.result()
        except Exception as exc:
            logger.exception("background task failed")
            self.log_view.appendPlainText(f"任务失败：{type(exc).__name__}: {exc}")
            QMessageBox.warning(self, "任务失败", str(exc))
            result = None
        if callback:
            callback(result)
        if self._closing and not self.workers.busy():
            self.close()

    def start_scan(self) -> None:
        try:
            cfg = self._config()
        except (ValueError, TypeError) as exc:
            self.auto_check.setChecked(False)
            QMessageBox.warning(self, "参数无效", str(exc))
            return
        if self.workers.busy(cfg.port):
            return
        self._auto_timer.stop()
        self._scan_cancel = Event()
        cancel = self._scan_cancel

        def job():
            store = RunStore("scan", cfg)
            results = list(scan(cfg, cancel, on_result=lambda item: (store.event(item.to_dict()), self.bridge.result.emit(item))))
            store.finish({"mode": "scan", "device": cfg.device, "count": len(results),
                          "online": sum(r.status == "online" for r in results), "cancelled": cancel.is_set()})
            return store.path

        if self._submit(cfg.port, job, self._scan_finished):
            self.scan_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.status_label.setText(f"正在扫描 {cfg.device} · {cfg.port} …")

    def stop_scan(self) -> None:
        self.auto_check.setChecked(False)
        self._auto_timer.stop()
        if self._scan_cancel:
            self._scan_cancel.set()
            self.status_label.setText("正在停止；等待当前串口操作结束…")

    def _show_result(self, result: ScanResult) -> None:
        key = (result.device, result.port, result.address)
        row = self._result_rows.get(key)
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._result_rows[key] = row
        local_time = datetime.fromisoformat(result.timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        values = [local_time, result.device, result.port,
                  "—" if result.address is None else str(result.address), result.status,
                  json.dumps(result.values, ensure_ascii=False), result.detail]
        for col, value in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.scrollToBottom()
        self.status_label.setText(f"{result.device} · {result.port} · {result.address or '—'}：{result.status}  {result.detail}")
        self.log_view.appendPlainText(f"[{local_time}] {result.device} {result.address or '—'} {result.status}: {result.detail}")

    def _scan_finished(self, run_path: Path | None) -> None:
        self.scan_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._scan_cancel = None
        if run_path:
            self.last_run = run_path
        if self.auto_check.isChecked() and not self._closing:
            self.status_label.setText(f"扫描完成，{self.auto_interval.value()} 秒后复查。")
            self._auto_timer.start(self.auto_interval.value() * 1000)
        else:
            self.status_label.setText("扫描完成。日志已保存。")

    def start_preview(self) -> None:
        try:
            cfg = self._config()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "参数无效", str(exc))
            return
        address = None if cfg.device == "balance" else self.write_address.value()
        self.preview = None
        self.write_button.setEnabled(False)
        self.preview_label.setText("正在读取当前值…")
        self._submit(cfg.port, lambda: preview_write(cfg, address), self._preview_finished)

    def _preview_finished(self, preview: WritePreview | None) -> None:
        if preview is None:
            self.preview_label.setText("读取失败，请检查连接和参数。")
            return
        self.preview = preview
        self.target.setRange(preview.min_value, preview.max_value)
        if preview.device == "balance":
            self.target.setValue(0)
        self.preview_label.setText(
            f"当前值：{preview.current:g} {preview.unit}；允许范围：{preview.min_value:g}–{preview.max_value:g} {preview.unit}。"
            + (f" 当前生效 SP：{preview.active_sp:g} L/min。" if preview.active_sp is not None else "")
            + (f" {preview.detail}" if preview.detail else "")
        )
        self.write_button.setEnabled(True)

    def start_write(self) -> None:
        if self.preview is None or not self.offline_check.isChecked():
            QMessageBox.warning(self, "写入条件未满足", "请先读取当前值并确认现场脱机状态。")
            return
        try:
            cfg = self._config()
            self.session.touch()
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "无法写入", str(exc))
            return
        address = None if cfg.device == "balance" else self.write_address.value()
        if (cfg.device, cfg.port, address) != (self.preview.device, self.preview.port, self.preview.address):
            QMessageBox.warning(self, "参数已变更", "请重新读取当前值。")
            return
        target = 0.0 if cfg.device == "balance" else self.target.value()
        message = (f"设备：{cfg.device}  地址：{address or '—'}\n当前：{self.preview.current:g} {self.preview.unit}"
                   f"\n目标：{target:g} {self.preview.unit}\n\n确认只执行一次写入？")
        if cfg.device == "azbil":
            message += "\n注意：SP-0 位于 EEPROM，写入可能持久保存。"
        if QMessageBox.question(self, "确认单次写入", message) != QMessageBox.Yes:
            return
        before = self.preview.current
        self.preview = None
        self.write_button.setEnabled(False)
        self.offline_check.setChecked(False)
        self.write_label.setText("正在写入与回读…")

        def job():
            store = RunStore("single_write", cfg)
            try:
                result = execute_write(cfg, address, target, session=self.session,
                                       offline_confirmed=True, expected_before=before)
                store.event(result.to_dict())
                store.finish({"mode": "single_write", "result": result.to_dict()})
                return result, store.path
            except Exception as exc:
                store.event({"status": "failed_before_write", "error": str(exc)})
                store.finish({"mode": "single_write", "error": str(exc)})
                raise

        self._submit(cfg.port, job, self._write_finished)

    def _write_finished(self, outcome) -> None:
        if outcome is None:
            self.write_label.setText("写入未完成，请查看日志并重新读取设备；不要直接重发。")
            return
        result, run_path = outcome
        self.last_run = run_path
        self.write_label.setText(
            f"结果：{result.status} · {result.detail}\n写入前：{result.before}；目标：{result.target}；"
            f"回读：{result.after}；当前生效 SP：{result.active_sp}。请核对并手动恢复原设定。"
        )
        self.log_view.appendPlainText(f"单次写入 {result.device} {result.address}: {result.status} {result.detail}")

    def start_stress(self) -> None:
        mode = self.stress_mode.currentData()
        try:
            cfg = self._config(simulation=mode.startswith("sim_"))
            settings = StressConfig(cfg, mode, self.stress_hz.value(), self.stress_duration.value())
            self.session.touch()
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "参数无效", str(exc))
            return
        if mode == "real_read" and not self.port.currentText().strip():
            QMessageBox.warning(self, "缺少串口", "真机采集请选择串口。")
            return
        if self.workers.busy(cfg.port):
            QMessageBox.warning(self, "串口忙", "该端口正在执行其他任务。")
            return
        self._stress_cancel = Event()
        cancel = self._stress_cancel
        self.stress_detail.clear()
        self.stress_label.setText(f"正在运行 {mode}，目标 {settings.target_hz:g} Hz，{settings.duration_s:g} 秒…")
        if self._submit(cfg.port, lambda: run_stress(settings, stop=cancel, progress=self.bridge.progress.emit),
                        self._stress_finished):
            self.stress_button.setEnabled(False)
            self.stress_stop.setEnabled(True)

    def _show_progress(self, event: dict) -> None:
        if event["status"] != "ok" or event["number"] % 10 == 0:
            self.stress_detail.appendPlainText(
                f"#{event['number']} {event['status']} {event['latency_ms']:.1f} ms {event['result']}"
            )

    def stop_stress(self) -> None:
        if self._stress_cancel:
            self._stress_cancel.set()
            self.stress_label.setText("正在停止；等待当前操作完成…")

    def _stress_finished(self, outcome) -> None:
        self.stress_button.setEnabled(True)
        self.stress_stop.setEnabled(False)
        self._stress_cancel = None
        if outcome is None:
            self.stress_label.setText("测试未完成，请查看日志。")
            return
        summary, store = outcome
        self.last_run = store.path
        self.stress_label.setText(
            f"完成：{summary['succeeded']}/{summary['attempted']} 成功，实际 {summary['achieved_hz']} Hz，"
            f"错误率 {summary['error_rate']:.3%}，漏采 {summary['missed_ticks']}。"
        )
        self.stress_detail.appendPlainText(json.dumps(summary, ensure_ascii=False, indent=2))
        self.log_view.appendPlainText(f"稳定性测试结束：{store.path}")

    def save_config(self) -> None:
        try:
            cfg = self._config()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "参数无效", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存配置", "config.local.json", "JSON (*.json)")
        if path:
            cfg.save(path)
            logger.info("config saved: %s", path)

    def load_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载配置", "", "JSON (*.json)")
        if not path:
            return
        try:
            cfg = ScanConfig.load(path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.warning(self, "配置文件无效", str(exc))
            return
        self.device.setCurrentIndex(self.device.findData(cfg.device))
        self.port.setCurrentText(cfg.port)
        self.baud.setCurrentText(str(cfg.baudrate))
        self.parity.setCurrentText(cfg.parity)
        self.stopbits.setCurrentText(str(int(cfg.stopbits)))
        self.timeout.setValue(cfg.timeout_s)
        self.start_address.setValue(cfg.start_address)
        self.end_address.setValue(cfg.end_address)
        self.balance_protocol.setCurrentIndex(self.balance_protocol.findData(cfg.balance_protocol))
        self.balance_lines.setValue(cfg.balance_lines)
        self.balance_start.setChecked(cfg.balance_start_output)
        self.temp_min.setValue(cfg.temp_sv_min_c)
        self.temp_max.setValue(cfg.temp_sv_max_c)
        self.preview = None
        self.write_button.setEnabled(False)
        logger.info("config loaded: %s", path)

    def export_log(self) -> None:
        if self.last_run:
            default = f"rka-diagnostics-{self.last_run.name}.zip"
            path, _ = QFileDialog.getSaveFileName(self, "导出诊断包", default, "ZIP (*.zip)")
            if path:
                try:
                    export_bundle(self.last_run, Path(path), self.log_path)
                except (OSError, ValueError) as exc:
                    QMessageBox.warning(self, "导出失败", str(exc))
        else:
            path, _ = QFileDialog.getSaveFileName(self, "导出日志", "rka-communication.log", "日志 (*.log)")
            if path:
                try:
                    shutil.copy2(self.log_path, path)
                except OSError as exc:
                    QMessageBox.warning(self, "导出失败", str(exc))

    def change_password(self) -> None:
        old, ok = QInputDialog.getText(self, "修改密码", "当前密码", QLineEdit.Password)
        if not ok:
            return
        new, ok = QInputDialog.getText(self, "修改密码", "新密码（至少 12 位）", QLineEdit.Password)
        if not ok:
            return
        try:
            self.auth_store.change_password(self.session.username, old, new)
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, "修改失败", str(exc))
            return
        logger.warning("admin password changed: %s", self.session.username)
        QMessageBox.information(self, "已修改", "管理员密码已更新。")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._auto_timer.stop()
        self._idle_timer.stop()
        if self.workers.busy():
            self._closing = True
            self.stop_scan()
            self.stop_stress()
            event.ignore()
            self.status_label.setText("等待后台串口操作完成后关闭…")
            return
        QApplication.instance().removeEventFilter(self)
        self.workers.close()
        event.accept()
