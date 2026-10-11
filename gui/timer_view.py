"""25분 집중 타이머 화면 (개발계획서 1장, 2.4).

session_start(캘리브레이션 종료 시각)부터 SESSION_SEC 동안 카운트다운한다.
남은 시간은 매 tick마다 (time.time() - session_start - 일시정지한 시간)으로 다시 계산하므로
QTimer가 밀려도 오차가 쌓이지 않는다.

- start(session_start): 카운트다운 시작. 타이머 화면에 들어올 때 MainWindow가 부른다.
- 남은 시간이 0이 되면 time_up을 한 번 보낸다.
- "일시 정지" / "다시 시작": 멈춘 만큼 세션 종료가 늦어진다. 횟수·시간 제한은 없다.
  CV·창 스레드는 정지 중에도 계속 슬롯을 보내므로, 정지 구간을 pause_intervals
  (schemas.PauseInterval 목록)로 남긴다. 이 구간과 조금이라도 겹치는 슬롯은
  1-13에서 schemas.slot_overlaps_pause()로 골라 점수·LLM에서 뺀다.
- "중간 종료"를 누르면 (정지 중이면 정지 구간을 닫고) 멈추고 end_requested를 보낸다.

화면 디자인은 원형 진행 링 + 남은 시간 (다크 팔레트).
색은 3-06에서 gui/style.py로 옮길 때까지 이 파일의 COLORS에 둔다.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from schemas import PauseInterval, pause_total

# TODO: 팀 합의 후 config/로 옮기기 제안 (지금은 config에 세션 길이 상수가 없다)
SESSION_SEC = 25 * 60
TICK_MS = 250  # 1초보다 짧게 돌려 표시가 초를 건너뛰지 않게 한다

STATUS_TEXT = "집중 세션 진행 중"
PAUSED_STATUS_TEXT = "일시 정지됨"
PAUSE_BUTTON_TEXT = "일시 정지"
RESUME_BUTTON_TEXT = "다시 시작"
END_BUTTON_TEXT = "중간 종료"

COLORS = {
    "background": "#0b1326",
    "surface_low": "#131b2e",
    "surface_container": "#171f33",
    "surface_high": "#222a3d",
    "surface_highest": "#2d3449",
    "on_surface": "#dae2fd",
    "on_surface_variant": "#c7c4d7",
    "outline": "#908fa0",
    "outline_variant": "#464554",
    "primary": "#c0c1ff",
    "primary_container": "#8083ff",
    "secondary": "#4edea3",
    "tertiary": "#ffb95f",
    "error": "#ffb4ab",
}
SANS = '"Inter", "Segoe UI", "Malgun Gothic"'
MONO = '"JetBrains Mono", "Consolas", "Malgun Gothic"'

RING_MIN_SIDE = 220
RING_DIAMETER_RATIO = 168 / 190  # 참고 디자인 viewBox 380, r=168
TRACK_WIDTH = 4
PROGRESS_WIDTH = 7
GLOW_WIDTH = 16
TIME_FONT_RATIO = 0.2  # 링 한 변 대비 남은 시간 글자 크기

STYLE = f"""
#timerView {{ background: {COLORS["background"]}; }}
#sessionTitle {{ color: {COLORS["on_surface"]}; font-family: {SANS}; font-size: 18px; font-weight: 600; }}
#goalLabel {{ color: {COLORS["outline"]}; font-family: {MONO}; font-size: 11px; }}
#liveDot {{ background: {COLORS["secondary"]}; border-radius: 4px; }}
#remainingCaption {{ color: {COLORS["outline"]}; font-family: {MONO}; font-size: 11px; letter-spacing: 1px; }}
#timeLabel {{ color: {COLORS["on_surface"]}; }}
#statusPill {{
    background: rgba(34, 42, 61, 0.7);
    border: 1px solid rgba(78, 222, 163, 0.25);
    border-radius: 12px;
}}
#statusDot {{ background: {COLORS["secondary"]}; border-radius: 3px; }}
#statusText {{ color: {COLORS["secondary"]}; font-family: {MONO}; font-size: 12px; }}
#statusPill[paused="true"] {{ border-color: rgba(255, 185, 95, 0.3); }}
#statusDot[paused="true"] {{ background: {COLORS["tertiary"]}; }}
#statusText[paused="true"] {{ color: {COLORS["tertiary"]}; }}
#liveDot[paused="true"] {{ background: {COLORS["tertiary"]}; }}
#pauseButton {{
    background: {COLORS["surface_container"]};
    border: 1px solid rgba(70, 69, 84, 0.8);
    border-radius: 4px;
    color: {COLORS["on_surface"]};
    font-family: {SANS};
    font-size: 13px;
    font-weight: 500;
    padding: 10px 22px;
}}
#pauseButton:hover {{ background: {COLORS["surface_high"]}; border-color: rgba(192, 193, 255, 0.4); }}
#pauseButton:pressed {{ background: {COLORS["surface_highest"]}; }}
#divider {{ background: rgba(70, 69, 84, 0.35); }}
#endButton {{
    background: {COLORS["surface_low"]};
    border: 1px solid rgba(70, 69, 84, 0.6);
    border-radius: 4px;
    color: {COLORS["on_surface_variant"]};
    font-family: {SANS};
    font-size: 13px;
    padding: 10px 18px;
}}
#endButton:hover {{
    color: {COLORS["error"]};
    border-color: rgba(255, 180, 171, 0.4);
    background: rgba(147, 0, 10, 0.12);
}}
#endButton:pressed {{ background: rgba(147, 0, 10, 0.25); }}
"""


def format_mmss(seconds: int) -> str:
    minutes, secs = divmod(max(seconds, 0), 60)
    return f"{minutes:02d}:{secs:02d}"


class ProgressRing(QWidget):
    """경과 비율만큼 12시 방향부터 시계방향으로 채우는 원형 링. 안쪽에 남은 시간 라벨을 둔다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self.setMinimumSize(RING_MIN_SIDE, RING_MIN_SIDE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        caption = QLabel("남은 시간")
        caption.setObjectName("remainingCaption")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.time_label = QLabel(format_mmss(SESSION_SEC))
        self.time_label.setObjectName("timeLabel")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.status_pill = pill = QFrame()
        pill.setObjectName("statusPill")
        self.status_dot = status_dot = QFrame()
        status_dot.setObjectName("statusDot")
        status_dot.setFixedSize(6, 6)
        self.status_label = QLabel(STATUS_TEXT)
        self.status_label.setObjectName("statusText")
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(12, 4, 12, 4)
        pill_layout.setSpacing(8)
        pill_layout.addWidget(status_dot, alignment=Qt.AlignmentFlag.AlignVCenter)
        pill_layout.addWidget(self.status_label)

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(caption)
        layout.addWidget(self.time_label)
        layout.addSpacing(8)
        layout.addWidget(pill, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    @property
    def progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = min(max(value, 0.0), 1.0)
        self.update()

    def _ring_rect(self) -> QRectF:
        side = min(self.width(), self.height()) * RING_DIAMETER_RATIO
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        font = QFont()
        font.setFamilies(["Inter", "Segoe UI", "Malgun Gothic"])
        font.setPixelSize(max(int(min(self.width(), self.height()) * TIME_FONT_RATIO), 32))
        font.setWeight(QFont.Weight.Bold)
        self.time_label.setFont(font)
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        rect = self._ring_rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 링 안쪽의 은은한 배경 원
        inset = rect.width() * 0.05
        backdrop = QColor(COLORS["surface_low"])
        backdrop.setAlphaF(0.45)
        border = QColor(COLORS["outline_variant"])
        border.setAlphaF(0.2)
        painter.setPen(QPen(border, 1))
        painter.setBrush(backdrop)
        painter.drawEllipse(rect.adjusted(inset, inset, -inset, -inset))

        # 바탕 트랙
        track = QColor(COLORS["surface_high"])
        track.setAlphaF(0.6)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(track, TRACK_WIDTH))
        painter.drawEllipse(rect)

        if self._progress <= 0:
            return
        # Qt 각도는 1/16도, 3시 방향 0도·반시계 양수 → 12시(90도)에서 음수로 그리면 시계방향
        start_angle = 90 * 16
        span_angle = -round(self._progress * 360 * 16)

        glow = QColor(COLORS["secondary"])
        glow.setAlphaF(0.12)
        painter.setPen(QPen(glow, GLOW_WIDTH, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, start_angle, span_angle)

        gradient = QLinearGradient(rect.topRight(), rect.bottomLeft())
        gradient.setColorAt(0.0, QColor(COLORS["secondary"]))
        gradient.setColorAt(0.6, QColor(COLORS["primary_container"]))
        gradient.setColorAt(1.0, QColor(COLORS["primary"]))
        painter.setPen(QPen(QBrush(gradient), PROGRESS_WIDTH, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, start_angle, span_angle)


class TimerView(QWidget):
    time_up = Signal()
    end_requested = Signal()

    def __init__(self, clock: Callable[[], float] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clock = clock or time.time
        self._session_start: float | None = None
        self._remaining = SESSION_SEC
        self._pause_intervals: list[PauseInterval] = []  # 닫힌 정지 구간
        self._paused_at: float | None = None  # 정지 중이면 정지 시각

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

        self.setObjectName("timerView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)

        # 헤더: 세션 제목 / 목표 시간
        self.live_dot = live_dot = QFrame()
        live_dot.setObjectName("liveDot")
        live_dot.setFixedSize(8, 8)
        title = QLabel("집중 세션")
        title.setObjectName("sessionTitle")
        goal = QLabel(f"{format_mmss(SESSION_SEC)} 목표")
        goal.setObjectName("goalLabel")
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(live_dot, alignment=Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(goal)

        self.ring = ProgressRing()
        self.time_label = self.ring.time_label

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)

        self.end_button = QPushButton(END_BUTTON_TEXT)
        self.end_button.setObjectName("endButton")
        self.end_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.end_button.clicked.connect(self.end)

        self.pause_button = QPushButton(PAUSE_BUTTON_TEXT)
        self.pause_button.setObjectName("pauseButton")
        self.pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_button.clicked.connect(self.toggle_pause)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        footer.addWidget(self.pause_button)
        footer.addWidget(self.end_button)
        footer.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.addLayout(header)
        layout.addWidget(self.ring, stretch=1)
        layout.addWidget(divider)
        layout.addSpacing(12)
        layout.addLayout(footer)

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
    def pause_intervals(self) -> list[PauseInterval]:
        """이번 세션의 정지 구간 목록 (시간순). 정지 중인 구간은 끝나기 전까지 들어가지 않는다."""
        return [p.copy() for p in self._pause_intervals]

    def start(self, session_start: float) -> None:
        self._session_start = session_start
        self._pause_intervals = []
        self._paused_at = None
        self._set_paused_look(False)
        self._timer.start()
        self._refresh()

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
        self.ring.status_label.setText(PAUSED_STATUS_TEXT if paused else STATUS_TEXT)
        for widget in (self.live_dot, self.ring.status_pill, self.ring.status_dot, self.ring.status_label):
            widget.setProperty("paused", paused)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _refresh(self) -> None:
        now = self._clock()
        elapsed = now - self._session_start - self._paused_seconds(now)
        elapsed = min(max(elapsed, 0.0), SESSION_SEC)
        self._remaining = math.ceil(SESSION_SEC - elapsed)
        self.time_label.setText(format_mmss(self._remaining))
        self.ring.set_progress(elapsed / SESSION_SEC)
