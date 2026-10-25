from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


def label(text: str, name: str = '', wrap: bool = True) -> QLabel:
    result = QLabel(text)
    result.setObjectName(name)
    result.setWordWrap(wrap)
    result.setTextFormat(Qt.TextFormat.PlainText)
    return result


def card(title: str = '') -> tuple[QFrame, QVBoxLayout]:
    result = QFrame()
    result.setObjectName('card')
    layout = QVBoxLayout(result)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    if title:
        layout.addWidget(label(title, 'cardTitle'))
    return result, layout


class Metric(QFrame):
    def __init__(self, title: str, value: str = '—'):
        super().__init__()
        self.setObjectName('card')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        self.title = label(title, 'muted')
        self.value = label(value, 'metric')
        self.value.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        self.setMinimumWidth(140)


class ActivityChart(QWidget):
    def __init__(self):
        super().__init__()
        self.values: deque[float] = deque(maxlen=90)
        self.setMinimumHeight(140)

    def add(self, rate: float) -> None:
        self.values.append(max(0.0, rate / 1024**2))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.rect().adjusted(6, 20, -8, -22)
        painter.setPen(QPen(QColor('#29415E'), 1))
        for fraction in (0, .5, 1):
            y = area.top() + area.height() * fraction
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.setPen(QColor('#8FA9C9'))
        ceiling = max(1.0, max(self.values, default=1))
        painter.drawText(8, 15, f'{ceiling:.1f} MiB/s  •  estimated completion throughput')
        if len(self.values) < 2:
            return
        points = [QPointF(area.left() + i * area.width() / max(1, self.values.maxlen - 1),
                          area.bottom() - n / ceiling * area.height()) for i, n in enumerate(self.values)]
        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        fill = QPainterPath(path)
        fill.lineTo(points[-1].x(), area.bottom())
        fill.lineTo(points[0].x(), area.bottom())
        fill.closeSubpath()
        gradient = QLinearGradient(0, area.top(), 0, area.bottom())
        gradient.setColorAt(0, QColor(6, 182, 212, 95))
        gradient.setColorAt(1, QColor(6, 182, 212, 0))
        painter.fillPath(fill, gradient)
        painter.setPen(QPen(QColor('#06B6D4'), 2))
        painter.drawPath(path)


def row(*widgets: QWidget) -> QWidget:
    result = QWidget()
    layout = QHBoxLayout(result)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch()
    return result


def size_text(value: int | float) -> str:
    number = float(value)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if number < 1024 or unit == 'TiB':
            return f'{number:,.1f} {unit}'
        number /= 1024
    return '—'


def time_text(seconds: float | None) -> str:
    if seconds is None:
        return '—'
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'
