"""Общие виджеты и привязка настроек к элементам управления."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)


def label(text: str, kind: str = "") -> QLabel:
    l = QLabel(text)
    if kind:
        l.setObjectName(kind)
    l.setWordWrap(True)
    return l


class Card(QFrame):
    def __init__(self, title: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(10)
        if title:
            self.lay.addWidget(label(title, "h2"))


class Binder:
    """Создаёт элементы управления, сразу привязанные к полям dataclass-настроек."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self.on_change = on_change
        self._refresh: list[Callable[[], None]] = []

    def refresh(self) -> None:
        for r in self._refresh:
            r()

    def _set(self, obj: Any, attr: str, val: Any) -> None:
        setattr(obj, attr, val)
        self.on_change()

    def check(self, text: str, obj: Any, attr: str, tip: str = "") -> QCheckBox:
        w = QCheckBox(text)
        w.setToolTip(tip)
        w.setChecked(bool(getattr(obj, attr)))
        w.toggled.connect(lambda v: self._set(obj, attr, bool(v)))
        self._refresh.append(lambda: w.setChecked(bool(getattr(obj, attr))))
        return w

    def combo(self, obj: Any, attr: str, items: list[tuple[Any, str]], tip: str = "") -> QComboBox:
        w = QComboBox()
        w.setToolTip(tip)
        for val, text in items:
            w.addItem(text, val)

        def load() -> None:
            i = w.findData(getattr(obj, attr))
            w.blockSignals(True)
            w.setCurrentIndex(max(0, i))
            w.blockSignals(False)
        load()
        w.currentIndexChanged.connect(lambda _: self._set(obj, attr, w.currentData()))
        self._refresh.append(load)
        return w

    def spin(self, obj: Any, attr: str, lo: float, hi: float, step: float = 1, decimals: int = 0, suffix: str = "") -> QWidget:
        w: QSpinBox | QDoubleSpinBox = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            w.setDecimals(decimals)
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setSuffix(suffix)
        conv = (lambda v: v) if decimals else (lambda v: int(round(v)))   # QSpinBox не принимает float
        w.setValue(conv(getattr(obj, attr)))
        w.valueChanged.connect(lambda v: self._set(obj, attr, v))
        self._refresh.append(lambda: w.setValue(conv(getattr(obj, attr))))
        return w

    def slider(self, obj: Any, attr: str, lo: int, hi: int, suffix: str = "%") -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        val = QLabel()
        val.setMinimumWidth(48)

        def load() -> None:
            s.blockSignals(True)
            s.setValue(int(getattr(obj, attr)))
            s.blockSignals(False)
            val.setText(f"{s.value()}{suffix}")
        load()

        def changed(v: int) -> None:
            val.setText(f"{v}{suffix}")
            self._set(obj, attr, v)
        s.valueChanged.connect(changed)
        lay.addWidget(s, 1)
        lay.addWidget(val)
        self._refresh.append(load)
        box.slider = s  # type: ignore[attr-defined]
        return box

    def text(self, obj: Any, attr: str, placeholder: str = "", password: bool = False) -> QLineEdit:
        w = QLineEdit(str(getattr(obj, attr)))
        w.setPlaceholderText(placeholder)
        if password:
            w.setEchoMode(QLineEdit.Password)
        w.textChanged.connect(lambda v: self._set(obj, attr, v))
        self._refresh.append(lambda: w.setText(str(getattr(obj, attr))))
        return w

    def color(self, obj: Any, attr: str) -> QPushButton:
        b = QPushButton()

        def paint() -> None:
            c = getattr(obj, attr)
            b.setText(c)
            b.setStyleSheet(f"background:{c}; color:{'#000' if QColor(c).lightness() > 128 else '#fff'};")
        paint()

        def pick() -> None:
            c = QColorDialog.getColor(QColor(getattr(obj, attr)), b)
            if c.isValid():
                self._set(obj, attr, c.name().upper())
                paint()
        b.clicked.connect(pick)
        self._refresh.append(paint)
        return b

    def file(self, obj: Any, attr: str, caption: str, filt: str, folder: bool = False) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        e = self.text(obj, attr)
        b = QPushButton("Обзор…")

        def pick() -> None:
            p = QFileDialog.getExistingDirectory(box, caption) if folder else QFileDialog.getOpenFileName(box, caption, "", filt)[0]
            if p:
                e.setText(p)
        b.clicked.connect(pick)
        lay.addWidget(e, 1)
        lay.addWidget(b)
        if not folder:
            c = QPushButton("✕")
            c.setFixedWidth(34)
            c.clicked.connect(lambda: e.setText(""))
            lay.addWidget(c)
        return box


def form(parent_layout, rows: list[tuple[str, QWidget]]) -> QFormLayout:
    f = QFormLayout()
    f.setLabelAlignment(Qt.AlignLeft)
    f.setHorizontalSpacing(18)
    f.setVerticalSpacing(10)
    for text, w in rows:
        if text:
            f.addRow(text, w)
        else:
            f.addRow(w)
    parent_layout.addLayout(f)
    return f


class RangeBar(QWidget):
    """Шкала времени: окно вокруг клипа, выделенный интервал с перетаскиваемыми границами."""
    changed = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(54)
        self.lo = self.hi = 0.0
        self.a = self.b = 0.0
        self.pos: float | None = None
        self._drag: str | None = None

    def set_range(self, lo: float, hi: float, a: float, b: float) -> None:
        self.lo, self.hi, self.a, self.b = lo, max(hi, lo + 1), a, b
        self.update()

    def set_pos(self, t: float | None) -> None:
        self.pos = t
        self.update()

    def _x(self, t: float) -> float:
        return 10 + (self.width() - 20) * (t - self.lo) / (self.hi - self.lo)

    def _t(self, x: float) -> float:
        return self.lo + (x - 10) / max(1, self.width() - 20) * (self.hi - self.lo)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        p.setBrush(QColor(120, 125, 150, 70)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(10, h / 2 - 6, self.width() - 20, 12), 6, 6)
        xa, xb = self._x(self.a), self._x(self.b)
        p.setBrush(QColor(124, 92, 255, 200))
        p.drawRoundedRect(QRectF(xa, h / 2 - 8, max(2, xb - xa), 16), 6, 6)
        p.setBrush(QColor(255, 255, 255))
        for x in (xa, xb):
            p.drawRoundedRect(QRectF(x - 4, h / 2 - 16, 8, 32), 3, 3)
        if self.pos is not None:
            p.setPen(QPen(QColor(62, 207, 142), 2))
            x = self._x(self.pos)
            p.drawLine(int(x), 4, int(x), h - 4)

    def mousePressEvent(self, e) -> None:
        x = e.position().x()
        self._drag = "a" if abs(x - self._x(self.a)) <= abs(x - self._x(self.b)) else "b"
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if not self._drag:
            return
        t = min(max(self._t(e.position().x()), self.lo), self.hi)
        if self._drag == "a":
            self.a = min(t, self.b - 1.0)
        else:
            self.b = max(t, self.a + 1.0)
        self.update()
        self.changed.emit(self.a, self.b)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None
