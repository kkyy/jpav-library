import re

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from av_library.db.models import Actress
from av_library.services.actresses import ActressInput, ActressService, ValidationError


class ActressDialog(QDialog):
    def __init__(self, service: ActressService, actress: Actress | None = None, parent=None):
        super().__init__(parent)
        self.service = service
        self.actress_id = actress.id if actress else None
        self.saved_id: int | None = None
        self.setWindowTitle("编辑女优" if actress else "添加女优")
        self.resize(610, 465)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        heading = QLabel(self.windowTitle())
        heading.setObjectName("heading")
        layout.addWidget(heading)
        form = QFormLayout()
        form.setSpacing(14)
        self.name_input = QLineEdit(actress.name if actress else "")
        self.name_input.setPlaceholderText("例如：翼舞")
        self.name_input.setMaxLength(100)
        self.japanese_input = QLineEdit(actress.japanese_name if actress else "")
        self.japanese_input.setPlaceholderText("选填")
        self.aliases_input = QPlainTextEdit("\n".join(actress.aliases) if actress else "")
        self.aliases_input.setPlaceholderText("每行一个，也支持中文或英文逗号分隔")
        self.aliases_input.setMaximumHeight(80)
        self.folder_input = QLineEdit(actress.folder_path if actress else "")
        self.folder_input.setPlaceholderText("D:\\AV\\翼舞")
        self.avatar_input = QLineEdit(actress.avatar_path or "" if actress else "")
        self.avatar_input.setPlaceholderText("选填：本地图片路径")
        for label, widget in (
            ("常用名 *", self.name_input),
            ("日文名", self.japanese_input),
            ("别名", self.aliases_input),
        ):
            form.addRow(label, widget)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_input)
        browse = QPushButton("选择文件夹")
        browse.clicked.connect(self.choose_folder)
        folder_row.addWidget(browse)
        form.addRow("本地目录 *", folder_row)
        avatar_row = QHBoxLayout()
        avatar_row.addWidget(self.avatar_input)
        avatar_button = QPushButton("选择图片")
        avatar_button.clicked.connect(self.choose_avatar)
        avatar_row.addWidget(avatar_button)
        form.addRow("头像", avatar_row)
        layout.addLayout(form)
        note = QLabel(
            "目录可随时修改；允许保存暂时离线的硬盘目录。\n保存后点击「扫描 / 本地文件」识别视频文件名。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择本地视频文件夹")
        if folder:
            self.folder_input.setText(folder)

    def choose_avatar(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择头像", "", "图片 (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self.avatar_input.setText(path)

    def save(self):
        try:
            row = self.service.save(
                ActressInput(
                    name=self.name_input.text(),
                    japanese_name=self.japanese_input.text(),
                    aliases=tuple(re.split(r"[\n,，;；]", self.aliases_input.toPlainText())),
                    folder_path=self.folder_input.text(),
                    avatar_path=self.avatar_input.text().strip() or None,
                ),
                self.actress_id,
            )
            self.saved_id = row.id
        except ValidationError as error:
            QMessageBox.warning(self, "请检查输入", str(error))
            return
        except SQLAlchemyError:
            QMessageBox.critical(
                self, "保存失败", "数据库写入失败，请检查磁盘空间、文件权限或稍后重试。"
            )
            return
        self.accept()
