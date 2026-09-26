import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from av_library.config import AppPaths
from av_library.services.scanning import ScanService
from av_library.services.settings import SettingsService


class SettingsDialog(QDialog):
    def __init__(self, service: SettingsService, paths: AppPaths, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("设置")
        self.resize(620, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        heading = QLabel("应用设置")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        form = QFormLayout()
        for label, value in (
            ("数据库", str(paths.database)),
            ("封面缓存", str(paths.covers)),
            ("日志", str(paths.logs)),
        ):
            text = QLabel(value)
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setWordWrap(True)
            form.addRow(label, text)
        options = ScanService(service.database).options()
        form.addRow("扫描子目录", QLabel("是" if options.recursive else "否（可在扫描窗口修改）"))
        form.addRow("视频扩展名", QLabel(", ".join(options.extensions)))
        form.addRow("视频信息读取", QLabel("关闭；仅扫描文件名，不读取 ffprobe"))
        form.addRow("番号规则", QLabel("标准番号 + FC2-PPV；有歧义时手动指定"))
        form.addRow(
            "DMM 数据源",
            QLabel(
                "已配置环境变量"
                if os.environ.get("DMM_API_ID") and os.environ.get("DMM_AFFILIATE_ID")
                else "未配置凭据；可使用 JSON/CSV 离线导入"
            ),
        )
        form.addRow("代理", QLabel("使用系统或 HTTPS_PROXY 环境变量（可选）"))
        self.interval = QSpinBox()
        self.interval.setRange(0, 720)
        self.interval.setSuffix(" 小时（0 为手动）")
        self.interval.setValue(service.update_interval_hours())
        form.addRow("自动更新间隔", self.interval)
        layout.addLayout(form)
        note = QLabel(
            "自动更新会刷新已绑定的 S1、IdeaPocket 来源；DMM 来源需已绑定并配置 API 凭据。首次运行按当前间隔检查。"
        )
        note.setObjectName("notice")
        note.setWordWrap(True)
        layout.addWidget(note)
        save = QPushButton("保存设置")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        layout.addWidget(save)

    def save(self):
        try:
            self.service.save_update_interval(self.interval.value())
        except Exception as error:  # noqa: BLE001 -- UI boundary
            QMessageBox.warning(self, "设置保存失败", str(error))
        else:
            self.accept()
