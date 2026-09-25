from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from av_library.services.backups import BackupError, BackupService


class BackupDialog(QDialog):
    def __init__(self, service: BackupService, parent=None):
        super().__init__(parent)
        self.service = service
        self.restored = False
        self.setWindowTitle("数据备份与恢复")
        self.resize(560, 310)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("本地数据库备份")
        title.setObjectName("heading")
        layout.addWidget(title)
        description = QLabel(
            "导出完整 SQLite 数据库快照，包含女优、作品、扫描记录、人工修正和忽略状态。"
            "封面可按需重新缓存；视频文件不会写入备份。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        location = QLabel(
            f"当前数据库：{service.database_path}\n自动安全备份：{service.backup_dir}"
        )
        location.setTextFormat(Qt.TextFormat.PlainText)
        location.setWordWrap(True)
        layout.addWidget(location)
        export_button = QPushButton("导出数据库备份…")
        export_button.setObjectName("primary")
        export_button.clicked.connect(self.export_backup)
        layout.addWidget(export_button)
        import_button = QPushButton("从数据库备份恢复…")
        import_button.clicked.connect(self.import_backup)
        layout.addWidget(import_button)
        note = QLabel("恢复前会自动备份当前数据库。恢复后可继续在本窗口使用软件。")
        note.setObjectName("notice")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def export_backup(self):
        suggested = str(Path.home() / "JPAVLibrary-backup.sqlite3")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出数据库", suggested, "SQLite 备份 (*.sqlite3)"
        )
        if not path:
            return
        try:
            target = self.service.backup(Path(path))
        except (BackupError, OSError) as error:
            QMessageBox.warning(self, "备份失败", str(error))
        else:
            QMessageBox.information(self, "备份完成", f"已保存数据库快照：\n{target}")

    def import_backup(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据库备份", str(Path.home()), "SQLite 备份 (*.sqlite3)"
        )
        if not path:
            return
        try:
            version = self.service.verify(Path(path))
        except BackupError as error:
            QMessageBox.warning(self, "备份无效", str(error))
            return
        choice = QMessageBox.question(
            self,
            "恢复数据库",
            f"将从 v{version} 备份恢复，替换当前作品库数据。\n"
            "恢复前会自动在数据目录保存当前数据库。确认继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            safety = self.service.restore(Path(path))
        except (BackupError, OSError, RuntimeError) as error:
            QMessageBox.warning(self, "恢复失败", str(error))
        else:
            self.restored = True
            QMessageBox.information(self, "恢复完成", f"当前库已恢复。原数据库备份位于：\n{safety}")
            self.accept()
