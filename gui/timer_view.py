"""25분 집중 타이머 화면 (개발계획서 1장, 2.4, 디자인 3-06).

session_start(캘리브레이션 종료 시각)부터 SESSION_SEC 동안 카운트다운한다.
남은 시간은 매 tick마다 (time.time() - session_start - 일시정지한 시간)으로 다시 계산하므로
QTimer가 밀려도 오차가 쌓이지 않는다.

- start(session_start): 카운트다운 시작. 타이머 화면에 들어올 때 MainWindow가 부른다.
- 남은 시간이 0이 되면 time_up을 한 번 보낸다.
- "일시 정지" / "다시 시작": 멈춘 만큼 세션 종료가 늦어진다. 횟수·시간 제한은 없다.
  CV·창 스레드는 정지 중에도 계속 슬롯을 보내므로, 정지 구간을 pause_intervals
  (schemas.PauseInterval 목록)로 남긴다. 이 구간과 조금이라도 겹치는 슬롯은
  1-13에서 schemas.slot_overlaps_pause()로 골라 점수·LLM에서 뺀다.
- "세션 종료"를 누르면 (정지 중이면 정지 구간을 닫고) 멈추고 end_requested를 보낸다.
- "미니 모드"를 누르면 mini_mode_requested를 보낸다. MainWindow가 미니 모드 창(gui.mini_timer)으로 바꾼다.
  타이머 계산은 미니 모드 중에도 이 화면이 계속 하고, 상태가 바뀔 때마다 refreshed를 보낸다.

디자인은 사용자가 준 HTML(Focus Timer)을 옮겼다. 색·글꼴은 gui.style, 아이콘은 gui.widgets에서 가져온다.
- 위: "SESSION n / 4"(긴 휴식 주기 안에서 몇 번째 세션인지). HTML의 "25:00 목표"는 뺐다
- 가운데: 유리 질감 트랙 위에 경과 비율만큼 민트 → 보라 그라데이션 링, 남은 시간, 상태 알약
- 아래: 일시 정지 · 세션 종료 / 미니 모드
HTML의 창 제목 표시줄은 OS 기본을 쓴다 (다른 화면과 같음).

    python -m gui.timer_view     # 타이머 화면만 띄워 보기 (60배속)
"""
from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QFont, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.pomodoro import LONG_BREAK_EVERY
from gui.style import APP_STYLE, COLORS, SANS, font, load_fonts, qcolor, rgba
from gui.widgets import PingDot, line_icon
from schemas import PauseInterval, pause_total

# TODO: 팀 합의 후 config/로 옮기기 제안 (지금은 config에 세션 길이 상수가 없다)
SESSION_SEC = 25 * 60
TICK_MS = 250  # 1초보다 짧게 돌려 표시가 초를 건너뛰지 않게 한다

STATUS_TEXT = "집중 세션 진행 중"
PAUSED_STATUS_TEXT = "일시 정지됨"
PAUSE_BUTTON_TEXT = "일시 정지"
RESUME_BUTTON_TEXT = "다시 시작"
END_BUTTON_TEXT = "세션 종료"
MINI_BUTTON_TEXT = "미니 모드"
FINISHING_STATUS_TEXT = "결과 정리 중…"
REMAINING_CAPTION = "Remaining time"  # telemetry_label 글꼴이 대문자로 바꿔 보여준다

# 링은 HTML viewBox(380) 기준 px. 위젯 크기에 맞춰 비율대로 그린다.
RING_VIEWBOX = 380
RING_RADIUS = 168
RING_MIN_SIDE = 220
TRACK_WIDTH = 12
PROGRESS_WIDTH = 9
TIME_FONT_RATIO = 70 / 340  # 링 위젯 한 변 대비 남은 시간 글자 크기 (HTML 340px에 70px)
TIME_FONT_MIN = 40

# 상태 알약 색 (정지 중이면 주황, 결과 정리 중이면 보라)
STATE_COLORS = {"running": "secondary", "paused": "tertiary", "finishing": "primary"}
NEON_STOPS = ((0.0, "secondary_fixed"), (0.3, "secondary"), (0.7, "primary_container"), (1.0, "primary"))

STYLE = f"""
#sessionPill {{
    background: {rgba("surface_container", 0.6)};
    border: 1px solid {rgba("primary", 0.2)};
    border-radius: 11px;
}}
#sessionDot {{ background: {rgba("primary", 0.7)}; border-radius: 3px; }}
#sessionLabel {{ color: {COLORS["primary"]}; }}
#remainingCaption {{ color: {rgba("outline", 0.8)}; }}
#timeLabel {{ color: {COLORS["on_surface"]}; }}
#statusPill {{
    background: {rgba("surface_container_high", 0.8)};
    border: 1px solid {rgba("secondary", 0.3)};
    border-radius: 11px;  /* 알약 높이(22px)의 절반. 넘으면 Qt가 둥근 모서리를 그리지 않는다 */
}}
#statusPill[state="paused"] {{ border-color: {rgba("tertiary", 0.3)}; }}
#statusPill[state="finishing"] {{ border-color: {rgba("primary", 0.3)}; }}
#statusText {{ color: {COLORS["secondary"]}; font-family: {SANS}; font-size: 12px; font-weight: 500; }}
#statusText[state="paused"] {{ color: {COLORS["tertiary"]}; }}
#statusText[state="finishing"] {{ color: {COLORS["primary"]}; }}
#controlBar {{ border-top: 1px solid {rgba("outline_variant", 0.2)}; }}
#pauseButton, #endButton, #miniButton {{
    border-radius: 4px;
    font-family: {SANS};
    font-size: 13px;
    font-weight: 500;
    padding: 8px 14px;
}}
#pauseButton {{
    background: {rgba("surface_container", 0.9)};
    border: 1px solid {rgba("outline_variant", 0.4)};
    color: {COLORS["on_surface"]};
    padding-left: 16px;
    padding-right: 16px;
}}
#pauseButton:hover {{ background: {COLORS["surface_container_high"]}; border-color: {rgba("primary", 0.5)}; }}
#pauseButton:pressed {{ background: {COLORS["surface_container_highest"]}; }}
#endButton, #miniButton {{
    background: {rgba("surface_container_low", 0.8)};
    border: 1px solid {rgba("outline_variant", 0.3)};
    color: {COLORS["on_surface_variant"]};
}}
#endButton:hover {{
    color: {COLORS["error"]};
    border-color: {rgba("error", 0.4)};
    background: {rgba("error_container", 0.15)};
}}
#endButton:pressed {{ background: {rgba("error_container", 0.3)}; }}
#miniButton:hover {{
    color: {COLORS["on_surface"]};
    background: {COLORS["surface_container"]};
    border-color: {rgba("outline_variant", 0.5)};
}}
#miniButton:pressed {{ background: {COLORS["surface_container_high"]}; }}
#pauseButton:disabled, #endButton:disabled, #miniButton:disabled {{
    background: {COLORS["surface_container_low"]};
    border-color: {rgba("outline_variant", 0.3)};
    color: {COLORS["outline_variant"]};
}}
"""


def format_mmss(seconds: int) -> str:
    minutes, secs = divmod(max(seconds, 0), 60)
    return f"{minutes:02d}:{secs:02d}"


def _neon_gradient(start: QPointF, end: QPointF, alpha: float = 1.0) -> QLinearGradient:
    gradient = QLinearGradient(start, end)
    for stop, color in NEON_STOPS:
        gradient.setColorAt(stop, qcolor(color, alpha))
    return gradient


class ProgressRing(QWidget):
    """경과 비율만큼 12시 방향부터 시계방향으로 채우는 원형 링. 안쪽에 남은 시간과 상태 알약을 둔다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self.setMinimumSize(RING_MIN_SIDE, RING_MIN_SIDE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        caption = QLabel(REMAINING_CAPTION)
        caption.setObjectName("remainingCaption")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption_font = font("telemetry_label")
        caption_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, caption_font.pixelSize() * 0.25)
        caption.setFont(caption_font)

        self.time_label = QLabel(format_mmss(SESSION_SEC))
        self.time_label.setObjectName("timeLabel")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glow = QGraphicsDropShadowEffect(self.time_label)
        glow.setColor(qcolor("primary", 0.28))
        glow.setBlurRadius(36)
        glow.setOffset(0, 0)
        self.time_label.setGraphicsEffect(glow)

        self.status_pill = pill = QFrame()
        pill.setObjectName("statusPill")
        self.status_dot = PingDot(COLORS["secondary"])
        self.status_label = QLabel(STATUS_TEXT)
        self.status_label.setObjectName("statusText")
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(8, 3, 12, 3)
        pill_layout.setSpacing(6)
        pill_layout.addWidget(self.status_dot, alignment=Qt.AlignmentFlag.AlignVCenter)
        pill_layout.addWidget(self.status_label)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.addStretch()
        layout.addWidget(caption)
        layout.addSpacing(2)
        layout.addWidget(self.time_label)
        layout.addSpacing(12)
        layout.addWidget(pill, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    @property
    def progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = min(max(value, 0.0), 1.0)
        self.update()

    def set_state(self, state: str) -> None:
        """상태 알약 색을 바꾼다: running / paused / finishing."""
        self.status_dot.set_state(COLORS[STATE_COLORS[state]], pinging=state != "paused")
        for widget in (self.status_pill, self.status_label):
            widget.setProperty("state", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt 이름)
        return QSize(340, 340)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        side = min(self.width(), self.height())
        f = font("display_timer", max(int(side * TIME_FONT_RATIO), TIME_FONT_MIN))
        f.setWeight(QFont.Weight.Bold)
        self.time_label.setFont(f)
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        side = min(self.width(), self.height())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(side / RING_VIEWBOX, side / RING_VIEWBOX)  # 이후 좌표는 HTML viewBox px, 원점은 중심
        r = RING_RADIUS
        rect = QRectF(-r, -r, 2 * r, 2 * r)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 트랙: 가라앉은 그림자 + 유리 질감 (왼쪽 위 밝음 → 오른쪽 아래 어두움) + 안팎 가는 테두리
        for width, alpha in ((22, 0.12), (16, 0.3)):
            painter.setPen(QPen(qcolor("#000000", alpha), width))
            painter.drawEllipse(rect)
        glass = QLinearGradient(QPointF(-r, -r), QPointF(r, r))
        glass.setColorAt(0.0, qcolor("#ffffff", 0.07))
        glass.setColorAt(0.5, qcolor("primary", 0.03))
        glass.setColorAt(1.0, qcolor("#000000", 0.25))
        painter.setPen(QPen(QBrush(glass), TRACK_WIDTH))
        painter.drawEllipse(rect)
        painter.setPen(QPen(qcolor("primary", 0.12), 1))
        painter.drawEllipse(QPointF(0, 0), r + 6, r + 6)
        painter.setPen(QPen(qcolor("#ffffff", 0.05), 1))
        painter.drawEllipse(QPointF(0, 0), r - 6, r - 6)

        if self._progress <= 0:
            return
        # Qt 각도는 1/16도, 3시 방향 0도·반시계 양수 → 12시(90도)에서 음수로 그리면 시계방향
        start_angle = 90 * 16
        span_angle = -round(self._progress * 360 * 16)
        # HTML은 -90도 돌린 SVG라 그라데이션이 왼쪽 아래(민트) → 오른쪽 위(연보라)
        neon_start, neon_end = QPointF(-r, r), QPointF(r, -r)

        # 빛 번짐: 넓고 옅은 선을 겹쳐 blur를 흉내 낸다
        for width, alpha in ((44, 0.02), (38, 0.025), (32, 0.03), (26, 0.04), (20, 0.06), (15, 0.09)):
            glow = _neon_gradient(neon_start, neon_end, alpha)
            painter.setPen(QPen(QBrush(glow), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(rect, start_angle, span_angle)

        neon = _neon_gradient(neon_start, neon_end)
        painter.setPen(QPen(QBrush(neon), PROGRESS_WIDTH, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, start_angle, span_angle)

        # 링 바깥 가장자리 하이라이트 (유리 반사)
        shine = QLinearGradient(QPointF(-r, -r), QPointF(r, r))
        shine.setColorAt(0.0, qcolor("#ffffff", 0.8))
        shine.setColorAt(0.4, qcolor("#ffffff", 0.34))
        shine.setColorAt(1.0, qcolor("#ffffff", 0.08))
        painter.setPen(QPen(QBrush(shine), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        hr = r + 1.5
        painter.drawArc(QRectF(-hr, -hr, 2 * hr, 2 * hr), start_angle, span_angle)


class TimerView(QWidget):
    time_up = Signal()
    end_requested = Signal()
    mini_mode_requested = Signal()  # MainWindow가 미니 모드 창(gui.mini_timer)으로 바꾼다
    refreshed = Signal()  # 남은 시간·정지·정리 중 상태가 바뀔 때마다 (미니 모드 창이 따라 그린다)

    def __init__(self, clock: Callable[[], float] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clock = clock or time.time
        self._session_start: float | None = None
        self._remaining = SESSION_SEC
        self._pause_intervals: list[PauseInterval] = []  # 닫힌 정지 구간
        self._paused_at: float | None = None  # 정지 중이면 정지 시각
        self._finishing = False  # 세션이 끝나 결과를 정리하는 중인지

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

        self.setObjectName("timerView")
        self.setStyleSheet(STYLE)

        # 위: 세션 번호
        session_pill = QFrame()
        session_pill.setObjectName("sessionPill")
        session_dot = QFrame()
        session_dot.setObjectName("sessionDot")
        session_dot.setFixedSize(6, 6)
        self.session_label = QLabel()
        self.session_label.setObjectName("sessionLabel")
        self.session_label.setFont(font("telemetry_label"))
        pill_layout = QHBoxLayout(session_pill)
        pill_layout.setContentsMargins(12, 4, 12, 4)
        pill_layout.setSpacing(6)
        pill_layout.addWidget(session_dot, alignment=Qt.AlignmentFlag.AlignVCenter)
        pill_layout.addWidget(self.session_label)
        self.set_session_number(1)

        header = QHBoxLayout()
        header.addWidget(session_pill)
        header.addStretch()

        self.ring = ProgressRing()
        self.time_label = self.ring.time_label

        # 아래: 일시 정지 · 세션 종료 / 미니 모드
        self.pause_button = QPushButton(PAUSE_BUTTON_TEXT)
        self.pause_button.setObjectName("pauseButton")
        self.pause_button.clicked.connect(self.toggle_pause)

        self.end_button = QPushButton(END_BUTTON_TEXT)
        self.end_button.setObjectName("endButton")
        self.end_button.setIcon(line_icon("stop", "on_surface_variant", 17))
        self.end_button.clicked.connect(self.end)

        self.mini_button = QPushButton(MINI_BUTTON_TEXT)
        self.mini_button.setObjectName("miniButton")
        self.mini_button.setIcon(line_icon("pip", "on_surface_variant", 17))
        self.mini_button.clicked.connect(self.mini_mode_requested)

        for button in (self.pause_button, self.end_button, self.mini_button):
            button.setIconSize(QSize(17, 17))
            button.setCursor(Qt.CursorShape.PointingHandCursor)

        control_bar = QFrame()
        control_bar.setObjectName("controlBar")
        controls = QHBoxLayout(control_bar)
        controls.setContentsMargins(0, 12, 0, 0)
        controls.setSpacing(8)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.end_button)
        controls.addStretch()
        controls.addWidget(self.mini_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.ring, stretch=1)
        layout.addWidget(control_bar)

        self._set_paused_look(False)

    def set_session_number(self, number: int) -> None:
        """위쪽 알약의 "SESSION n / LONG_BREAK_EVERY". MainWindow가 타이머로 갈 때 부른다."""
        self.session_label.setText(f"Session {number} / {LONG_BREAK_EVERY}")

    @property
    def remaining(self) -> int:
        """남은 시간(초, 올림). 시작 직후는 SESSION_SEC, 끝나면 0."""
        return self._remaining

    @property
    def is_running(self) -> bool:
        """카운트다운 중인지. 일시정지 중에는 False."""
        return self._timer.isActive()

    @property
    def is_paused(self) -> bool:
        return self._paused_at is not None

    @property
    def is_finishing(self) -> bool:
        """세션이 끝나 결과를 정리하는 중인지 (show_finishing() 뒤, 다음 start() 전)."""
        return self._finishing

    @property
    def pause_intervals(self) -> list[PauseInterval]:
        """이번 세션의 정지 구간 목록 (시간순). 정지 중인 구간은 끝나기 전까지 들어가지 않는다."""
        return [p.copy() for p in self._pause_intervals]

    def start(self, session_start: float) -> None:
        self._session_start = session_start
        self._pause_intervals = []
        self._paused_at = None
        self._finishing = False
        self._set_paused_look(False)
        self._set_buttons_enabled(True)
        self._timer.start()
        self._refresh()

    def show_finishing(self) -> None:
        """세션이 끝나 결과를 정리하는 동안(CV flush 대기) 버튼을 막고 상태 문구를 바꾼다."""
        self.stop()
        self._finishing = True
        self._set_buttons_enabled(False)
        self.ring.status_label.setText(FINISHING_STATUS_TEXT)
        self.ring.set_state("finishing")
        self.refreshed.emit()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self.pause_button.setEnabled(enabled)
        self.end_button.setEnabled(enabled)
        self.mini_button.setEnabled(enabled)

    def stop(self) -> None:
        """카운트다운을 멈춘다. 정지 중이었으면 정지 구간을 지금 시각으로 닫는다."""
        self._close_pause()
        self._timer.stop()

    def pause(self) -> None:
        if not self.is_running:
            return
        self._timer.stop()
        self._paused_at = self._clock()
        self._refresh()
        self._set_paused_look(True)

    def resume(self) -> None:
        if not self.is_paused:
            return
        self._close_pause()
        self._set_paused_look(False)
        self._timer.start()
        self._refresh()

    def toggle_pause(self) -> None:
        if self.is_paused:
            self.resume()
        else:
            self.pause()

    def end(self) -> None:
        self.stop()
        self.end_requested.emit()

    def tick(self) -> None:
        """QTimer가 부르며, 테스트에서는 clock을 움직인 뒤 직접 부른다."""
        if not self.is_running:
            return
        self._refresh()
        if self._remaining == 0:
            self.stop()
            self.time_up.emit()

    def _close_pause(self) -> None:
        if self._paused_at is None:
            return
        self._pause_intervals.append({"start": self._paused_at, "end": self._clock()})
        self._paused_at = None

    def _paused_seconds(self, now: float) -> float:
        total = pause_total(self._pause_intervals)
        if self._paused_at is not None:
            total += now - self._paused_at
        return total

    def _set_paused_look(self, paused: bool) -> None:
        self.pause_button.setText(RESUME_BUTTON_TEXT if paused else PAUSE_BUTTON_TEXT)
        self.pause_button.setIcon(line_icon("play" if paused else "pause", "primary", 17))
        self.ring.status_label.setText(PAUSED_STATUS_TEXT if paused else STATUS_TEXT)
        self.ring.set_state("paused" if paused else "running")
        self.refreshed.emit()

    def _refresh(self) -> None:
        now = self._clock()
        elapsed = now - self._session_start - self._paused_seconds(now)
        elapsed = min(max(elapsed, 0.0), SESSION_SEC)
        self._remaining = math.ceil(SESSION_SEC - elapsed)
        self.time_label.setText(format_mmss(self._remaining))
        self.ring.set_progress(elapsed / SESSION_SEC)
        self.refreshed.emit()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        # 바탕: 링 뒤가 살짝 밝고 가장자리로 갈수록 어두워지는 남색 (HTML radial-gradient)
        painter = QPainter(self)
        w, h = self.width(), self.height()
        gradient = QRadialGradient(QPointF(w * 0.5, h * 0.48), max(w, h) * 0.75)
        gradient.setColorAt(0.0, qcolor("surface_container_low"))
        gradient.setColorAt(0.75, qcolor("background"))
        gradient.setColorAt(1.0, qcolor("surface_container_lowest"))
        painter.fillRect(self.rect(), gradient)
        painter.end()
        super().paintEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    load_fonts()
    start = time.time()
    view = TimerView(clock=lambda: start + (time.time() - start) * 60)  # 60배속
    view.setStyleSheet(APP_STYLE + STYLE)  # 혼자 띄울 때는 MainWindow의 공통 스타일이 없어서 같이 깐다
    view.resize(580, 580)
    view.setWindowTitle("FocusPomo")
    view.time_up.connect(app.quit)
    view.end_requested.connect(app.quit)
    view.show()
    view.start(start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
