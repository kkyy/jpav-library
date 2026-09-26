from pathlib import Path
from threading import Event

from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from av_library.config import AppPaths
from av_library.db.models import Actress
from av_library.services.actresses import ActressService, ValidationError
from av_library.services.automatic_sync import AutomaticSyncService
from av_library.services.backups import BackupService
from av_library.services.covers import CoverService
from av_library.services.matching import MatchService, MovieView
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


class ClickableFrame(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class GlobalUpdateWorker(QThread):
    def __init__(self, service: AutomaticSyncService, parent=None):
        super().__init__(parent)
        self.service = service
        self.cancel = Event()
        self.result = (0, 0)

    def run(self):
        try:
            self.result = self.service.run_due(self.cancel, force=True)
        except Exception:  # noqa: BLE001 -- worker boundary
            self.result = (0, 1)


class CoverBatchWorker(QThread):
    ready = Signal(dict)

    def __init__(self, covers: CoverService, urls: list[str], parent=None):
        super().__init__(parent)
        self.covers, self.urls = covers, urls

    def run(self):
        from concurrent.futures import ThreadPoolExecutor

        def fetch(url):
            try:
                path = self.covers.fetch(url)
                return url, str(path)
            except Exception:  # noqa: BLE001 -- an unavailable cover leaves its placeholder
                return url, ""

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.ready.emit(dict(pool.map(fetch, self.urls)))


class MainWindow(QMainWindow):
    def __init__(self, service: ActressService, paths: AppPaths):
        super().__init__()
        self.service, self.paths = service, paths
        self.covers = CoverService(paths.covers)
        self.current: Actress | None = None
        self.rows: list[Actress] = []
        self.summaries = {}
        self.movie_rows: list[MovieView] = []
        self.selected_movie: MovieView | None = None
        self.auto_worker: GlobalUpdateWorker | None = None
        self.cover_worker: CoverBatchWorker | None = None
        self.cover_paths: dict[str, str] = {}
        self.movie_search_text = ""
        self._actress_columns: int | None = None
        self._movie_columns: int | None = None
        self._movie_grid_width: int | None = None
        self.setWindowTitle("本地作品库 · JPAV Library")
        self.resize(1280, 860)
        self.setMinimumSize(1000, 700)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(168)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 24, 16, 18)
        side.setSpacing(10)
        side.addWidget(label("本地作品库", "brand"))
        side.addWidget(label("JPAV LIBRARY", "sidecaption"))
        side.addSpacing(22)
        self.side_home = QPushButton("◈  女优收藏")
        self.side_home.clicked.connect(self.show_actress_grid)
        side.addWidget(self.side_home)
        side.addStretch()
        storage = QPushButton("备份与恢复")
        storage.clicked.connect(self.show_storage)
        side.addWidget(storage)
        preferences = QPushButton("设置")
        preferences.clicked.connect(self.show_settings)
        side.addWidget(preferences)
        outer.addWidget(sidebar)

        body = QVBoxLayout()
        body.setSpacing(12)
        outer.addLayout(body, 1)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("搜索女优名称、别名、番号或作品标题")
        self.search.textChanged.connect(self.on_search_changed)
        toolbar.addWidget(self.search, 1)
        self.add_button = QPushButton("＋ 添加女优")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(lambda: self.edit_actress(new=True))
        toolbar.addWidget(self.add_button)
        self.global_button = QPushButton("↻ 全局更新")
        self.global_button.clicked.connect(self.global_update)
        toolbar.addWidget(self.global_button)
        self.global_search_button = QPushButton("全局搜索")
        self.global_search_button.clicked.connect(self.open_global_search)
        toolbar.addWidget(self.global_search_button)
        body.addLayout(toolbar)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.grid_page = QWidget()
        grid_layout = QVBoxLayout(self.grid_page)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(10)
        grid_header = QHBoxLayout()
        grid_header.addWidget(label("女优收藏", "heading"))
        grid_header.addStretch()
        self.count_label = label("", "muted")
        grid_header.addWidget(self.count_label)
        grid_layout.addLayout(grid_header)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.actress_canvas = QWidget()
        self.actress_grid = QGridLayout(self.actress_canvas)
        self.actress_grid.setContentsMargins(4, 4, 4, 4)
        self.actress_grid.setSpacing(14)
        self.actress_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        grid_scroll.setWidget(self.actress_canvas)
        grid_layout.addWidget(grid_scroll, 1)
        self.grid_empty = label("还没有女优资料。点击「添加女优」开始。", "muted", True)
        grid_layout.addWidget(self.grid_empty)
        self.stack.addWidget(self.grid_page)

        self.detail_page = QWidget()
        detail_layout = QVBoxLayout(self.detail_page)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(9)
        actress_bar = QHBoxLayout()
        self.back_button = QPushButton("← 返回女优")
        self.back_button.clicked.connect(self.show_actress_grid)
        actress_bar.addWidget(self.back_button)
        self.detail_avatar = label("", "avatar")
        self.detail_avatar.setFixedSize(44, 44)
        self.detail_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        actress_bar.addWidget(self.detail_avatar)
        self.detail_name = label("", "heading")
        actress_bar.addWidget(self.detail_name)
        self.detail_aliases = label("", "muted")
        actress_bar.addWidget(self.detail_aliases, 1)
        self.edit_button = QPushButton("编辑资料")
        self.edit_button.clicked.connect(lambda: self.edit_actress())
        actress_bar.addWidget(self.edit_button)
        detail_layout.addLayout(actress_bar)
        self.collection_stats = label("作品库待同步", "compactstats")
        detail_layout.addWidget(self.collection_stats)
        work_bar = QHBoxLayout()
        self.work_search = QLineEdit()
        self.work_search.setPlaceholderText("筛选番号或标题")
        self.work_search.textChanged.connect(self.render_movies)
        work_bar.addWidget(self.work_search, 1)
        self.work_filter = QComboBox()
        self.work_filter.addItems(["全部状态", "已收藏", "缺少", "已忽略", "合集", "多人企划"])
        self.work_filter.currentIndexChanged.connect(self.render_movies)
        work_bar.addWidget(self.work_filter)
        self.work_count = label("", "muted")
        work_bar.addWidget(self.work_count)
        detail_layout.addLayout(work_bar)
        self.movie_scroll = QScrollArea()
        self.movie_scroll.setWidgetResizable(True)
        self.movie_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.movie_canvas = QWidget()
        self.movie_grid = QGridLayout(self.movie_canvas)
        self.movie_grid.setContentsMargins(4, 4, 4, 4)
        self.movie_grid.setSpacing(12)
        self.movie_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.movie_scroll.setWidget(self.movie_canvas)
        detail_layout.addWidget(self.movie_scroll, 1)
        self.movie_empty = label("作品库尚未同步。点击下方「同步作品库」开始。", "muted", True)
        detail_layout.addWidget(self.movie_empty)
        self.movie_info = QFrame()
        self.movie_info.setObjectName("card")
        self.movie_info.setMaximumHeight(176)
        info_layout = QVBoxLayout(self.movie_info)
        info_layout.setContentsMargins(12, 9, 12, 8)
        info_layout.setSpacing(4)
        self.movie_title = label("选择作品封面查看资料", "subheading")
        info_layout.addWidget(self.movie_title)
        self.movie_meta = label("", "muted", True)
        info_layout.addWidget(self.movie_meta)
        self.movie_status = label("", "muted", True)
        info_layout.addWidget(self.movie_status)
        controls = QHBoxLayout()
        self.scan_button = QPushButton("扫描本地文件")
        self.scan_button.clicked.connect(self.open_scan)
        controls.addWidget(self.scan_button)
        self.sync_button = QPushButton("同步作品库")
        self.sync_button.setObjectName("primary")
        self.sync_button.clicked.connect(self.open_metadata)
        controls.addWidget(self.sync_button)
        self.missing_button = QPushButton("查看缺少")
        self.missing_button.clicked.connect(lambda: self.open_metadata(2))
        controls.addWidget(self.missing_button)
        self.latest_button = QPushButton("查看最新")
        self.latest_button.clicked.connect(lambda: self.open_metadata(4))
        controls.addWidget(self.latest_button)
        self.folder_button = QPushButton("打开目录")
        self.folder_button.clicked.connect(self.open_folder)
        controls.addWidget(self.folder_button)
        self.remove_button = QPushButton("移除女优")
        self.remove_button.setObjectName("danger")
        self.remove_button.clicked.connect(self.remove_actress)
        controls.addWidget(self.remove_button)
        controls.addStretch()
        info_layout.addLayout(controls)
        detail_layout.addWidget(self.movie_info)
        self.stack.addWidget(self.detail_page)
        self.reflow_timer = QTimer(self)
        self.reflow_timer.setSingleShot(True)
        self.reflow_timer.setInterval(100)
        self.reflow_timer.timeout.connect(self.reflow_grids)
        self.statusBar().showMessage(f"本地数据库：{paths.database}")
        self.reload()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.check_auto_sync)
        self.refresh_timer.start(10 * 60 * 1000)
        QTimer.singleShot(2000, self.check_auto_sync)

    def on_search_changed(self, _text):
        if self.stack.currentWidget() is self.grid_page:
            self.reload()
        elif self.current:
            self.work_search.blockSignals(True)
            self.work_search.setText(self.search.text())
            self.work_search.blockSignals(False)
            self.render_movies()

    def _clear_grid(self, grid):
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def reload(self, select_id: int | None = None):
        previous = select_id or (self.current.id if self.current else None)
        try:
            self.rows = self.service.list(self.search.text())
            matcher = MatchService(self.service.database)
            self.summaries = {row.id: matcher.summary(row.id) for row in self.rows}
        except SQLAlchemyError:
            QMessageBox.critical(self, "读取失败", "无法读取数据库，请检查文件权限或稍后重试。")
            return
        if self.rows:
            self.rows.sort(key=lambda row: row.last_synced_at or row.updated_at, reverse=True)
        self._clear_grid(self.actress_grid)
        columns = self.grid_columns(self.actress_canvas.width(), 196, 14, 6)
        self._actress_columns = columns
        for index, row in enumerate(self.rows):
            card = ClickableFrame()
            card.setObjectName("photoCard")
            card.setFixedSize(196, 248)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 10, 10, 9)
            card_layout.setSpacing(5)
            photo = QLabel()
            photo.setObjectName("actressPhoto")
            photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            photo.setFixedSize(174, 158)
            pixmap = QPixmap(row.avatar_path) if row.avatar_path else QPixmap()
            if not pixmap.isNull():
                photo.setPixmap(pixmap.scaled(photo.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            else:
                photo.setText(row.name[:1])
            card_layout.addWidget(photo)
            card_layout.addWidget(label(row.name, "cardTitle"))
            stats = self.summaries[row.id]
            stats_text = f"{stats.collected} / {stats.needed} 已收藏" if row.last_synced_at else "尚未同步作品库"
            card_layout.addWidget(label(stats_text, "muted"))
            percent = f" · {stats.completion_percent:.1f}%" if stats.completion_percent is not None else ""
            card_layout.addWidget(label(f"缺少 {stats.missing if stats.scanned else '—'}{percent}", "muted"))
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.clicked.connect(lambda actress=row: self.open_actress(actress))
            self.actress_grid.addWidget(card, index // columns, index % columns)
        self.count_label.setText(f"{len(self.rows)} 位女优")
        self.grid_empty.setVisible(not self.rows)
        if self.current and any(row.id == self.current.id for row in self.rows):
            self.current = next(row for row in self.rows if row.id == self.current.id)
            if self.stack.currentWidget() is self.detail_page:
                self.open_actress(self.current, refresh=True)
        elif not self.rows and self.stack.currentWidget() is self.detail_page:
            self.show_actress_grid()
        if previous is not None:
            selected = next((row for row in self.rows if row.id == previous), None)
            if selected:
                self.current = selected

    def show_actress_grid(self):
        self.stack.setCurrentWidget(self.grid_page)
        self.current = None

    def open_actress(self, actress: Actress, refresh: bool = False):
        try:
            self.current = self.service.get(actress.id)
        except SQLAlchemyError:
            self.current = actress
        self.stack.setCurrentWidget(self.detail_page)
        self.detail_name.setText(self.current.name)
        self.detail_aliases.setText(self.current.japanese_name or "")
        avatar = QPixmap(self.current.avatar_path) if self.current.avatar_path else QPixmap()
        self.detail_avatar.setPixmap(avatar.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation) if not avatar.isNull() else QPixmap())
        if avatar.isNull():
            self.detail_avatar.setText(self.current.name[:1])
        self.work_search.setText(self.search.text())
        self.load_movies(self.current.id)

    def load_movies(self, actress_id: int):
        try:
            matcher = MatchService(self.service.database)
            self.movie_rows = matcher.views(actress_id)
            summary = matcher.summary(actress_id, self.movie_rows)
        except Exception as error:  # noqa: BLE001 -- UI boundary
            self.statusBar().showMessage(f"无法加载作品：{error}", 7000)
            return
        percent = f"{summary.completion_percent:.1f}%" if summary.completion_percent is not None else "待扫描"
        missing = summary.missing if summary.scanned else "待扫描"
        latest = f"最新 {summary.latest.movie.code}" if summary.latest else "暂无最新作品"
        self.collection_stats.setText(
            f"作品 {summary.total}  ·  已收藏 {summary.collected}  ·  缺少 {missing}  ·  完成度 {percent}  ·  忽略 {summary.ignored}  ·  {latest}"
        )
        self.render_movies()

    def render_movies(self, *_args):
        if not hasattr(self, "movie_grid"):
            return
        query = self.work_search.text().casefold() if hasattr(self, "work_search") else ""
        status_index = self.work_filter.currentIndex() if hasattr(self, "work_filter") else 0
        status_values = ("", "collected", "missing", "ignored", "compilation", "multi_actress")
        status_key = status_values[status_index]
        rows = [
            row for row in self.movie_rows
            if query in f"{row.movie.code} {row.movie.title} {row.movie.japanese_title}".casefold()
            and (not status_key or row.status == status_key)
        ]
        self._clear_grid(self.movie_grid)
        # Emby-inspired poster shelf: consistent portrait ratio and a tight, image-led grid.
        rows = [row for row in rows if row.is_solo and not row.is_compilation]
        spacing = self.movie_grid.spacing()
        usable_width = max(1, self.movie_scroll.viewport().width() - 8)
        columns = self.grid_columns(usable_width, 186, spacing, 7)
        self._movie_columns = columns
        self._movie_grid_width = usable_width
        base_width, extra_pixels = divmod(usable_width - spacing * (columns - 1), columns)
        needed_urls = []
        for index, view in enumerate(rows):
            poster_width = base_width + int(index % columns < extra_pixels)
            card = ClickableFrame()
            card.setObjectName("posterCard")
            card.setFixedWidth(poster_width)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setProperty("selected", bool(self.selected_movie and self.selected_movie.movie.id == view.movie.id))
            title = view.movie.title or view.movie.japanese_title or "作品标题未收录"
            status = "已收藏" if view.local_paths else "已忽略" if view.is_ignored else "缺少"
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 5)
            card_layout.setSpacing(5)
            poster = QLabel()
            poster.setObjectName("posterImage")
            poster.setFixedSize(poster_width, round(poster_width * 1.5))
            poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
            poster_url = view.movie.cover_url
            cached = self.covers.cached(view.movie.cover_url)
            if cached:
                poster.setPixmap(self._crop_poster(cached, poster.size()))
            else:
                poster.setText(view.movie.code if poster_url else "暂无封面")
                if poster_url:
                    needed_urls.append(poster_url)
            card_layout.addWidget(poster, alignment=Qt.AlignmentFlag.AlignHCenter)
            card_layout.addWidget(label(view.movie.code, "posterCode"))
            short_title = title if len(title) <= 32 else title[:31] + "…"
            card_layout.addWidget(label(short_title, "posterTitle", True))
            card_layout.addWidget(label(status, "posterStatus"))
            card.setToolTip(f"{view.movie.code} · {title} · {status}")
            card.clicked.connect(lambda movie=view: self.select_movie(movie))
            self.movie_grid.addWidget(card, index // columns, index % columns, Qt.AlignmentFlag.AlignTop)
        self.work_count.setText(f"{len(rows)} 部")
        self.movie_empty.setVisible(not rows)
        if not rows:
            self.movie_empty.setText(
                "没有符合条件的作品。"
                if self.movie_rows
                else "作品库尚未同步，或当前没有可展示的单人非合集作品。"
            )
        if rows and (not self.selected_movie or all(self.selected_movie.movie.id != row.movie.id for row in rows)):
            self.selected_movie = rows[0]
            self.update_movie_info(rows[0])
        if needed_urls and self.cover_worker is None:
            pending = list(dict.fromkeys(url for url in needed_urls if not self.covers.cached(url)))[:12]
            if pending:
                self.cover_worker = CoverBatchWorker(self.covers, pending, self)
                self.cover_worker.ready.connect(self.covers_ready, Qt.ConnectionType.QueuedConnection)
                self.cover_worker.finished.connect(self.cover_batch_finished, Qt.ConnectionType.QueuedConnection)
                self.cover_worker.finished.connect(self.cover_worker.deleteLater)
                self.cover_worker.start()

    @staticmethod
    def grid_columns(width: int, item_width: int, spacing: int, maximum: int) -> int:
        return max(1, min(maximum, (max(0, width) + spacing) // (item_width + spacing)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        timer = getattr(self, "reflow_timer", None)
        if timer:
            timer.start()

    @Slot()
    def reflow_grids(self):
        actress_columns = self.grid_columns(self.actress_canvas.width(), 196, 14, 6)
        movie_width = max(1, self.movie_scroll.viewport().width() - 8)
        if actress_columns != self._actress_columns:
            self.reload(self.current.id if self.current else None)
        if movie_width != self._movie_grid_width and self.current:
            self.render_movies()

    @Slot(dict)
    def covers_ready(self, covers: dict):
        self.cover_paths.update({url: path for url, path in covers.items() if path})
        self.render_movies()

    @staticmethod
    def _crop_poster(path: Path, size: QSize) -> QPixmap:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return pixmap
        scaled = pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        left = max(0, (scaled.width() - size.width()) // 2)
        top = max(0, (scaled.height() - size.height()) // 2)
        return scaled.copy(left, top, size.width(), size.height())

    @Slot()
    def cover_batch_finished(self):
        self.cover_worker = None
        self.render_movies()

    def select_movie(self, view: MovieView):
        self.selected_movie = view
        self.update_movie_info(view)
        self.render_movies()

    def update_movie_info(self, view: MovieView):
        movie = view.movie
        self.movie_title.setText(f"{movie.code}  ·  {movie.title or movie.japanese_title or '作品标题未收录'}")
        self.movie_meta.setText(
            f"发行日期：{movie.release_date or '未知'}  ·  制作商：{movie.manufacturer or '未知'}  ·  系列：{movie.series or '—'}"
        )
        state = "已收藏" if view.local_paths else "已忽略" if view.is_ignored else "多人企划（不统计）" if not view.is_solo else "合集（不统计）" if view.is_compilation else "缺少"
        local = "\n本地文件：" + "；".join(view.local_paths) if view.local_paths else ""
        self.movie_status.setText(f"状态：{state}{local}")

    def global_update(self):
        if self.auto_worker:
            return
        self.auto_worker = GlobalUpdateWorker(AutomaticSyncService(self.service.database), self)
        self.auto_worker.finished.connect(self.global_update_finished, Qt.ConnectionType.QueuedConnection)
        self.global_button.setEnabled(False)
        self.global_button.setText("正在更新…")
        self.statusBar().showMessage("正在更新所有已绑定的数据源…")
        self.auto_worker.start()

    @Slot()
    def global_update_finished(self):
        worker = self.auto_worker
        if worker is None:
            return
        worker.wait()
        self.auto_worker = None
        done, failed = worker.result
        self.global_button.setEnabled(True)
        self.global_button.setText("↻ 全局更新")
        self.reload(self.current.id if self.current else None)
        self.statusBar().showMessage(f"全局更新完成：成功 {done} 项，失败 {failed} 项。", 10000)

    def open_scan(self):
        if not self.current:
            return
        dialog = ScanDialog(ScanService(self.service.database), self.current, self)
        dialog.exec()
        self.reload(self.current.id)

    def open_metadata(self, filter_index: int = 0):
        if not self.current:
            return
        dialog = MetadataDialog(MetadataService(self.service.database), self.current, self, cover_dir=self.paths.covers)
        dialog.filter.setCurrentIndex(filter_index)
        dialog.exec()
        self.reload(self.current.id)

    def open_global_search(self):
        SearchDialog(SearchService(self.service.database), self, self.paths.covers).exec()
        self.reload(self.current.id if self.current else None)

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
        answer = QMessageBox.question(self, "移除女优资料", "确定移除所选女优资料？\n仅移除数据库记录，本地文件和文件夹会保留。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
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
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.warning(self, "无法打开文件夹", "目录不存在或磁盘未连接，请检查路径。")

    def show_storage(self):
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
        self.global_update()

    def closeEvent(self, event):
        self.refresh_timer.stop()
        for worker in (self.auto_worker, self.cover_worker):
            if worker and worker.isRunning():
                worker.wait()
        event.accept()
