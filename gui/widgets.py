"""여러 화면이 같이 쓰는 작은 위젯과 아이콘 (3-06).

Material Symbols 글꼴 대신 QPainter로 단순한 선 아이콘을 그린다 (새 패키지·글꼴 파일 없이).
- LineIcon: 라벨 옆에 두는 아이콘 위젯
- line_icon(): 버튼에 넣는 QIcon
- PingDot: 상태 점. 켜져 있으면 바깥으로 퍼지며 사라지는 원을 그린다 (HTML animate-ping)
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from gui.style import qcolor

ICON_KINDS = ("clock", "flag", "info", "camera", "close", "play", "pause", "stop", "pip")
PING_MS = 1000  # 상태 점이 퍼지는 주기


def draw_icon(painter: QPainter, kind: str, side: float, color: str) -> None:
    """(0, 0)~(side, side) 상자 안에 아이콘을 그린다."""
    if kind not in ICON_KINDS:
        raise ValueError(f"알 수 없는 아이콘: {kind}")
    s = side
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(qcolor(color), max(s / 11, 1.2))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    m = s * 0.12
    c = QPointF(s / 2, s / 2)
    if kind == "clock":
        painter.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
        painter.drawLine(c, QPointF(s / 2, s * 0.3))
        painter.drawLine(c, QPointF(s * 0.66, s * 0.6))
    elif kind == "flag":
        painter.drawLine(QPointF(s * 0.25, s * 0.12), QPointF(s * 0.25, s * 0.9))
        path = QPainterPath(QPointF(s * 0.25, s * 0.16))
        path.lineTo(s * 0.8, s * 0.16)
        path.lineTo(s * 0.68, s * 0.34)
        path.lineTo(s * 0.8, s * 0.52)
        path.lineTo(s * 0.25, s * 0.52)
        painter.drawPath(path)
    elif kind == "info":
        painter.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
        painter.drawLine(QPointF(s / 2, s * 0.46), QPointF(s / 2, s * 0.7))
        painter.setBrush(qcolor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(s / 2, s * 0.32), s * 0.06, s * 0.06)
    elif kind == "camera":
        painter.drawRoundedRect(QRectF(s * 0.08, s * 0.28, s * 0.58, s * 0.44), s * 0.08, s * 0.08)
        path = QPainterPath(QPointF(s * 0.66, s * 0.44))
        path.lineTo(s * 0.92, s * 0.3)
        path.lineTo(s * 0.92, s * 0.7)
        path.lineTo(s * 0.66, s * 0.56)
        painter.drawPath(path)
    elif kind == "close":
        k = s * 0.25
        painter.drawLine(QPointF(k, k), QPointF(s - k, s - k))
        painter.drawLine(QPointF(s - k, k), QPointF(k, s - k))
    elif kind == "play":
        path = QPainterPath(QPointF(s * 0.32, s * 0.2))
        path.lineTo(s * 0.8, s * 0.5)
        path.lineTo(s * 0.32, s * 0.8)
        path.closeSubpath()
        painter.setBrush(qcolor(color))
        painter.drawPath(path)
    elif kind == "pause":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(color))
        bar = s * 0.16
        painter.drawRoundedRect(QRectF(s * 0.28, s * 0.2, bar, s * 0.6), bar * 0.3, bar * 0.3)
        painter.drawRoundedRect(QRectF(s * 0.72 - bar, s * 0.2, bar, s * 0.6), bar * 0.3, bar * 0.3)
    elif kind == "stop":  # stop_circle
        painter.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(color))
        painter.drawRoundedRect(QRectF(s * 0.36, s * 0.36, s * 0.28, s * 0.28), s * 0.03, s * 0.03)
    else:  # pip (picture_in_picture_alt)
        painter.drawRoundedRect(QRectF(s * 0.08, s * 0.18, s * 0.84, s * 0.64), s * 0.08, s * 0.08)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(color))
        painter.drawRoundedRect(QRectF(s * 0.5, s * 0.48, s * 0.3, s * 0.22), s * 0.03, s * 0.03)
    painter.restore()


def line_icon(kind: str, color: str, side: int = 16) -> QIcon:
    """버튼용 QIcon. 고해상도 화면에서도 흐리지 않게 2배로 그린다."""
    if kind not in ICON_KINDS:
        raise ValueError(f"알 수 없는 아이콘: {kind}")
    scale = 2
    pixmap = QPixmap(side * scale, side * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:  # 그리다 예외가 나도 painter를 닫는다 (열린 채 정리되면 프로세스가 죽는다)
        painter.scale(scale, scale)
        draw_icon(painter, kind, side, color)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


class LineIcon(QWidget):
    """라벨 옆에 두는 작은 선 아이콘 (ICON_KINDS)."""

    def __init__(self, kind: str, color: str, side: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if kind not in ICON_KINDS:
            raise ValueError(f"알 수 없는 아이콘: {kind}")
        self._kind = kind
        self._color = color
        self.setFixedSize(side, side)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        draw_icon(painter, self._kind, self.width(), self._color)


class PingDot(QWidget):
    """상태 점. 켜져 있으면 바깥으로 퍼지며 사라지는 원을 PING_MS 주기로 그린다."""

    SIDE = 16
    DOT = 8

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIDE, self.SIDE)
        self._color = color
        self._phase = 0.0
        self._pinging = True
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(PING_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._on_phase)

    @property
    def is_pinging(self) -> bool:
        return self._pinging

    def set_state(self, color: str, pinging: bool) -> None:
        self._color = color
        self._pinging = pinging
        if pinging and self.isVisible():
            self._animation.start()
        elif not pinging:
            self._animation.stop()
            self._phase = 0.0
        self.update()

    def _on_phase(self, value: float) -> None:
        self._phase = value
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        if self._pinging:
            self._animation.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._animation.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        center = QPointF(self.width() / 2, self.height() / 2)
        r = self.DOT / 2
        if self._pinging:
            painter.setBrush(qcolor(self._color, 0.75 * (1 - self._phase)))
            painter.drawEllipse(center, r * (1 + self._phase), r * (1 + self._phase))
        painter.setBrush(qcolor(self._color))
        painter.drawEllipse(center, r, r)
