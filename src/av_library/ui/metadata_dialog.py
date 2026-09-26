from collections.abc import Callable
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from av_library.db.models import Actress
from av_library.providers.dmm import DmmProvider, ProviderError
from av_library.providers.file_import import FileCatalogProvider
from av_library.providers.s1_public import S1PublicProvider
from av_library.services.covers import CoverService
from av_library.services.discovery import SoloCatalogDiscoveryService
from av_library.services.matching import MatchService
from av_library.services.metadata_sync import MetadataService, SyncProgress


class MetadataWorker(QThread):
    progress = Signal(object)

    def __init__(self, operation: Callable, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel = Event()
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation(self.cancel, self.progress.emit)
        except Exception as error:  # noqa: BLE001 -- QThread boundary
            self.error = str(error)


class MetadataDialog(QDialog):
    def __init__(
        self, service: MetadataService, actress: Actress, parent=None, cover_dir: Path | None = None
    ):
        super().__init__(parent)
        self.service, self.actress = service, actress
        self.matches = MatchService(service.database)
        self.covers = CoverService(cover_dir) if cover_dir else None
        self.worker: MetadataWorker | None = None
        self.operation = ""
        self.close_pending = False
        self.setWindowTitle(f"作品库 · {actress.name}")
        self.resize(1100, 710)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        title = QLabel(f"{actress.name} · 作品元数据")
        title.setObjectName("heading")
        layout.addWidget(title)
        tip = QLabel(
            "自动检索请先在女优管理填写准确日文名。每个来源只覆盖自己的目录；单人统计不保证跨片商全部发行作品。"
        )
        tip.setObjectName("notice")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        buttons = QHBoxLayout()
        self.discovery_button = QPushButton("自动检索单人作品")
        self.discovery_button.setObjectName("primary")
        self.discovery_button.clicked.connect(self.discover_solo_catalog)
        buttons.addWidget(self.discovery_button)
        self.import_button = QPushButton("导入 JSON / CSV")
        self.import_button.clicked.connect(self.choose_import)
        buttons.addWidget(self.import_button)
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        advanced = QHBoxLayout()
        self.bind_button = QPushButton("查找并绑定 DMM 身份")
        self.bind_button.clicked.connect(self.search_dmm)
        advanced.addWidget(self.bind_button)
        self.sync_button = QPushButton("更新 DMM 作品库")
        self.sync_button.clicked.connect(self.sync_dmm)
        advanced.addWidget(self.sync_button)
        self.s1_bind_button = QPushButton("绑定 S1 官网身份")
        self.s1_bind_button.clicked.connect(self.search_s1)
        advanced.addWidget(self.s1_bind_button)
        self.s1_sync_button = QPushButton("更新 S1 官网目录")
        self.s1_sync_button.clicked.connect(self.sync_s1)
        advanced.addWidget(self.s1_sync_button)
        self.s1_adjacent_button = QPushButton("S1 邻号补查")
        self.s1_adjacent_button.clicked.connect(self.sync_s1_adjacent)
        advanced.addWidget(self.s1_adjacent_button)
        advanced.addStretch()
        layout.addLayout(advanced)
        self.sources = QLabel()
        self.sources.setTextFormat(Qt.TextFormat.PlainText)
        self.sources.setWordWrap(True)
        layout.addWidget(self.sources)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.search = QLineEdit()
        self.search.setPlaceholderText("按番号或标题筛选已收录作品")
        self.search.textChanged.connect(self.render_rows)
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(
            [
                "全部",
                "已收藏",
                "缺少",
                "已忽略",
                "合集（不统计）",
                "多人企划（不统计）",
                "最新作品",
                "单人作品",
            ]
        )
        self.filter.currentIndexChanged.connect(self.render_rows)
        filters.addWidget(self.filter)
        self.sort = QComboBox()
        self.sort.addItems(["发行日期（新→旧）", "发行日期（旧→新）", "番号", "首次收录"])
        self.sort.currentIndexChanged.connect(self.render_rows)
        filters.addWidget(self.sort)
        layout.addLayout(filters)
        self.collection = QLabel()
        self.collection.setTextFormat(Qt.TextFormat.PlainText)
        self.collection.setWordWrap(True)
        layout.addWidget(self.collection)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["番号", "发行日期", "作品标题", "制作商", "状态"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 135)
        self.table.setColumnWidth(1, 120)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(3, 160)
        self.table.setColumnWidth(4, 130)
        self.table.itemSelectionChanged.connect(self.show_cover)
        layout.addWidget(self.table, 1)
        cover_row = QHBoxLayout()
        self.cover = QLabel("未选择作品")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedSize(96, 132)
        cover_row.addWidget(self.cover)
        self.cover_button = QPushButton("缓存所选封面")
        self.cover_button.clicked.connect(self.fetch_cover)
        self.cover_button.setEnabled(self.covers is not None)
        cover_row.addWidget(self.cover_button)
        self.detail_button = QPushButton("打开作品详情页")
        self.detail_button.clicked.connect(self.open_detail)
        cover_row.addWidget(self.detail_button)
        cover_row.addStretch()
        layout.addLayout(cover_row)
        close_row = QHBoxLayout()
        self.ignore_button = QPushButton("忽略 / 恢复所选作品")
        self.ignore_button.clicked.connect(self.toggle_ignored)
        close_row.addWidget(self.ignore_button)
        close_row.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        close_row.addWidget(close)
        layout.addLayout(close_row)
        self.reload()
        self.filter.setCurrentText("单人作品")

    def reload(self):
        self.rows = self.matches.views(self.actress.id)
        summary = self.matches.summary(self.actress.id, self.rows)
        latest = summary.latest
        latest_status = (
            "已忽略"
            if latest and latest.is_ignored
            else "待扫描"
            if not summary.scanned
            else "已收藏"
            if latest and latest.local_paths
            else "未收藏"
        )
        newest = (
            f"最新作品：{latest.movie.code} · {latest.movie.release_date or '日期未知'} · "
            f"{latest_status}"
            if latest
            else "最新作品：暂无"
        )
        percent = (
            f"{summary.completion_percent:.1f}%"
            if summary.scanned and summary.completion_percent is not None
            else "待扫描"
            if not summary.scanned
            else "—"
        )
        self.collection.setText(
            f"单人作品 {summary.total} · 合集 {summary.compilations} · 多人企划 {summary.multi_actress}（不统计）· 忽略 {summary.ignored} · 需收藏 {summary.needed} · "
            f"已收藏 {summary.collected} · 缺少 {summary.missing if summary.scanned else '待扫描'} · 完成度 {percent} · "
            f"最近同步新增 {summary.new_count}\n{newest}"
        )
        bindings = self.service.bindings(self.actress.id)
        self.sources.setText(
            "来源："
            + (
                "；".join(
                    f"{provider.name}（范围：{provider.coverage_description}）"
                    for _, provider in bindings
                )
                or "尚未绑定或导入"
            )
        )
        latest = self.service.latest(self.actress.id)
        if latest:
            meaning = {
                "success": "完成",
                "partial": "不完整",
                "failed": "失败",
                "cancelled": "已取消",
                "running": "上次运行中断",
            }[latest.status]
            self.status.setText(
                f"已收录 {len(self.rows)} 部 · 最近同步：{meaning} · 来源返回 "
                f"{latest.items_received} 条 · 新关联 {latest.added_count} 部"
                + (f" · {latest.error_summary}" if latest.error_summary else "")
            )
        else:
            self.status.setText(f"已收录 {len(self.rows)} 部 · 尚未同步")
        self.render_rows()

    def render_rows(self, *_args):
        query = self.search.text().casefold()
        mode = self.filter.currentIndex()
        rows = [
            row
            for index, row in enumerate(self.rows)
            if query in f"{row.movie.code} {row.movie.title} {row.movie.japanese_title}".casefold()
            and (
                mode == 0
                or (mode == 1 and row.status == "collected")
                or (mode == 2 and row.status == "missing")
                or (mode == 3 and row.status == "ignored")
                or (mode == 4 and row.status == "compilation")
                or (mode == 5 and row.status == "multi_actress")
                or (mode == 6 and index < 20)
                or (mode == 7 and row.is_solo and not row.is_compilation)
            )
        ]
        self.visible_rows = rows
        sort = self.sort.currentIndex()
        if sort == 1:
            rows = sorted(
                rows, key=lambda view: (view.movie.release_date is None, view.movie.release_date)
            )
        elif sort == 2:
            rows = sorted(rows, key=lambda view: view.movie.code)
        elif sort == 3:
            rows = sorted(rows, key=lambda view: view.movie.first_seen_time, reverse=True)
        self.visible_rows = rows
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            movie = row.movie
            for column, value in enumerate(
                (
                    movie.code,
                    str(movie.release_date or "—"),
                    movie.title or movie.japanese_title or "—",
                    movie.manufacturer or "—",
                    "合集（不统计）"
                    if row.is_compilation
                    else "多人企划（不统计）"
                    if not row.is_solo
                    else "已忽略"
                    if row.is_ignored
                    else f"已收藏（{len(row.local_paths)} 文件）"
                    if row.local_paths
                    else "缺少",
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 4 and row.local_paths:
                    item.setToolTip("\n".join(row.local_paths))
                self.table.setItem(index, column, item)
        self.show_cover()

    def selected_view(self):
        index = self.table.currentRow()
        return self.visible_rows[index] if 0 <= index < len(self.visible_rows) else None

    def show_cover(self):
        view = self.selected_view()
        cached = self.covers.cached(view.movie.cover_url) if self.covers and view else None
        if cached:
            pixmap = QPixmap(str(cached))
            self.cover.setPixmap(
                pixmap.scaled(
                    self.cover.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.cover.setPixmap(QPixmap())
            self.cover.setText("封面未缓存" if view and view.movie.cover_url else "无封面地址")
        self.cover_button.setEnabled(
            self.covers is not None
            and view is not None
            and bool(view.movie.cover_url)
            and self.worker is None
        )
        self.detail_button.setEnabled(view is not None and bool(view.movie.detail_url))

    def fetch_cover(self):
        view = self.selected_view()
        if not self.covers or not view or not view.movie.cover_url:
            return
        url = view.movie.cover_url
        self.start_task("cover", lambda _cancel, _progress: self.covers.fetch(url))

    def open_detail(self):
        view = self.selected_view()
        if view and view.movie.detail_url:
            url = QUrl(view.movie.detail_url)
            if url.scheme() in ("https", "http") and url.host():
                QDesktopServices.openUrl(url)
            else:
                self.show_error("作品详情页地址无效，仅支持网页链接。")

    def toggle_ignored(self):
        row = self.selected_view()
        if row is None:
            return
        try:
            self.matches.set_ignored(self.actress.id, row.movie.id, not row.is_ignored)
            self.reload()
        except Exception as error:  # noqa: BLE001 -- UI boundary
            self.show_error(str(error))

    def choose_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择元数据文件", "", "元数据 (*.json *.csv)")
        if path:
            self.import_file(path)

    def import_file(self, path: str):
        provider = FileCatalogProvider(path, self.actress.name)

        def work(cancel, progress):
            if cancel.is_set():
                return None
            provider._load()
            if cancel.is_set():
                return None
            self.service.bind(
                self.actress.id, provider, provider.search_actresses(self.actress.name)[0]
            )
            return self.service.sync(self.actress.id, provider, cancel, progress)

        self.start_task("sync", work)

    def search_dmm(self):
        try:
            provider = DmmProvider()
        except ProviderError as error:
            self.show_error(str(error))
            return
        name = self.actress.japanese_name or self.actress.name
        self.start_task(
            "search", lambda _cancel, _progress: provider.search_actresses(name), provider
        )

    def discover_solo_catalog(self):
        discovery = SoloCatalogDiscoveryService(self.service)
        self.start_task(
            "discover",
            lambda cancel, progress: discovery.run(self.actress, cancel, progress),
        )

    def sync_dmm(self):
        try:
            provider = DmmProvider()
        except ProviderError as error:
            self.show_error(str(error))
            return
        if not any(
            source.provider_id == provider.id
            for source, _ in self.service.bindings(self.actress.id)
        ):
            self.show_error("请先查找并确认 DMM 女优身份。")
            return
        self.start_task(
            "sync",
            lambda cancel, progress: self.service.sync(self.actress.id, provider, cancel, progress),
            provider,
        )

    def search_s1(self):
        url, accepted = QInputDialog.getText(
            self,
            "查找 S1 官网女优身份",
            "输入日文名，或粘贴 S1 官网资料页 HTTPS 地址：",
        )
        if accepted and url.strip():
            provider = S1PublicProvider()
            self.start_task(
                "search", lambda _cancel, _progress: provider.search_actresses(url), provider
            )

    def sync_s1(self):
        self._sync_s1_with_radius(0)

    def sync_s1_adjacent(self):
        radius, accepted = QInputDialog.getInt(
            self,
            "S1 邻号补查",
            "每个已知番号前后各检查多少个编号？（最多 10）",
            2,
            1,
            10,
        )
        if accepted:
            self._sync_s1_with_radius(radius)

    def _sync_s1_with_radius(self, radius: int):
        provider = S1PublicProvider(adjacent_radius=radius)
        if not any(
            source.provider_id == provider.id
            for source, _ in self.service.bindings(self.actress.id)
        ):
            self.show_error("请先粘贴 S1 官网资料页并确认对应的女优身份。")
            return
        self.start_task(
            "sync",
            lambda cancel, progress: self.service.sync(self.actress.id, provider, cancel, progress),
            provider,
        )

    def start_task(self, operation: str, work: Callable, provider=None):
        if self.worker:
            return
        self.operation, self.active_provider = operation, provider
        self.worker = MetadataWorker(work, self)
        self.worker.progress.connect(self.update_progress, Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self.task_finished, Qt.ConnectionType.QueuedConnection)
        for button in (
            self.discovery_button,
            self.import_button,
            self.bind_button,
            self.sync_button,
            self.s1_bind_button,
            self.s1_sync_button,
            self.s1_adjacent_button,
            self.cover_button,
        ):
            button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText("正在读取作品元数据…")
        self.worker.start()

    @Slot(object)
    def update_progress(self, progress: SyncProgress):
        if self.worker and not self.worker.cancel.is_set():
            self.status.setText(f"正在同步：{progress.pages} 页，{progress.items} 条作品…")

    def cancel_task(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消，等待当前请求返回…")

    @Slot()
    def task_finished(self):
        worker = self.worker
        worker.wait()
        error, result = worker.error, worker.result
        # Parent dialog owns the completed worker until the dialog itself closes.
        # Deferred deletion during nested Qt event loops can race queued callbacks.
        self.worker = None
        for button in (
            self.discovery_button,
            self.import_button,
            self.bind_button,
            self.sync_button,
            self.s1_bind_button,
            self.s1_sync_button,
            self.s1_adjacent_button,
        ):
            button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if self.operation == "search" and result and not self.close_pending:
            labels = [f"{person.name} · ID {person.external_id}" for person in result]
            chosen, accepted = QInputDialog.getItem(
                self, "确认来源身份", "选择与当前女优对应的来源身份：", labels, 0, False
            )
            if accepted:
                try:
                    self.service.bind(
                        self.actress.id, self.active_provider, result[labels.index(chosen)]
                    )
                except Exception as problem:  # noqa: BLE001 -- UI boundary
                    error = str(problem)
        elif self.operation == "search" and result == ():
            error = "来源未找到候选女优，可检查日文名或使用本地导入。"
        try:
            self.reload()
        except Exception as problem:  # noqa: BLE001 -- UI boundary
            error = str(problem)
        if self.operation == "discover" and result and not self.close_pending:
            self.status.setText("自动检索：" + result.summary())
        if error and not self.close_pending:
            self.show_error(error)
        self.show_cover()
        if self.close_pending:
            super().reject()

    def show_error(self, message: str):
        box = QMessageBox(self)
        box.setWindowTitle("作品库提示")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message)
        box.exec()

    def reject(self):
        if self.worker:
            self.close_pending = True
            self.cancel_task()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.close_pending = True
            self.cancel_task()
            event.ignore()
        else:
            event.accept()
