"""캘리브레이션 화면 (개발계획서 2.0, 디자인 3-06).

"화면 중앙을 N초간 응시해 주세요" 안내와 N초 카운트다운을 보여준다 (N = CALIBRATION_SEC).
EAR 측정은 CV 스레드(김시언)가 하고, 이 화면은 안내와 시간 흐름만 담당한다.

- start(): 기본 안내로 카운트다운을 처음부터 시작한다. 화면에 들어올 때마다 MainWindow가 부른다.
- retry(): 재시도 안내로 카운트다운을 다시 시작한다 (첫 시도 실패 시 MainWindow가 부른다).
- 0초가 되면 countdown_finished를 보낸다.
- "캘리브레이션 취소"를 누르면 카운트다운을 멈추고 cancel_requested를 보낸다.

결과 판정(성공/재시도/폴백/거부)은 core.calibration.decide()로 MainWindow가 한다.

디자인은 사용자가 준 HTML(Calibration HUD)을 옮겼다. 색·글꼴은 gui.style에서 가져온다.
- 가운데 다이얼: 천천히 도는 점선 링, 남은 시간 비율만큼 줄어드는 진행 링, 조준선, 모서리 브래킷, 남은 초
- 아래: 안내 문구, 취소 버튼, 창 맨 아래 남은 시간 막대
- 재시도 때는 위쪽에 주황 안내 상자를 띄운다 (HTML에는 없음, 빈 "Top Status Cluster" 자리)
HTML의 창 제목 표시줄은 OS 기본을 쓴다 (대기 화면과 같음).

    python -m gui.calibration_view            # 캘리브레이션 화면만 띄워 보기 (끝나면 다시 시작)
    python -m gui.calibration_view --retry    # 재시도 안내
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.aggregation import CALIBRATION_SEC
from gui.style import APP_STYLE, COLORS, MONO, SANS, font, load_fonts, qcolor, set_style_props

TICK_MS = 1000
START_MESSAGE = f"화면 중앙을 {CALIBRATION_SEC}초간 응시해 주세요"
RETRY_MESSAGE = f"화면 중앙을 {CALIBRATION_SEC}초간 한 번 더 응시해 주세요"
DETAIL_TEXT = "개인 눈 크기 기준값(EAR Baseline)을 측정하고 있습니다.\n자연스러운 눈 깜빡임과 평소 작업 자세를 유지해 주세요."
RETRY_NOTICE = "얼굴이 잘 보이지 않았어요. 카메라에 얼굴이 보이게 앉아 주세요."
DIAL_CAPTION = "시선 집중"
CANCEL_TEXT = "캘리브레이션 취소"

# 다이얼 크기는 HTML 기준 px (디자인 한 변 DIAL_DESIGN_SIDE). 창이 작으면 비율대로 줄인다.
DIAL_DESIGN_SIDE = 288
DIAL_MIN_SIDE = 220
DASHED_DIAMETER = 256  # 천천히 도는 점선 링
FRAME_DIAMETER = 224  # 얇은 보라 링
DIAL_DIAMETER = 208  # 유리 다이얼
ARC_RADIUS = 98  # 진행 링 반지름
ARC_WIDTH = 4
CROSSHAIR_HALF = 32  # 조준선 반 길이 (40px 상자 + 양쪽 12px)
BRACKET_OFFSET = 12  # 다이얼 상자 밖으로 브래킷이 나가는 거리
BRACKET_SIZE = 12
COUNT_FONT_PX = 72

SPIN_MS = 60_000  # 점선 링 한 바퀴 (HTML spin 60s)
PROGRESS_ANIM_MS = 900  # 1초마다 줄어드는 진행 링 애니메이션 (HTML transition 1s ease-out)
BOTTOM_TRACK_HEIGHT = 4

STYLE = f"""
#dialCaption {{ color: {COLORS["on_surface_variant"]}; font-family: {SANS}; font-size: 11px; font-weight: 500; }}
#countUnit {{ color: {COLORS["primary"]}; font-family: {MONO}; font-size: 18px; font-weight: 600; }}
#calibrationMessage {{ color: {COLORS["on_surface"]}; font-family: {SANS}; font-size: 18px; font-weight: 600; }}
#calibrationDetail {{ color: {COLORS["on_surface_variant"]}; font-family: {SANS}; font-size: 12px; }}
"""


def _close_icon(color: str, side: int = 14) -> QIcon:
    """취소 버튼의 X 아이콘. 고해상도 화면에서도 흐리지 않게 2배로 그린다."""
    scale = 2
    pixmap = QPixmap(side * scale, side * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    painter.setPen(QPen(qcolor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    m = side * 0.25
    painter.drawLine(QPointF(m, m), QPointF(side - m, side - m))
    painter.drawLine(QPointF(side - m, m), QPointF(m, side - m))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


class CalibrationDial(QWidget):
    """가운데 원형 다이얼. 진행 링은 남은 시간 비율(1 → 0)만큼 12시부터 시계방향으로 그린다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(DIAL_MIN_SIDE, DIAL_MIN_SIDE)
        self.setMaximumSize(DIAL_DESIGN_SIDE, DIAL_DESIGN_SIDE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._fraction = 1.0
        self._angle = 0.0
        self._spin = QVariantAnimation(self)
        self._spin.setDuration(SPIN_MS)
        self._spin.setStartValue(0.0)
        self._spin.setEndValue(360.0)
        self._spin.setLoopCount(-1)
        self._spin.valueChanged.connect(self._on_spin)

        self.count_label = QLabel()
        self.count_label.setObjectName("countLabel")
        self.count_label.setFont(font("display_timer", COUNT_FONT_PX))
        self.count_label.setStyleSheet(f"color: {COLORS['on_surface']};")
        glow = QGraphicsDropShadowEffect(self.count_label)
        glow.setColor(qcolor("primary_container", 0.45))
        glow.setBlurRadius(24)
        glow.setOffset(0, 2)
        self.count_label.setGraphicsEffect(glow)
        unit = QLabel("s")
        unit.setObjectName("countUnit")
        caption = QLabel(DIAL_CAPTION)
        caption.setObjectName("dialCaption")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        count_row = QHBoxLayout()
        count_row.setSpacing(2)
        count_row.addStretch()
        count_row.addWidget(self.count_label, alignment=Qt.AlignmentFlag.AlignBottom)
        count_row.addWidget(unit, alignment=Qt.AlignmentFlag.AlignBottom)
        count_row.addStretch()
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.addStretch()
        layout.addLayout(count_row)
        layout.addWidget(caption)
        layout.addStretch()
        # 남은 초 숫자가 "s"와 함께 가운데로 오도록 숫자 아래 여백을 맞춘다
        unit.setContentsMargins(0, 0, 0, 14)

    @property
    def fraction(self) -> float:
        return self._fraction

    def set_fraction(self, value: float) -> None:
        self._fraction = min(max(value, 0.0), 1.0)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt 이름)
        return QSize(DIAL_DESIGN_SIDE, DIAL_DESIGN_SIDE)

    def _on_spin(self, value: float) -> None:
        self._angle = value
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._spin.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._spin.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        side = min(self.width(), self.height())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(side / DIAL_DESIGN_SIDE, side / DIAL_DESIGN_SIDE)  # 이후 좌표는 디자인 px, 원점은 중심
        origin = QPointF(0, 0)

        # 다이얼 바깥 보랏빛 빛무리 (HTML hud-glow)
        r = DIAL_DIAMETER / 2
        glow = QRadialGradient(origin, r + 30)
        glow.setColorAt(0.75, qcolor("primary_container", 0.22))
        glow.setColorAt(1.0, qcolor("primary_container", 0.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(origin, r + 30, r + 30)

        # 천천히 도는 점선 링
        painter.save()
        painter.rotate(self._angle)
        dashed = QPen(qcolor("outline_variant", 0.35), 1, Qt.PenStyle.DashLine)
        dashed.setDashPattern([4, 4])
        painter.setPen(dashed)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(origin, DASHED_DIAMETER / 2, DASHED_DIAMETER / 2)
        painter.restore()

        # 얇은 보라 링
        painter.setPen(QPen(qcolor("primary", 0.2), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(origin, FRAME_DIAMETER / 2, FRAME_DIAMETER / 2)

        # 유리 다이얼 (안쪽이 살짝 밝은 반투명 원)
        inner = QRadialGradient(origin, r)
        inner.setColorAt(0.0, qcolor("surface_container_high", 0.55))
        inner.setColorAt(0.8, qcolor("surface_container_high", 0.45))
        inner.setColorAt(1.0, qcolor("primary_container", 0.18))
        painter.setBrush(inner)
        painter.setPen(QPen(qcolor("primary", 0.4), 1))
        painter.drawEllipse(origin, r, r)

        # 진행 링: 바탕 트랙 + 남은 비율
        arc_rect = QRectF(-ARC_RADIUS, -ARC_RADIUS, ARC_RADIUS * 2, ARC_RADIUS * 2)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(qcolor("outline_variant", 0.25), ARC_WIDTH))
        painter.drawEllipse(arc_rect)
        if self._fraction > 0:
            pen = QPen(qcolor("primary_container"), ARC_WIDTH, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            # Qt 각도는 1/16도, 3시 방향 0도·반시계 양수 → 12시(90도)에서 음수로 그리면 시계방향
            painter.drawArc(arc_rect, 90 * 16, -round(self._fraction * 360 * 16))

        # 조준선
        painter.setPen(QPen(qcolor("primary", 0.4), 1))
        painter.drawLine(QPointF(-CROSSHAIR_HALF, 0), QPointF(CROSSHAIR_HALF, 0))
        painter.drawLine(QPointF(0, -CROSSHAIR_HALF), QPointF(0, CROSSHAIR_HALF))

        # 모서리 브래킷 (다이얼 상자 밖 네 귀퉁이)
        bracket = QPen(qcolor("primary", 0.6), 2)
        bracket.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(bracket)
        edge = r + BRACKET_OFFSET
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            corner = QPointF(sx * edge, sy * edge)
            painter.drawLine(corner, corner + QPointF(-sx * BRACKET_SIZE, 0))
            painter.drawLine(corner, corner + QPointF(0, -sy * BRACKET_SIZE))


class BottomTrack(QWidget):
    """창 맨 아래 얇은 남은 시간 막대 (HTML Bottom Ambient Status Track)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(BOTTOM_TRACK_HEIGHT)
        self._fraction = 1.0

    def set_fraction(self, value: float) -> None:
        self._fraction = min(max(value, 0.0), 1.0)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.fillRect(self.rect(), qcolor("surface_container_lowest"))
        filled = round(self.width() * self._fraction)
        painter.fillRect(filled, 0, self.width() - filled, self.height(), qcolor("surface_container_high", 0.4))
        painter.fillRect(0, 0, filled, self.height(), qcolor("primary", 0.8))


class CalibrationView(QWidget):
    countdown_finished = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calibrationView")
        self.setStyleSheet(STYLE)
        self._remaining = CALIBRATION_SEC

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

        # 진행 링·아래 막대를 1초 동안 부드럽게 줄인다
        self._progress_anim = QVariantAnimation(self)
        self._progress_anim.setDuration(PROGRESS_ANIM_MS)
        self._progress_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._progress_anim.valueChanged.connect(self._show_fraction)

        self.retry_notice = QLabel(RETRY_NOTICE)
        self.retry_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style_props(self.retry_notice, notice="warning")
        self.retry_notice.hide()

        self.dial = CalibrationDial()
        self.countdown_label = self.dial.count_label

        self.message_label = QLabel(START_MESSAGE)
        self.message_label.setObjectName("calibrationMessage")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel(DETAIL_TEXT)
        detail.setObjectName("calibrationDetail")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.cancel_button = QPushButton(CANCEL_TEXT)
        set_style_props(self.cancel_button, variant="ghost")
        self.cancel_button.setIcon(_close_icon("outline"))
        self.cancel_button.setIconSize(QSize(14, 14))
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.cancel)

        self.bottom_track = BottomTrack()

        content = QVBoxLayout()
        content.setContentsMargins(32, 20, 32, 20)
        content.setSpacing(0)
        content.addWidget(self.retry_notice, alignment=Qt.AlignmentFlag.AlignCenter)
        content.addStretch()
        content.addWidget(self.dial, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        content.addStretch()
        content.addWidget(self.message_label)
        content.addSpacing(6)
        content.addWidget(detail)
        content.addSpacing(14)
        content.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(content, 1)
        layout.addWidget(self.bottom_track)

        self._update_countdown(animate=False)

    @property
    def remaining(self) -> int:
        return self._remaining

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def target_fraction(self) -> float:
        """진행 링이 향하는 남은 시간 비율 (애니메이션이 끝나면 화면 값과 같다)."""
        return self._remaining / CALIBRATION_SEC

    def start(self) -> None:
        self._begin(START_MESSAGE, retry=False)

    def retry(self) -> None:
        self._begin(RETRY_MESSAGE, retry=True)

    def _begin(self, message: str, retry: bool) -> None:
        self.message_label.setText(message)
        self.retry_notice.setVisible(retry)
        self._remaining = CALIBRATION_SEC
        self._update_countdown(animate=False)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def cancel(self) -> None:
        self.stop()
        self.cancel_requested.emit()

    def tick(self) -> None:
        """1초 경과. QTimer가 부르며, 테스트에서는 직접 부른다."""
        if self._remaining <= 0:
            return
        self._remaining -= 1
        self._update_countdown()
        if self._remaining == 0:
            self.stop()
            self.countdown_finished.emit()

    def _update_countdown(self, animate: bool = True) -> None:
        self.countdown_label.setText(str(self._remaining))
        self._progress_anim.stop()
        if animate:
            self._progress_anim.setStartValue(self.dial.fraction)
            self._progress_anim.setEndValue(self.target_fraction)
            self._progress_anim.start()
        else:
            self._show_fraction(self.target_fraction)

    def _show_fraction(self, value: float) -> None:
        self.dial.set_fraction(value)
        self.bottom_track.set_fraction(value)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        # 바탕: 짙은 남색 + 다이얼 뒤 보랏빛, 왼쪽 위(보라)·오른쪽 아래(초록) 은은한 빛
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), qcolor("background"))
        painter.setPen(Qt.PenStyle.NoPen)
        for center, radius, color, alpha in (
            (QPointF(w * 0.5, h * 0.45), max(w, h) * 0.65 / 2 * 1.4, "primary_container", 0.12),
            (QPointF(0, 0), 190, "primary", 0.06),
            (QPointF(w, h), 190, "secondary", 0.05),
        ):
            gradient = QRadialGradient(center, radius)
            gradient.setColorAt(0.0, qcolor(color, alpha))
            gradient.setColorAt(1.0, qcolor(color, 0.0))
            painter.setBrush(gradient)
            painter.drawRect(self.rect())
        painter.end()
        super().paintEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    load_fonts()
    view = CalibrationView()
    view.setStyleSheet(APP_STYLE + STYLE)  # 혼자 띄울 때는 MainWindow의 공통 스타일이 없어서 같이 깐다
    view.resize(580, 580)
    view.setWindowTitle("FocusPomo")
    restart = view.retry if "--retry" in sys.argv else view.start
    view.countdown_finished.connect(lambda: QTimer.singleShot(1000, restart))
    view.cancel_requested.connect(app.quit)
    view.show()
    restart()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
