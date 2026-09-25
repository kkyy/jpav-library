from collections import Counter
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from av_library.db.models import Actress
from av_library.scanner.files import ScanOptions, path_key
from av_library.services.actresses import ValidationError
from av_library.services.matching import MatchService
from av_library.services.scanning import ScanService


class ScanWorker(QThread):
    progress = Signal(int)

    def __init__(self, service: ScanService, actress_id: int, options: ScanOptions, parent=None):
        super().__init__(parent)
        self.service, self.actress_id, self.options = service, actress_id, options
        self.cancel = Event()
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.service.run(
                self.actress_id, self.options, self.cancel, self.progress.emit
            )
        except Exception as error:  # noqa: BLE001 -- never let a worker exception abort the process
            self.error = str(error)


class ScanDialog(QDialog):
    def __init__(self, service: ScanService, actress: Actress, parent=None):
        super().__init__(parent)
        self.service, self.actress = service, actress
        self.worker: ScanWorker | None = None
        self.close_pending = False
        self.rows = []
        self.setWindowTitle(f"本地文件 · {actress.name}")
        self.resize(1120, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        heading = QLabel("本地文件名扫描")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        root = QLabel(actress.folder_path)
        root.setTextFormat(Qt.TextFormat.PlainText)
        root.setWordWrap(True)
        layout.addWidget(root)
        note = QLabel("仅识别文件名，不读取视频内容。作品库未收录的番号会留在文件列表供人工检查。")
        note.setObjectName("notice")
        note.setWordWrap(True)
        layout.addWidget(note)
        options = service.options()
        controls = QHBoxLayout()
        self.recursive = QCheckBox("扫描所有子文件夹")
        self.recursive.setChecked(options.recursive)
        controls.addWidget(self.recursive)
        controls.addWidget(QLabel("扩展名"))
        self.extensions = QLineEdit(", ".join(options.extensions))
        controls.addWidget(self.extensions, 1)
        self.start_button = QPushButton("开始扫描")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_scan)
        controls.addWidget(self.start_button)
        self.cancel_button = QPushButton("取消扫描")
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.cancel_button.setEnabled(False)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("筛选文件名、番号或路径")
        self.search.textChanged.connect(self.render_rows)
        filters.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(
            ["当前目录记录", "已识别", "待确认", "同番号多个文件", "历史未见 / 旧目录", "全部记录"]
        )
        self.filter.currentIndexChanged.connect(self.render_rows)
        filters.addWidget(self.filter)
        layout.addLayout(filters)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["番号 / 候选", "状态", "文件名", "完整路径"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 165)
        self.table.setColumnWidth(2, 270)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.manual_button = QPushButton("手动指定番号")
        self.manual_button.clicked.connect(self.set_manual)
        actions.addWidget(self.manual_button)
        self.reset_button = QPushButton("恢复自动识别")
        self.reset_button.clicked.connect(self.reset_manual)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.reload()

    def reload(self):
        self.rows = self.service.files(self.actress.id)
        self.matched_file_ids = set(
            MatchService(self.service.database).file_matches(self.actress.id)
        )
        latest = self.service.latest(self.actress.id)
        if latest:
            title = {
                "success": "完成",
                "partial": "不完整，保留上次结果",
                "failed": "失败，保留上次结果",
                "cancelled": "已取消，保留上次结果",
                "running": "上次运行中断，请重新扫描",
            }[latest.status]
            self.status.setText(
                f"最近扫描：{title} · 发现 {latest.files_seen} 个视频文件"
                f" · 跳过 {latest.skipped_links} 个链接\n"
                + (
                    "目录已修改，请扫描新目录。 "
                    if path_key(latest.root_path) != path_key(self.actress.folder_path)
                    else ""
                )
                + (
                    (latest.error_summary[:180] + "（悬停查看详情）")
                    if latest.error_summary
                    else "记录反映各文件最后一次完整扫描结果；缩小扫描范围不影响范围外记录。"
                )
            )
            self.status.setToolTip(latest.error_summary or "")
        else:
            self.status.setText("尚未扫描。点击「开始扫描」建立文件记录。")
        self.render_rows()

    def render_rows(self, *_args):
        present = [
            row
            for row in self.rows
            if row.is_present and row.scan_root_key == path_key(self.actress.folder_path)
        ]
        present_ids = {row.id for row in present}
        counts = Counter(row.effective_code for row in present if row.effective_code)
        pending = sum(not row.effective_code for row in present)
        self.summary.setText(
            f"当前记录 {len(present)} 个 · 已识别 {len(present) - pending} 个"
            f" · 待确认 {pending} 个 · 同番号多文件 {sum(n > 1 for n in counts.values())} 组"
        )
        mode, query = self.filter.currentIndex(), self.search.text().casefold()
        visible = []
        for row in self.rows:
            code = row.effective_code
            current = row.id in present_ids
            duplicate = bool(current and code and counts[code] > 1)
            if mode < 4 and not current:
                continue
            if (
                (mode == 1 and not code)
                or (mode == 2 and code)
                or (mode == 3 and not duplicate)
                or (mode == 4 and current)
            ):
                continue
            if query and query not in f"{row.filename} {code or ''} {row.path}".casefold():
                continue
            status = (
                ("人工指定" if row.manual_code else "已识别")
                if code
                else ("多个候选，待确认" if row.parse_status == "ambiguous" else "无法识别")
            )
            if not current:
                status = "历史未见 / 旧目录"
            elif row.id in self.matched_file_ids:
                status += " · 已收藏"
            elif code:
                status += " · 当前作品来源未收录"
            elif duplicate:
                status += f" · {counts[code]} 个文件"
            visible.append((row, code or " / ".join(row.candidates) or "—", status))
        self.table.setRowCount(len(visible))
        for index, (row, code, status) in enumerate(visible):
            for column, value in enumerate((code, status, row.filename, row.path)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, row.id)
                self.table.setItem(index, column, item)

    def start_scan(self):
        if self.worker:
            return
        try:
            options = self.service.save_options(self.recursive.isChecked(), self.extensions.text())
        except (ValidationError, SQLAlchemyError) as error:
            self.show_error(str(error))
            return
        self.worker = ScanWorker(self.service, self.actress.id, options, self)
        self.worker.progress.connect(self.update_progress, Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self.scan_finished, Qt.ConnectionType.QueuedConnection)
        self.set_busy(True)
        self.status.setText("正在读取目录…")
        self.worker.start()

    def set_busy(self, busy: bool):
        for widget in (
            self.start_button,
            self.recursive,
            self.extensions,
            self.manual_button,
            self.reset_button,
        ):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.progress_bar.setVisible(busy)

    @Slot(int)
    def update_progress(self, count: int):
        if self.worker and not self.worker.cancel.is_set():
            self.status.setText(f"扫描中：已发现 {count} 个视频文件…")

    def cancel_scan(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消；等待当前目录读取结束…")

    @Slot()
    def scan_finished(self):
        self.worker.wait()
        error = self.worker.error
        self.worker = None
        self.set_busy(False)
        try:
            self.reload()
        except SQLAlchemyError as problem:
            error = str(problem)
        if error:
            self.show_error(error)
        if self.close_pending:
            super().reject()

    def selected_id(self) -> int | None:
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def set_manual(self):
        file_id = self.selected_id()
        if file_id is None:
            return
        row = next(row for row in self.rows if row.id == file_id)
        code, accepted = QInputDialog.getText(
            self, "指定番号", "输入完整番号（只修改数据库记录）：", text=row.effective_code or ""
        )
        if accepted and code.strip():
            self.apply_manual(file_id, code)

    def reset_manual(self):
        file_id = self.selected_id()
        if file_id is not None:
            self.apply_manual(file_id, None)

    def apply_manual(self, file_id: int, code: str | None):
        try:
            self.service.set_manual_code(file_id, code)
            self.reload()
        except (ValidationError, SQLAlchemyError) as error:
            self.show_error(str(error))

    def show_error(self, text: str):
        box = QMessageBox(self)
        box.setWindowTitle("扫描提示")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.exec()

    def reject(self):
        if self.worker:
            self.close_pending = True
            self.cancel_scan()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.close_pending = True
            self.cancel_scan()
            event.ignore()
        else:
            event.accept()
