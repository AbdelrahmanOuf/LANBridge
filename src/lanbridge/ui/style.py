from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLineEdit


def icon(name: str) -> QIcon:
    return QIcon(str(Path(__file__).parents[1] / 'resources' / f'{name}.svg'))


def ltr(widget: QLineEdit) -> QLineEdit:
    widget.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    widget.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return widget


def stylesheet(theme: str = 'dark') -> str:
    dark = theme == 'dark'
    bg, card, raised = ('#0B1220', '#111D31', '#17263D') if dark else ('#F1F5FB', '#FFFFFF', '#EAF0F9')
    ink, muted, border = ('#EAF2FF', '#98ABC8', '#243650') if dark else ('#112440', '#5D718E', '#D7E1EF')
    return f'''
    QWidget {{ color: {ink}; font-family: "Segoe UI", "Noto Sans Arabic", sans-serif; font-size: 10pt; }}
    QMainWindow, QWidget#root {{ background: {bg}; }}
    QFrame#sidebar {{ background: {card}; border: none; border-right: 1px solid {border}; }}
    QLabel#brand {{ font-size: 15pt; font-weight: 800; color: {ink}; }}
    QLabel#brandSub {{ color: #06B6D4; font-size: 8pt; letter-spacing: 2px; }}
    QLabel#pageTitle {{ font-size: 23pt; font-weight: 700; }}
    QLabel#muted, QLabel#hint {{ color: {muted}; }}
    QLabel#eyebrow {{ color: #06B6D4; font-size: 9pt; font-weight: 700; }}
    QLabel#heroTitle {{ font-size: 30pt; font-weight: 800; color: #F7FBFF; }}
    QLabel#heroText {{ color: #BFD8F8; font-size: 11pt; }}
    QFrame#hero {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #132F61,stop:0.58 #164281,stop:1 #087D9D); border: 1px solid #286C9E; border-radius: 22px; }}
    QFrame#card {{ background: {card}; border: 1px solid {border}; border-radius: 16px; }}
    QLabel#metric {{ font-size: 21pt; font-weight: 700; color: {ink}; }}
    QLabel#cardTitle {{ font-size: 13pt; font-weight: 700; }}
    QPushButton {{ background: {raised}; border: 1px solid {border}; border-radius: 9px; padding: 10px 16px; font-weight: 600; }}
    QPushButton:hover {{ background: #214675; border-color: #3B82F6; color: white; }}
    QPushButton:pressed {{ background: #18345B; }}
    QPushButton:disabled {{ color: {muted}; background: {card}; border-color: {border}; }}
    QPushButton#primary {{ background: #2563EB; border: 1px solid #3B82F6; color: white; }}
    QPushButton#primary:hover {{ background: #3475F8; }}
    QPushButton#danger {{ color: #FB918D; }}
    QPushButton#nav {{ text-align: left; padding: 13px 14px; border: none; background: transparent; color: {muted}; border-radius: 10px; }}
    QPushButton#nav:checked {{ background: #18345B; color: #7EDDF0; border-left: 3px solid #06B6D4; }}
    QPushButton#nav:hover {{ background: {raised}; color: {ink}; }}
    QLineEdit, QComboBox, QSpinBox, QListWidget, QPlainTextEdit {{ background: {card}; border: 1px solid {border}; border-radius: 8px; padding: 9px; selection-background-color: #2563EB; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: #06B6D4; }}
    QComboBox QAbstractItemView {{ background: {card}; color: {ink}; selection-background-color: #2563EB; }}
    QTableWidget {{ background: {card}; alternate-background-color: {raised}; border: 1px solid {border}; border-radius: 12px; gridline-color: {border}; selection-background-color: #214675; }}
    QHeaderView::section {{ background: {raised}; color: {muted}; padding: 10px; border: none; border-bottom: 1px solid {border}; font-weight: 600; }}
    QTableWidget::item {{ padding: 7px; border: none; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: {bg}; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 28px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QProgressBar {{ background: {raised}; border: none; border-radius: 6px; height: 12px; text-align: center; }}
    QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #2563EB,stop:1 #06B6D4); border-radius: 6px; }}
    QCheckBox {{ spacing: 9px; padding: 5px 0; }}
    QCheckBox::indicator {{ width: 17px; height: 17px; border: 1px solid {border}; border-radius: 4px; background: {card}; }}
    QCheckBox::indicator:checked {{ background: #2563EB; border-color: #06B6D4; }}
    QDialog {{ background: {bg}; }}
    QToolTip {{ background: {card}; color: {ink}; border: 1px solid #2563EB; padding: 8px; }}
    '''
