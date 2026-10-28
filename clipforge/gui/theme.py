"""Тёмная и светлая темы (QSS)."""
PALETTES = {
    "dark": dict(bg="#14161c", panel="#1d2029", panel2="#252a36", border="#313746", text="#e8eaf0", muted="#9aa3b5",
                 accent="#7c5cff", accent2="#9b82ff", ok="#3ecf8e", warn="#f5b942", err="#ff6b6b", input="#181b23"),
    "light": dict(bg="#f4f5f9", panel="#ffffff", panel2="#eceef5", border="#d5d9e6", text="#1c2030", muted="#667089",
                  accent="#6a48f0", accent2="#8467ff", ok="#14a36b", warn="#c98a0a", err="#d93f3f", input="#ffffff"),
}


def build_qss(theme: str = "dark") -> str:
    p = PALETTES.get(theme, PALETTES["dark"])
    return f"""
* {{ font-family: "Segoe UI", "Inter", "Roboto", sans-serif; font-size: 13px; }}
QMainWindow, QWidget#page, QDialog {{ background: {p['bg']}; color: {p['text']}; }}
QWidget {{ color: {p['text']}; }}
QLabel#h1 {{ font-size: 22px; font-weight: 700; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
QLabel#muted {{ color: {p['muted']}; }}
QLabel#stage {{ font-size: 17px; font-weight: 600; color: {p['accent2']}; }}
QFrame#card {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 12px; }}
QListWidget#nav {{ background: {p['panel']}; border: none; border-right: 1px solid {p['border']}; padding-top: 12px; outline: none; }}
QListWidget#nav::item {{ padding: 12px 18px; margin: 2px 8px; border-radius: 8px; color: {p['muted']}; }}
QListWidget#nav::item:selected {{ background: {p['accent']}; color: white; }}
QListWidget#nav::item:hover:!selected {{ background: {p['panel2']}; }}
QPushButton {{ background: {p['panel2']}; border: 1px solid {p['border']}; border-radius: 8px; padding: 8px 14px; }}
QPushButton:hover {{ border-color: {p['accent']}; }}
QPushButton:disabled {{ color: {p['muted']}; background: {p['panel']}; }}
QPushButton#primary {{ background: {p['accent']}; color: white; border: none; font-weight: 600; padding: 11px 22px; }}
QPushButton#primary:hover {{ background: {p['accent2']}; }}
QPushButton#primary:disabled {{ background: {p['border']}; color: {p['muted']}; }}
QPushButton#danger {{ background: transparent; border: 1px solid {p['err']}; color: {p['err']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget, QTreeWidget, QListWidget {{
  background: {p['input']}; border: 1px solid {p['border']}; border-radius: 8px; padding: 6px; selection-background-color: {p['accent']}; }}
QComboBox QAbstractItemView {{ background: {p['panel']}; selection-background-color: {p['accent']}; }}
QHeaderView::section {{ background: {p['panel2']}; border: none; padding: 6px; }}
QTableWidget, QTreeWidget {{ gridline-color: {p['border']}; }}
QProgressBar {{ background: {p['panel2']}; border: none; border-radius: 8px; height: 16px; text-align: center; color: {p['text']}; }}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 8px; }}
QSlider::groove:horizontal {{ height: 6px; background: {p['panel2']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; background: {p['accent2']}; }}
QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 10px; background: {p['panel']}; top: -1px; }}
QTabBar::tab {{ padding: 9px 16px; margin-right: 2px; border-top-left-radius: 8px; border-top-right-radius: 8px; color: {p['muted']}; }}
QTabBar::tab:selected {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; border-bottom: none; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QGroupBox {{ border: 1px solid {p['border']}; border-radius: 10px; margin-top: 14px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {p['muted']}; }}
QScrollArea {{ border: none; background: transparent; }}
QStatusBar {{ background: {p['panel']}; color: {p['muted']}; }}
QToolTip {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; }}
"""
