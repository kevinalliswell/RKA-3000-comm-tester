"""Compact Qt front end. All serial work runs in ScanWorker."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import ScanConfig
from .profiles import FileProfileStore
from .scanner import ScanResult, available_ports, scan

logger = logging.getLogger(__name__)


class ScanWorker(QThread):
    result_ready = Signal(object)

    def __init__(self, cfg: ScanConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.cancelled = Event()
        self.was_cancelled = False

    def run(self) -> None:
        for result in scan(self.cfg, self.cancelled):
            self.result_ready.emit(result)

    def cancel(self) -> None:
        self.was_cancelled = True
        self.cancelled.set()


class MainWindow(QMainWindow):
    def __init__(self, log_path: Path) -> None:
        super().__init__()
        self.log_path = log_path
        self.worker: ScanWorker | None = None
        self._result_rows: dict[tuple[str, str, int | None], int] = {}
        self._closing = False
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.start_scan)
        self.setWindowTitle("RKA-3000 通信测试工具")
        self.resize(1050, 720)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("设备通信检查")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("TAIE · Azbil CPL · 电子天平  |  仅探测与读取，不下发设定值或去皮"))

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
        self.timeout.setSingleStep(0.1)
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
        self.start_address.setValue(1)
        self.end_address.setValue(6)
        range_row.addWidget(QLabel("起始"))
        range_row.addWidget(self.start_address)
        range_row.addWidget(QLabel("结束"))
        range_row.addWidget(self.end_address)
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

        self.status_label = QLabel("等待扫描。串口列表只枚举系统端口；设备在线由有效协议响应判定。")
        layout.addWidget(self.status_label)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["时间", "设备", "串口", "地址", "状态", "读取值", "说明"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 155)
        self.table.setColumnWidth(1, 75)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 60)
        self.table.setColumnWidth(4, 75)
        self.table.setColumnWidth(5, 250)
        layout.addWidget(self.table, 1)

        log_row = QHBoxLayout()
        self.log_label = QLabel(f"日志：{log_path}")
        self.log_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        log_row.addWidget(self.log_label, 1)
        open_button = QPushButton("打开日志目录")
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path.parent))))
        export_button = QPushButton("导出日志")
        export_button.clicked.connect(self.export_log)
        log_row.addWidget(open_button)
        log_row.addWidget(export_button)
        layout.addLayout(log_row)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fa; color: #1f2937; }
            QLabel { color: #334155; }
            QGroupBox { border: 1px solid #cbd5e1; border-radius: 6px; margin-top: 10px; padding: 10px; background: #ffffff; color: #1f2937; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; font-weight: 600; color: #1f2937; }
            QLabel#title { font-size: 22px; font-weight: 700; color: #14283f; }
            QPushButton { padding: 6px 12px; border: 1px solid #b8c4d1; border-radius: 4px; background: #ffffff; color: #1f2937; }
            QPushButton:hover { background: #e9f1fb; border-color: #8aa9c7; }
            QPushButton#primary { background: #1769aa; color: white; border-color: #1769aa; font-weight: 600; }
            QPushButton#primary:hover { background: #145b93; }
            QCheckBox { color: #334155; }
            QComboBox, QSpinBox, QDoubleSpinBox { background: #ffffff; color: #1f2937; border: 1px solid #b8c4d1; border-radius: 4px; padding: 4px 6px; }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #1769aa; }
            QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QCheckBox:disabled, QPushButton:disabled { color: #94a3b8; background: #eef2f6; border-color: #d5dde6; }
            QTableWidget { background: #ffffff; color: #1f2937; border: 1px solid #cbd5e1; gridline-color: #e2e8f0; }
            QHeaderView::section { background: #3f4b59; color: #ffffff; border: 0; padding: 5px; font-weight: 600; }
        """)
        self.device.currentIndexChanged.connect(self._device_changed)
        self._device_changed()
        self.refresh_ports()

    def _device_changed(self) -> None:
        kind = self.device.currentData()
        is_balance = kind == "balance"
        for widget in (self.start_address, self.end_address):
            widget.setEnabled(not is_balance)
            widget.setMaximum(127 if kind == "azbil" else 247)
        for widget in (self.balance_protocol, self.balance_lines, self.balance_start):
            widget.setEnabled(is_balance)
        if kind == "azbil":
            self.parity.setCurrentText("E")
            self.stopbits.setCurrentText("1")
            self.end_address.setValue(min(self.end_address.value(), 127))
        elif is_balance:
            self.baud.setCurrentText("1200")
            self.parity.setCurrentText("N")
            self.stopbits.setCurrentText("1")

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

    def _config(self) -> ScanConfig:
        displayed = self.port.currentText().split(" — ", 1)[0].strip()
        return ScanConfig(
            device=self.device.currentData(), port=displayed,
            baudrate=int(self.baud.currentText()), parity=self.parity.currentText(),
            stopbits=float(self.stopbits.currentText()), timeout_s=self.timeout.value(),
            start_address=self.start_address.value(), end_address=self.end_address.value(),
            balance_protocol=self.balance_protocol.currentData(),
            balance_lines=self.balance_lines.value(), balance_start_output=self.balance_start.isChecked(),
        )

    def start_scan(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        try:
            cfg = self._config()
        except (ValueError, TypeError) as exc:
            self.auto_check.setChecked(False)
            QMessageBox.warning(self, "参数无效", str(exc))
            return
        self._auto_timer.stop()
        self.worker = ScanWorker(cfg)
        self.worker.result_ready.connect(self._show_result)
        self.worker.finished.connect(self._scan_finished)
        self.scan_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText(f"正在扫描 {cfg.device} · {cfg.port} …")
        self.worker.start()

    def stop_scan(self) -> None:
        self.auto_check.setChecked(False)
        self._auto_timer.stop()
        if self.worker:
            self.worker.cancel()
            self.status_label.setText("正在停止；等待当前串口读操作超时…")

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

    def _scan_finished(self) -> None:
        self.scan_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if self._closing:
            self.close()
            return
        if self.worker and self.worker.was_cancelled:
            self.status_label.setText("扫描已停止，结果不完整。")
            return
        if self.auto_check.isChecked():
            self.status_label.setText(f"扫描完成，{self.auto_interval.value()} 秒后复查。")
            self._auto_timer.start(self.auto_interval.value() * 1000)
        else:
            self.status_label.setText("扫描完成。日志已写入下方路径。")

    def save_config(self) -> None:
        try:
            cfg = self._config()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "参数无效", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存配置", "config.local.json", "JSON (*.json)")
        if path:
            FileProfileStore(path).save(cfg)
            logger.info("config saved: %s", path)

    def load_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载配置", "", "JSON (*.json)")
        if not path:
            return
        try:
            cfg = FileProfileStore(path).load()
            if cfg is None:
                raise ValueError("配置文件不存在")
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
        logger.info("config loaded: %s", path)

    def export_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "rka-communication.log", "日志 (*.log)")
        if path:
            try:
                shutil.copy2(self.log_path, path)
            except OSError as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._auto_timer.stop()
        if self.worker and self.worker.isRunning():
            self._closing = True
            self.worker.cancel()
            event.ignore()
            self.status_label.setText("等待当前扫描结束后关闭…")
            return
        event.accept()
