STYLESHEET = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #f0f1f2; }
QMainWindow, QDialog, QMenu { background: #17191d; color: #f0f1f2; }
QFrame#sidebar { background: #22252a; border-radius: 16px; }
QFrame#sidebar QLabel { color: #c5c8ca; }
QFrame#sidebar QLabel#brand { color: #ffffff; font-size: 22px; font-weight: 700; }
QLabel#heading { font-size: 26px; font-weight: 700; color: #f4f4f2; }
QLabel#subheading { font-size: 16px; font-weight: 600; color: #f2f2ef; }
QLabel#muted { color: #d0d2d3; }
QLabel#notice { background: #292e2c; padding: 12px; border-radius: 8px; color: #d1dfc9; }
QFrame#card { background: #22252a; border: 1px solid #34383e; border-radius: 12px; }
QFrame#photoCard { background: #22252a; border: 1px solid #34383e; border-radius: 12px; }
QFrame#photoCard:hover { border: 1px solid #74856d; background: #282c2d; }
QLabel#actressPhoto { background: #30343a; border-radius: 8px; color: #c1c9bd; font-size: 34px; font-weight: 600; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; color: #f4f4f2; }
QLabel#tileMeta { color: #d0d2d3; font-size: 11px; }
QLabel#posterImage { background: #292c31; color: #c0c2c4; border: none; font-size: 13px; }
QLabel#posterImage[upcoming='true'] { border: 3px solid #66d18c; }
QFrame#posterCard { background: transparent; border: 2px solid transparent; border-radius: 6px; }
QFrame#posterCard:hover { background: #252a29; border-color: #62725d; }
QFrame#posterCard[selected='true'] { background: #292f2b; border-color: #849579; }
QLabel#posterCode { color: #e3e6df; font-size: 12px; font-weight: 700; }
QLabel#posterTitle { color: #e7e8e4; font-size: 12px; }
QLabel#posterStatus { color: #d0d2d3; font-size: 11px; }
QLabel#compactstats { background: #292e2c; border-radius: 7px; padding: 8px 10px; color: #d7e2d0; font-weight: 600; }
QLabel#sidecaption { color: #a7aaa6; font-size: 10px; letter-spacing: 1px; }
QLabel#avatar { background: #30343a; border-radius: 12px; font-size: 25px; color: #c1c9bd; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDateEdit { background: #22252a; color: #f0f1f2;
    border: 1px solid #41454c; border-radius: 7px; padding: 8px; selection-background-color: #718367; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus { border-color: #849579; }
QLineEdit::placeholder { color: #a0a4a8; }
QComboBox QAbstractItemView { background: #25282d; color: #f0f1f2; selection-background-color: #41493f; border: 1px solid #41454c; }
QPushButton { background: #2a2d32; color: #f0f1f2; border: 1px solid #41454c; border-radius: 7px; padding: 8px 13px; }
QPushButton:hover { background: #34383d; border-color: #747c70; }
QPushButton#primary { background: #78886d; color: #ffffff; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #87977c; }
QPushButton#danger { color: #f0aaa0; }
QPushButton:disabled { color: #85898d; background: #25282c; border-color: #34383d; }
QTableWidget, QTreeWidget, QListWidget { background: #202328; color: #f0f1f2; border: 1px solid #383c42; border-radius: 8px;
    gridline-color: #34383d; selection-background-color: #3a4437; selection-color: #ffffff; }
QHeaderView::section { background: #292c31; padding: 9px; border: none; color: #d5d7d5; }
QTableWidget::item { padding: 8px; }
QGroupBox { color: #e9eae7; border: 1px solid #41454c; border-radius: 7px; margin-top: 9px; padding: 9px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QCheckBox, QRadioButton { color: #f0f1f2; spacing: 7px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: #1c1e22; width: 11px; margin: 2px; }
QScrollBar::handle:vertical { background: #454a50; border-radius: 5px; min-height: 24px; }
QScrollBar:horizontal { background: #1c1e22; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal { background: #454a50; border-radius: 5px; min-width: 24px; }
QStatusBar { color: #c2c5c5; background: #1c1e22; }
QToolTip { background: #2c3035; color: #ffffff; border: 1px solid #535961; padding: 5px; }
"""
