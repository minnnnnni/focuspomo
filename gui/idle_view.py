"""대기(메인) 화면: 앱을 켜면 처음 보는 화면 (3-06 UI 디자인).

디자인은 사용자가 준 HTML(Launch Screen)을 옮겼다. 타이머 화면과 같은 다크 팔레트(gui.timer_view.COLORS)를 쓴다.
- 가운데: 모래시계 아이콘(은은하게 깜빡이는 바깥 링), 앱 이름, 설명, 웹캠 상태 알약, "집중 세션 시작하기" 버튼
- 버튼 아래: "N초간 시선 캘리브레이션 후 시작됩니다" (N = CALIBRATION_SEC), 세션 시작 거부·오류 안내 문구
- 아래 막대: 집중/휴식 시간, 이번이 긴 휴식 주기의 몇 번째 세션인지

HTML의 창 제목 표시줄(최소화·최대화·닫기)은 OS 기본 제목 표시줄을 그대로 쓰고,
아래 막대의 알림·카메라 조정·환경설정 버튼은 기능이 아직 없어 넣지 않았다.
아이콘은 Material Symbols 글꼴 대신 QPainter로 단순하게 그린다 (새 패키지·글꼴 파일 없이).

    python -m gui.idle_view     # 대기 화면만 띄워 보기
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.aggregation import CALIBRATION_SEC
from core.pomodoro import LONG_BREAK_EVERY, SHORT_BREAK_SEC
from gui.timer_view import COLORS, MONO, SANS, SESSION_SEC

APP_NAME = "FocusPomo"
TAGLINE = "산만함 · 졸음 인식 기반 집중 관리 타이머"
INVITE_TEXT = "준비가 되셨나요? 오늘의 몰입 세션을 시작해보세요."
START_BUTTON_TEXT = "집중 세션 시작하기"
CALIBRATION_HINT = f"{CALIBRATION_SEC}초간 시선 캘리브레이션 후 시작됩니다"
CAMERA_STATUS_TEXT: dict[bool | None, str] = {
    None: "웹캠 확인 전",
    True: "웹캠 준비 완료",
    False: "웹캠을 찾을 수 없음",
}

ON_PRIMARY_CONTAINER = "#0d0096"  # 시작 버튼 글자색 (HTML on-primary-container)
SURFACE_LOWEST = "#060e20"  # 아래 막대 바탕 (HTML surface-container-lowest)

HERO_SIDE = 112  # 아이콘 원(96) + 바깥 링 여백
HERO_CIRCLE = 96
PULSE_MS = 2000  # 바깥 링이 흐려졌다 돌아오는 주기 (HTML animate-pulse)
PING_MS = 1000  # 상태 점이 퍼지는 주기 (HTML animate-ping)
GRID_STEP = 24  # 바탕 점 격자 간격

STYLE = f"""
#appName {{ color: {COLORS["on_surface"]}; font-family: {SANS}; font-size: 24px; font-weight: 700; }}
#tagline {{ color: {COLORS["on_surface_variant"]}; font-family: {SANS}; font-size: 12px; }}
#invite {{ color: {COLORS["outline"]}; font-family: {SANS}; font-size: 12px; }}
#cameraPill {{
    background: rgba(34, 42, 61, 0.6);
    border: 1px solid rgba(70, 69, 84, 0.3);
    border-radius: 12px;
}}
#cameraText {{ color: {COLORS["on_surface"]}; font-family: {SANS}; font-size: 12px; font-weight: 500; }}
#startButton {{
    background: {COLORS["primary_container"]};
    border: none;
    border-radius: 8px;
    color: {ON_PRIMARY_CONTAINER};
    font-family: {SANS};
    font-size: 15px;
    font-weight: 600;
    padding: 13px 20px;
}}
#startButton:hover {{ background: {COLORS["primary"]}; }}
#startButton:pressed {{ background: #9a9cff; padding-top: 14px; padding-bottom: 12px; }}
#hintText {{ color: {COLORS["outline"]}; font-family: {SANS}; font-size: 12px; }}
#noticeLabel {{
    background: rgba(147, 0, 10, 0.18);
    border: 1px solid rgba(255, 180, 171, 0.3);
    border-radius: 6px;
    color: {COLORS["error"]};
    font-family: {SANS};
    font-size: 12px;
    padding: 8px 12px;
}}
#footer {{
    background: rgba(6, 14, 32, 0.6);
    border-top: 1px solid rgba(70, 69, 84, 0.2);
}}
#footerText {{ color: {COLORS["on_surface_variant"]}; font-family: {SANS}; font-size: 12px; font-weight: 600; }}
#footerMuted {{ color: {COLORS["outline"]}; font-family: {SANS}; font-size: 12px; font-weight: 600; }}
#footerDot {{ color: {COLORS["outline_variant"]}; font-size: 12px; }}
"""


def _color(name: str, alpha: float = 1.0) -> QColor:
    color = QColor(COLORS.get(name, name))
    color.setAlphaF(alpha)
    return color


def cycle_position(completed_sessions: int) -> int:
    """다음 세션이 긴 휴식 주기(LONG_BREAK_EVERY개)의 몇 번째인지 (1부터)."""
    return max(completed_sessions, 0) % LONG_BREAK_EVERY + 1


class LineIcon(QWidget):
    """Material Symbols 대신 쓰는 작은 선 아이콘 (clock / flag / info / camera)."""

    KINDS = ("clock", "flag", "info", "camera")

    def __init__(self, kind: str, color: str, side: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if kind not in self.KINDS:
            raise ValueError(f"알 수 없는 아이콘: {kind}")
        self._kind = kind
        self._color = color
        self.setFixedSize(side, side)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        s = self.width()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(_color(self._color), max(s / 11, 1.2))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        m = s * 0.12
        c = QPointF(s / 2, s / 2)
        if self._kind == "clock":
            painter.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
            painter.drawLine(c, QPointF(s / 2, s * 0.3))
            painter.drawLine(c, QPointF(s * 0.66, s * 0.6))
        elif self._kind == "flag":
            painter.drawLine(QPointF(s * 0.25, s * 0.12), QPointF(s * 0.25, s * 0.9))
            path = QPainterPath(QPointF(s * 0.25, s * 0.16))
            path.lineTo(s * 0.8, s * 0.16)
            path.lineTo(s * 0.68, s * 0.34)
            path.lineTo(s * 0.8, s * 0.52)
            path.lineTo(s * 0.25, s * 0.52)
            painter.drawPath(path)
        elif self._kind == "info":
            painter.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
            painter.drawLine(QPointF(s / 2, s * 0.46), QPointF(s / 2, s * 0.7))
            painter.setBrush(_color(self._color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(s / 2, s * 0.32), s * 0.06, s * 0.06)
        else:  # camera
            painter.drawRoundedRect(QRectF(s * 0.08, s * 0.28, s * 0.58, s * 0.44), s * 0.08, s * 0.08)
            path = QPainterPath(QPointF(s * 0.66, s * 0.44))
            path.lineTo(s * 0.92, s * 0.3)
            path.lineTo(s * 0.92, s * 0.7)
            path.lineTo(s * 0.66, s * 0.56)
            painter.drawPath(path)


class HeroIcon(QWidget):
    """가운데 원형 모래시계 아이콘. 바깥 링은 PULSE_MS 주기로 흐려졌다 돌아온다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(HERO_SIDE, HERO_SIDE)
        self._pulse = 1.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(PULSE_MS)
        self._animation.setStartValue(1.0)
        self._animation.setKeyValueAt(0.5, 0.45)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._on_pulse)

    def _on_pulse(self, value: float) -> None:
        self._pulse = value
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._animation.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._animation.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        r = HERO_CIRCLE / 2

        # 은은한 보랏빛 빛무리 (HTML hud-glow)
        glow = QRadialGradient(center, r + 8)
        glow.setColorAt(0.7, _color("primary_container", 0.16))
        glow.setColorAt(1.0, _color("primary_container", 0.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, r + 8, r + 8)

        # 아이콘 원
        painter.setBrush(_color("primary", 0.10))
        painter.setPen(QPen(_color("primary", 0.25), 1))
        painter.drawEllipse(center, r, r)

        # 깜빡이는 바깥 링
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_color("secondary", 0.2 * self._pulse), 1))
        painter.drawEllipse(center, r + 7.5, r + 7.5)

        self._draw_hourglass(painter, center, 36)

    @staticmethod
    def _draw_hourglass(painter: QPainter, c: QPointF, h: float) -> None:
        w = h * 0.62
        top, bottom = c.y() - h / 2, c.y() + h / 2
        left, right = c.x() - w / 2, c.x() + w / 2
        neck = w * 0.07
        pen = QPen(_color("primary"), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(left - 3, top), QPointF(right + 3, top))
        painter.drawLine(QPointF(left - 3, bottom), QPointF(right + 3, bottom))

        body = QPainterPath(QPointF(left, top))
        body.lineTo(left, top + h * 0.16)
        body.lineTo(c.x() - neck, c.y())
        body.lineTo(left, bottom - h * 0.16)
        body.lineTo(left, bottom)
        body.moveTo(right, top)
        body.lineTo(right, top + h * 0.16)
        body.lineTo(c.x() + neck, c.y())
        body.lineTo(right, bottom - h * 0.16)
        body.lineTo(right, bottom)
        painter.drawPath(body)

        # 위쪽에 남은 모래 (hourglass_top)
        sand = QPainterPath(QPointF(left + 3, top + h * 0.18))
        sand.lineTo(right - 3, top + h * 0.18)
        sand.lineTo(c.x(), c.y() - 2)
        sand.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_color("primary"))
        painter.drawPath(sand)


class PingDot(QWidget):
    """상태 점. 켜져 있으면 바깥으로 퍼지며 사라지는 원을 PING_MS 주기로 그린다 (HTML animate-ping)."""

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
            painter.setBrush(_color(self._color, 0.75 * (1 - self._phase)))
            painter.drawEllipse(center, r * (1 + self._phase), r * (1 + self._phase))
        painter.setBrush(_color(self._color))
        painter.drawEllipse(center, r, r)


def _play_icon(color: str, side: int = 20) -> QIcon:
    """채워진 재생(▶) 아이콘. 고해상도 화면에서도 흐리지 않게 2배로 그린다."""
    scale = 2
    pixmap = QPixmap(side * scale, side * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    path = QPainterPath(QPointF(side * 0.32, side * 0.2))
    path.lineTo(side * 0.8, side * 0.5)
    path.lineTo(side * 0.32, side * 0.8)
    path.closeSubpath()
    painter.setPen(QPen(_color(color), side * 0.08, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(_color(color))
    painter.drawPath(path)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


class IdleView(QWidget):
    """대기 화면: 세션 시작 버튼과, 세션 시작이 거부됐을 때의 안내 문구."""

    start_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("idleView")
        self.setStyleSheet(STYLE)

        # 가운데: 아이콘 · 이름 · 설명
        hero = HeroIcon()

        app_name = QLabel(APP_NAME)
        app_name.setObjectName("appName")
        app_name.setAlignment(Qt.AlignmentFlag.AlignCenter)

        tagline = QLabel(TAGLINE)
        tagline.setObjectName("tagline")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        invite = QLabel(INVITE_TEXT)
        invite.setObjectName("invite")
        invite.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 웹캠 상태 알약
        self.camera_pill = pill = QFrame()
        pill.setObjectName("cameraPill")
        self.camera_dot = PingDot(COLORS["outline"])
        self.camera_icon = LineIcon("camera", COLORS["outline"])
        self.camera_label = QLabel()
        self.camera_label.setObjectName("cameraText")
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(10, 3, 12, 3)
        pill_layout.setSpacing(6)
        pill_layout.addWidget(self.camera_dot, alignment=Qt.AlignmentFlag.AlignVCenter)
        pill_layout.addWidget(self.camera_icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        pill_layout.addWidget(self.camera_label)
        self.camera_ready: bool | None = None
        self.set_camera_ready(None)

        # 시작 버튼 · 안내
        self.start_button = QPushButton(START_BUTTON_TEXT)
        self.start_button.setObjectName("startButton")
        self.start_button.setIcon(_play_icon(ON_PRIMARY_CONTAINER))
        self.start_button.setIconSize(QSize(20, 20))
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setFixedWidth(320)
        self.start_button.clicked.connect(self.start_requested)

        hint = QLabel(CALIBRATION_HINT)
        hint.setObjectName("hintText")
        hint_row = QHBoxLayout()
        hint_row.setSpacing(6)
        hint_row.addStretch()
        hint_row.addWidget(LineIcon("info", COLORS["outline"], 13), alignment=Qt.AlignmentFlag.AlignVCenter)
        hint_row.addWidget(hint)
        hint_row.addStretch()

        self.notice_label = QLabel()
        self.notice_label.setObjectName("noticeLabel")
        self.notice_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice_label.hide()

        center = QVBoxLayout()
        center.setContentsMargins(24, 16, 24, 16)
        center.setSpacing(0)
        center.addStretch()
        center.addWidget(hero, alignment=Qt.AlignmentFlag.AlignCenter)
        center.addSpacing(14)
        center.addWidget(app_name)
        center.addSpacing(6)
        center.addWidget(tagline)
        center.addSpacing(2)
        center.addWidget(invite)
        center.addSpacing(20)
        center.addWidget(pill, alignment=Qt.AlignmentFlag.AlignCenter)
        center.addSpacing(24)
        center.addWidget(self.start_button, alignment=Qt.AlignmentFlag.AlignCenter)
        center.addSpacing(10)
        center.addLayout(hint_row)
        center.addSpacing(12)
        center.addWidget(self.notice_label, alignment=Qt.AlignmentFlag.AlignCenter)
        center.addStretch()

        # 아래 막대: 집중/휴식 시간 · 주기 안 세션 번호
        footer = QFrame()
        footer.setObjectName("footer")
        durations = QLabel(f"{SESSION_SEC // 60}분 / {SHORT_BREAK_SEC // 60}분")
        durations.setObjectName("footerText")
        durations.setToolTip("집중 / 짧은 휴식")
        dot = QLabel("•")
        dot.setObjectName("footerDot")
        self.cycle_label = QLabel()
        self.cycle_label.setObjectName("footerMuted")
        self.cycle_label.setToolTip(f"{LONG_BREAK_EVERY}세션을 끝까지 마치면 긴 휴식")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 9, 16, 9)
        footer_layout.setSpacing(5)
        footer_layout.addWidget(LineIcon("clock", COLORS["outline"]))
        footer_layout.addWidget(durations)
        footer_layout.addSpacing(4)
        footer_layout.addWidget(dot)
        footer_layout.addSpacing(4)
        footer_layout.addWidget(LineIcon("flag", COLORS["outline"]))
        footer_layout.addWidget(self.cycle_label)
        footer_layout.addStretch()
        self.set_completed_sessions(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(center, 1)
        layout.addWidget(footer)

    def show_notice(self, text: str) -> None:
        self.notice_label.setText(text)
        self.notice_label.show()

    def clear_notice(self) -> None:
        self.notice_label.clear()
        self.notice_label.hide()

    def set_camera_ready(self, ready: bool | None) -> None:
        """웹캠 상태 표시. None = 아직 모름(회색), True = 준비됨(초록, 퍼지는 점), False = 못 찾음(빨강)."""
        self.camera_ready = ready
        color = {None: COLORS["outline"], True: COLORS["secondary"], False: COLORS["error"]}[ready]
        self.camera_dot.set_state(color, pinging=ready is True)
        self.camera_icon.set_color(color)
        self.camera_label.setText(CAMERA_STATUS_TEXT[ready])

    def set_completed_sessions(self, completed_sessions: int) -> None:
        """끝까지 마친 세션 수로 "세션 n/LONG_BREAK_EVERY"(다음 세션 번호)를 갱신한다."""
        self.cycle_label.setText(f"세션 {cycle_position(completed_sessions)}/{LONG_BREAK_EVERY}")

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        # 바탕: 짙은 남색 + 위(보라)·아래(초록) 은은한 빛 + 점 격자 (HTML 배경 blur·grid)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), _color("background"))
        painter.setPen(Qt.PenStyle.NoPen)

        top = QRadialGradient(QPointF(w / 2, -h * 0.05), max(w * 0.55, 1))
        top.setColorAt(0.0, _color("primary", 0.12))
        top.setColorAt(1.0, _color("primary", 0.0))
        painter.setBrush(top)
        painter.drawRect(self.rect())

        bottom = QRadialGradient(QPointF(w / 2, h * 0.88), max(w * 0.45, 1))
        bottom.setColorAt(0.0, _color("secondary", 0.06))
        bottom.setColorAt(1.0, _color("secondary", 0.0))
        painter.setBrush(bottom)
        painter.drawRect(self.rect())

        painter.setBrush(_color("outline_variant", 0.15 * 0.6))
        for y in range(GRID_STEP // 2, h, GRID_STEP):
            for x in range(GRID_STEP // 2, w, GRID_STEP):
                painter.drawEllipse(QPointF(x, y), 0.9, 0.9)
        painter.end()
        super().paintEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    view = IdleView()
    view.set_camera_ready(True)
    view.resize(580, 580)
    view.setWindowTitle(APP_NAME)
    view.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
