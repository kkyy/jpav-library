from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from av_library.services.actresses import ActressService
from av_library.services.metadata_sync import MetadataService
from av_library.services.search import SearchService
from av_library.ui.metadata_dialog import MetadataDialog


class SearchDialog(QDialog):
    def __init__(self, service: SearchService, parent=None, cover_dir: Path | None = None):
        super().__init__(parent)
        self.service = service
        self.cover_dir = cover_dir
        self.setWindowTitle("全局搜索")
        self.resize(970, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.addWidget(QLabel("搜索女优、番号或作品标题"))
        self.query = QLineEdit()
        self.query.setPlaceholderText("例如：翼舞 或 MIDV-123")
        self.query.textChanged.connect(self.reload)
        layout.addWidget(self.query)
        self.summary = QLabel("输入关键词开始搜索。")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["女优", "番号", "标题", "状态", "本地文件路径"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 230)
        self.table.cellDoubleClicked.connect(self.open_result)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        row.addStretch()
        layout.addLayout(row)

    def reload(self):
        result = self.service.search(self.query.text())
        self.rows = result.movies
        actresses = "、".join(row.name for row in result.actresses[:10]) or "无"
        self.summary.setText(
            f"匹配女优：{actresses} · 匹配作品：{len(self.rows)} 条（最多显示 100 条）。"
        )
        self.table.setRowCount(len(self.rows))
        for index, hit in enumerate(self.rows):
            values = (
                hit.actress_name,
                hit.code,
                hit.title or "—",
                {"collected": "已收藏", "missing": "缺少", "ignored": "已忽略"}[hit.status],
                hit.local_paths[0] if hit.local_paths else "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip("\n".join(hit.local_paths) if column == 4 else value)
                self.table.setItem(index, column, item)

    def open_result(self, index: int, _column: int):
        if not 0 <= index < len(self.rows):
            return
        hit = self.rows[index]
        actress = ActressService(self.service.database).get(hit.actress_id)
        dialog = MetadataDialog(
            MetadataService(self.service.database), actress, self, cover_dir=self.cover_dir
        )
        dialog.search.setText(hit.code)
        dialog.exec()
        self.reload()
