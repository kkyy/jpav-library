STYLESHEET = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #24334b; }
QMainWindow, QDialog { background: #f3f5f9; }
QFrame#sidebar { background: #19263d; border-radius: 16px; }
QFrame#sidebar QLabel { color: #c7d3e5; }
QFrame#sidebar QLabel#brand { color: white; font-size: 23px; font-weight: 700; }
QLabel#heading { font-size: 27px; font-weight: 700; color: #17263d; }
QLabel#subheading { font-size: 17px; font-weight: 600; }
QLabel#muted { color: #687991; }
QLabel#notice { background: #e8eefb; padding: 12px; border-radius: 8px; color: #31568a; }
QFrame#card { background: white; border: 1px solid #e1e7ef; border-radius: 12px; }
QLabel#avatar { background: #e4ebf7; border-radius: 12px; font-size: 30px; color: #426299; }
QLineEdit, QPlainTextEdit { background: white; border: 1px solid #ccd5e3;
    border-radius: 7px; padding: 9px; selection-background-color: #3669d6; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #3669d6; }
QPushButton { background: white; border: 1px solid #ccd5e3; border-radius: 7px;
    padding: 9px 15px; }
QPushButton:hover { background: #edf2fc; border-color: #8da9e4; }
QPushButton#primary { background: #3267d6; color: white; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #2857b9; }
QPushButton#danger { color: #af3d4d; }
QPushButton:disabled { color: #98a3b3; background: #edf0f5; border-color: #e0e4eb; }
QTableWidget { background: white; border: 1px solid #e1e7ef; border-radius: 8px;
    gridline-color: #eef1f6; selection-background-color: #e7efff; selection-color: #254d91; }
QHeaderView::section { background: #edf1f7; padding: 10px; border: none; color: #586b87; }
QTableWidget::item { padding: 10px; }
QStatusBar { color: #738099; }
"""
