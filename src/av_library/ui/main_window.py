from datetime import UTC
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from av_library.config import AppPaths
from av_library.db.models import Actress
from av_library.services.actresses import ActressService, ValidationError
from av_library.services.automatic_sync import AutomaticSyncService
from av_library.services.backups import BackupService
from av_library.services.matching import MatchService
from av_library.services.metadata_sync import MetadataService
from av_library.services.scanning import ScanService
from av_library.services.search import SearchService
from av_library.services.settings import SettingsService
from av_library.ui.actress_dialog import ActressDialog
from av_library.ui.backup_dialog import BackupDialog
from av_library.ui.metadata_dialog import MetadataDialog
from av_library.ui.scan_dialog import ScanDialog
from av_library.ui.search_dialog import SearchDialog
from av_library.ui.settings_dialog import SettingsDialog


def label(text: str, style: str = "", wrap: bool = False) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setObjectName(style)
    result.setWordWrap(wrap)
    return result


class AutomaticSyncWorker(QThread):
    def __init__(self, service: AutomaticSyncService, parent=None):
        super().__init__(parent)
        self.service = service
        self.cancel = Event()
        self.result = (0, 0)

    def run(self):
        try:
            self.result = self.service.run_due(self.cancel)
        except Exception:  # noqa: BLE001 -- worker boundary; startup remains usable
            self.result = (0, 1)


class MainWindow(QMainWindow):
    def __init__(self, service: ActressService, paths: AppPaths):
        super().__init__()
        self.service, self.paths = service, paths
        self.current: Actress | None = None
        self.rows: list[Actress] = []
        self.summaries = {}
        self.auto_worker: AutomaticSyncWorker | None = None
        self.setWindowTitle("本地作品库 · JPAV Library")
        self.resize(1200, 780)
        self.setMinimumSize(980, 680)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(24)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 26, 20, 22)
        side.addWidget(label("本地作品库", "brand"))
        side.addWidget(label("JPAV LIBRARY"))
        side.addSpacing(36)
        side.addWidget(label("●  女优管理", "subheading"))
        side.addSpacing(14)
        side.addWidget(label("本地保存 · 私人管理"))
        side.addStretch()
        settings = QPushButton("备份与恢复")
        settings.clicked.connect(self.show_storage)
        side.addWidget(settings)
        preferences = QPushButton("设置")
        preferences.clicked.connect(self.show_settings)
        side.addWidget(preferences)
        side.addWidget(label("PHASE 06 / 06\n本地作品管理", wrap=True))
        layout.addWidget(sidebar)
        body = QVBoxLayout()
        body.setSpacing(18)
        layout.addLayout(body, 1)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("作品收藏总览", "heading"))
        titles.addWidget(label("按女优查看已收录、已收藏与缺少作品。", "muted"))
        header.addLayout(titles)
        header.addStretch()
        self.add_button = QPushButton("＋ 添加女优")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(lambda: self.edit_actress(new=True))
        header.addWidget(self.add_button)
        body.addLayout(header)
        body.addWidget(
            label(
                "本地扫描只读取视频文件名。完成度以已收录作品为基数；来源覆盖范围可能不完整。",
                "notice",
                True,
            )
        )
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索常用名、日文名或别名…")
        self.search.textChanged.connect(lambda: self.reload())
        search_row.addWidget(self.search)
        self.global_button = QPushButton("全局搜索")
        self.global_button.clicked.connect(self.open_global_search)
        search_row.addWidget(self.global_button)
        self.sort = QComboBox()
        self.sort.addItems(["最近更新", "完成度", "作品数量", "缺少数量"])
        self.sort.currentIndexChanged.connect(lambda: self.reload())
        search_row.addWidget(self.sort)
        self.count_label = label("", "muted")
        search_row.addWidget(self.count_label)
        body.addLayout(search_row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["女优", "收藏", "缺少", "完成度", "最新作品", "最近更新", "本地目录"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.select_row)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_actress())
        body.addWidget(self.table, 1)
        self.empty = label("还没有女优资料。点击右上角「添加女优」开始。", "muted", True)
        body.addWidget(self.empty)
        self.detail = QFrame()
        self.detail.setObjectName("card")
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(22, 20, 22, 20)
        info = QHBoxLayout()
        self.avatar = label("", "avatar")
        self.avatar.setFixedSize(74, 74)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.addWidget(self.avatar)
        info_text = QVBoxLayout()
        self.detail_name = label("", "subheading")
        self.detail_aliases = label("", "muted", True)
        info_text.addWidget(self.detail_name)
        info_text.addWidget(self.detail_aliases)
        info.addLayout(info_text, 1)
        detail_layout.addLayout(info)
        self.folder_label = label("", wrap=True)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.folder_label)
        self.stats_label = label("作品库待同步", "muted")
        detail_layout.addWidget(self.stats_label)
        self.updated_label = label("", "muted", True)
        detail_layout.addWidget(self.updated_label)
        buttons = QHBoxLayout()
        self.edit_button = QPushButton("编辑资料 / 修改目录")
        self.edit_button.clicked.connect(lambda: self.edit_actress())
        buttons.addWidget(self.edit_button)
        open_button = QPushButton("打开本地文件夹")
        open_button.clicked.connect(self.open_folder)
        buttons.addWidget(open_button)
        self.scan_button = QPushButton("扫描 / 本地文件")
        self.scan_button.setObjectName("primary")
        self.scan_button.clicked.connect(self.open_scan)
        buttons.addWidget(self.scan_button)
        sync_button = QPushButton("作品库 / 同步")
        sync_button.clicked.connect(self.open_metadata)
        buttons.addWidget(sync_button)
        buttons.addStretch()
        remove = QPushButton("移除")
        remove.setObjectName("danger")
        remove.clicked.connect(self.remove_actress)
        buttons.addWidget(remove)
        detail_layout.addLayout(buttons)
        quick = QHBoxLayout()
        missing_button = QPushButton("查看缺少作品")
        missing_button.clicked.connect(lambda: self.open_metadata(2))
        quick.addWidget(missing_button)
        latest_button = QPushButton("查看最新作品")
        latest_button.clicked.connect(lambda: self.open_metadata(4))
        quick.addWidget(latest_button)
        quick.addStretch()
        detail_layout.addLayout(quick)
        body.addWidget(self.detail)
        self.statusBar().showMessage(f"本地数据库：{paths.database}")
        self.reload()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.check_auto_sync)
        self.refresh_timer.start(10 * 60 * 1000)
        QTimer.singleShot(2000, self.check_auto_sync)

    def reload(self, select_id: int | None = None):
        previous = select_id or (self.current.id if self.current else None)
        try:
            self.rows = self.service.list(self.search.text())
            matcher = MatchService(self.service.database)
            self.summaries = {row.id: matcher.summary(row.id) for row in self.rows}
        except SQLAlchemyError:
            QMessageBox.critical(self, "读取失败", "无法读取数据库，请检查文件权限或稍后重试。")
            return
        mode = self.sort.currentIndex()
        if mode == 0:
            self.rows.sort(key=lambda row: row.last_synced_at or row.updated_at, reverse=True)
        elif mode == 1:
            self.rows.sort(
                key=lambda row: (
                    self.summaries[row.id].completion_percent
                    if self.summaries[row.id].completion_percent is not None
                    else -1
                ),
                reverse=True,
            )
        elif mode == 2:
            self.rows.sort(key=lambda row: self.summaries[row.id].total, reverse=True)
        else:
            self.rows.sort(key=lambda row: self.summaries[row.id].missing, reverse=True)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        selected = 0
        for index, row in enumerate(self.rows):
            if row.id == previous:
                selected = index
            stats = self.summaries[row.id]
            percent = (
                f"{stats.completion_percent:.1f}%"
                if stats.scanned and stats.completion_percent is not None
                else "待扫描"
                if not stats.scanned
                else "—"
            )
            latest = stats.latest.movie.code if stats.latest else "—"
            updated = row.last_synced_at or row.updated_at
            values = (
                row.name,
                f"{stats.collected} / {stats.needed}" if row.last_synced_at else "待同步",
                str(stats.missing) if stats.scanned and row.last_synced_at else "—",
                percent if row.last_synced_at else "—",
                latest,
                updated.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d"),
                row.folder_path,
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, col, item)
        self.table.blockSignals(False)
        self.count_label.setText(f"{len(self.rows)} 位女优")
        self.empty.setVisible(not self.rows)
        self.empty.setText(
            "没有匹配的女优。"
            if self.search.text()
            else "还没有女优资料。点击右上角「添加女优」开始。"
        )
        if self.rows:
            self.table.selectRow(selected)
        self.select_row()

    def select_row(self):
        index = self.table.currentRow()
        self.current = self.rows[index] if 0 <= index < len(self.rows) else None
        self.detail.setVisible(self.current is not None)
        if not self.current:
            return
        row = self.current
        self.detail_name.setText(row.name)
        self.detail_aliases.setText(
            "日文名："
            + (row.japanese_name or "未填写")
            + "    别名："
            + ("、".join(row.aliases) or "未填写")
        )
        self.avatar.clear()
        pixmap = QPixmap(row.avatar_path) if row.avatar_path else QPixmap()
        if not pixmap.isNull():
            self.avatar.setPixmap(
                pixmap.scaled(
                    74,
                    74,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.avatar.setText(row.name[:1])
        self.folder_label.setText(f"本地目录：{row.folder_path}")
        if row.last_synced_at:
            stats = self.summaries[row.id]
            percent = (
                f"{stats.completion_percent:.1f}%"
                if stats.scanned and stats.completion_percent is not None
                else "待扫描"
                if not stats.scanned
                else "—"
            )
            newest = (
                f"{stats.latest.movie.code}（{stats.latest.movie.release_date or '日期未知'}，"
                f"{'已忽略' if stats.latest.is_ignored else '待扫描' if not stats.scanned else '已收藏' if stats.latest.local_paths else '未收藏'}）"
                if stats.latest
                else "暂无"
            )
            self.stats_label.setText(
                f"单人作品 {stats.total} · 合集 {stats.compilations} · 多人企划 {stats.multi_actress}（不统计）· 忽略 {stats.ignored} · 已收藏 {stats.collected} · "
                f"缺少 {stats.missing if stats.scanned else '待扫描'} · 完成度 {percent}\n"
                f"最新作品：{newest} · 最近同步新增 {stats.new_count} 部"
            )
        else:
            self.stats_label.setText("作品库待同步")
        stamp = row.updated_at.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M")
        scanned = (
            row.last_scanned_at.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M")
            if row.last_scanned_at
            else "尚未扫描当前目录"
        )
        self.updated_label.setText(f"资料更新：{stamp}    ·    最近成功扫描：{scanned}")

    def open_scan(self):
        if not self.current:
            return
        try:
            dialog = ScanDialog(ScanService(self.service.database), self.current, self)
            dialog.exec()
            self.reload(self.current.id)
        except SQLAlchemyError:
            QMessageBox.warning(self, "无法读取文件库", "数据库访问失败，请稍后重试。")

    def open_metadata(self, filter_index: int = 0):
        if not self.current:
            return
        try:
            dialog = MetadataDialog(
                MetadataService(self.service.database),
                self.current,
                self,
                cover_dir=self.paths.covers,
            )
            dialog.filter.setCurrentIndex(filter_index)
            dialog.exec()
            self.reload(self.current.id)
        except SQLAlchemyError:
            QMessageBox.warning(self, "无法读取作品库", "数据库访问失败，请稍后重试。")

    def open_global_search(self):
        dialog = SearchDialog(SearchService(self.service.database), self, self.paths.covers)
        dialog.exec()
        self.reload()

    def edit_actress(self, new: bool = False):
        if not new and self.current is None:
            return
        dialog = ActressDialog(self.service, None if new else self.current, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.search.clear()
            self.reload(dialog.saved_id)
            self.statusBar().showMessage("资料已保存。", 5000)

    def remove_actress(self):
        if not self.current:
            return
        answer = QMessageBox.question(
            self,
            "移除女优资料",
            "确定移除所选女优资料？\n仅移除数据库记录，本地文件和文件夹会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete(self.current.id)
        except (SQLAlchemyError, ValidationError):
            QMessageBox.warning(self, "移除失败", "无法移除记录，请刷新或稍后重试。")
            return
        self.current = None
        self.reload()

    def open_folder(self):
        if not self.current:
            return
        path = Path(self.current.folder_path)
        try:
            if not path.is_dir():
                raise OSError("目录不存在或磁盘未连接，请检查路径。")
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError("无法打开资源管理器。")
        except OSError as error:
            QMessageBox.warning(self, "无法打开文件夹", str(error))

    def show_storage(self):
        if self.auto_worker:
            self.auto_worker.cancel.set()
            self.auto_worker.wait()
            self.auto_worker = None
        service = BackupService(self.service.database, self.paths.database, self.paths.backups)
        dialog = BackupDialog(service, self)
        dialog.exec()
        if dialog.restored:
            self.current = None
            self.reload()

    def show_settings(self):
        dialog = SettingsDialog(SettingsService(self.service.database), self.paths, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.check_auto_sync()

    def check_auto_sync(self):
        if self.auto_worker or SettingsService(self.service.database).update_interval_hours() == 0:
            return
        self.auto_worker = AutomaticSyncWorker(AutomaticSyncService(self.service.database), self)
        self.auto_worker.finished.connect(
            self.auto_sync_finished, Qt.ConnectionType.QueuedConnection
        )
        self.auto_worker.start()

    @Slot()
    def auto_sync_finished(self):
        worker = self.auto_worker
        if worker is None or self.sender() is not worker:
            return
        worker.wait()
        self.auto_worker = None
        done, failed = worker.result
        if done or failed:
            self.statusBar().showMessage(f"自动更新完成：成功 {done} 位，失败 {failed} 位。", 10000)
            self.reload()

    def closeEvent(self, event):
        self.refresh_timer.stop()
        if self.auto_worker:
            self.auto_worker.cancel.set()
            self.auto_worker.wait()
            self.auto_worker = None
        event.accept()
