"""휴식 카운트다운 막대 (1-15, 3-06 디자인). 리포트 화면 위쪽에 붙는다.

리포트 = 휴식 화면이다 (개발계획서 2.3 "휴식 시작 시 1회 호출"). 리포트가 뜨면 MainWindow가 start()로
짧은/긴 휴식을 시작하고, 시간이 다 되면 finished를 한 번 보낸 뒤 "휴식이 끝났어요"를 보여준다.
다음 세션은 자동으로 시작하지 않는다 (캘리브레이션 때 화면을 봐야 하므로 사용자가 버튼을 누른다).

남은 시간은 TimerView처럼 매 tick (clock() - 시작 시각)으로 다시 계산한다.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QProgressBar, QWidget

from core.pomodoro import BREAK_SECONDS, LONG_BREAK_EVERY, BreakKind
from gui.style import COLORS, RADIUS, SPACING, font, pill_radius, rgba, set_style_props
from gui.timer_view import TICK_MS, format_mmss

BREAK_TITLE = {BreakKind.SHORT: "짧은 휴식", BreakKind.LONG: "긴 휴식"}
FINISHED_TEXT = "휴식이 끝났어요. 준비되면 다음 세션을 시작하세요."

PROGRESS_HEIGHT = 4
TIME_FONT_PX = 22

STYLE = f"""
QProgressBar {{
    background: {rgba("surface_container_highest", 0.6)};
    border: none;
    border-radius: {pill_radius(PROGRESS_HEIGHT)}px;
}}
QProgressBar::chunk {{
    background: {COLORS["primary_container"]};
    border-radius: {pill_radius(PROGRESS_HEIGHT)}px;
}}
"""


class BreakTimerBar(QFrame):
    finished = Signal()

    def __init__(self, clock: Callable[[], float] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clock = clock or time.time
        self._kind = BreakKind.SHORT
        self._started_at = 0.0
        self._remaining = 0
        self._finished = False

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

        self.setStyleSheet(STYLE)
        set_style_props(self, surface="card")
        self.title_label = QLabel()
        self.title_label.setFont(font("headline_sm", 15))
        self.title_label.setWordWrap(True)
        set_style_props(self.title_label, tone="default")
        self.time_label = QLabel()
        self.time_label.setFont(font("telemetry_metric", TIME_FONT_PX))
        set_style_props(self.time_label, tone="info")
        self.info_label = QLabel()
        self.info_label.setFont(font("body_sm"))
        set_style_props(self.info_label, tone="muted")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(PROGRESS_HEIGHT)

        layout = QGridLayout(self)
        layout.setContentsMargins(SPACING["md"], SPACING["sm"] + RADIUS["default"], SPACING["md"], SPACING["sm"] + RADIUS["default"])
        layout.setHorizontalSpacing(SPACING["md"])
        layout.setVerticalSpacing(SPACING["xs"])
        layout.setColumnStretch(0, 1)
        layout.addWidget(self.title_label, 0, 0)
        layout.addWidget(self.time_label, 0, 1, 2, 1)
        layout.addWidget(self.info_label, 1, 0)
        layout.addWidget(self.progress, 2, 0, 1, 2)

    @property
    def kind(self) -> BreakKind:
        return self._kind

    @property
    def remaining(self) -> int:
        """남은 휴식 시간(초, 올림)."""
        return self._remaining

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def is_finished(self) -> bool:
        return self._finished

    def start(self, kind: BreakKind, completed_sessions: int = 0) -> None:
        self._kind = kind
        self._started_at = self._clock()
        self._finished = False
        self._set_finished_look(False)
        self.title_label.setText(BREAK_TITLE[kind])
        self.info_label.setText(
            f"마친 세션 {completed_sessions}개 · {LONG_BREAK_EVERY}개마다 긴 휴식"
        )
        self.progress.setRange(0, BREAK_SECONDS[kind])
        self._timer.start()
        self._refresh()

    def stop(self) -> None:
        self._timer.stop()

    def tick(self) -> None:
        """QTimer가 부르며, 테스트에서는 clock을 움직인 뒤 직접 부른다."""
        if not self.is_running:
            return
        self._refresh()
        if self._remaining == 0:
            self._timer.stop()
            self._finished = True
            self.title_label.setText(FINISHED_TEXT)
            self._set_finished_look(True)
            self.finished.emit()

    def _refresh(self) -> None:
        total = BREAK_SECONDS[self._kind]
        elapsed = min(max(self._clock() - self._started_at, 0.0), total)
        self._remaining = math.ceil(total - elapsed)
        self.time_label.setText(format_mmss(self._remaining))
        self.progress.setValue(self._remaining)

    def _set_finished_look(self, finished: bool) -> None:
        set_style_props(self.title_label, tone="focus" if finished else "default")
        set_style_props(self.time_label, tone="focus" if finished else "info")
        self.info_label.setVisible(not finished)
