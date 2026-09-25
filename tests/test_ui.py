from PySide6.QtWidgets import QDialog, QMessageBox

from av_library.config import AppPaths
from av_library.services.actresses import ActressService
from av_library.ui.actress_dialog import ActressDialog
from av_library.ui.main_window import MainWindow
from av_library.ui.theme import STYLESHEET


def test_gui_add_edit_filter_remove(database, tmp_path, monkeypatch, qapp):
    app = qapp
    app.setStyleSheet(STYLESHEET)
    service = ActressService(database)
    window = MainWindow(service, AppPaths(tmp_path))
    window.show()
    app.processEvents()
    assert window.table.rowCount() == 0
    dialog = ActressDialog(service, parent=window)
    dialog.name_input.setText("翼舞")
    dialog.aliases_input.setPlainText("测试别名，TEST")
    dialog.folder_input.setText(str(tmp_path / "offline"))
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    window.reload(dialog.saved_id)
    assert window.current.name == "翼舞"
    assert window.table.item(0, 1).text() == "待同步"
    edit = ActressDialog(service, window.current, window)
    edit.folder_input.setText(str(tmp_path / "changed"))
    edit.save()
    window.reload(edit.saved_id)
    assert "changed" in window.folder_label.text()
    window.search.setText("TEST")
    assert window.table.rowCount() == 1
    window.search.setText("无匹配")
    assert window.table.rowCount() == 0
    assert window.current is None
    window.search.clear()
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    window.remove_actress()
    assert window.table.rowCount() == 0
    assert service.list() == []
    window.close()
