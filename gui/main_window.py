"""메인 윈도우와 화면 전환 구조 (개발계획서 1장, 2.4).

화면 흐름: 대기 → 캘리브레이션 → 타이머 → 리포트 → 대기
- 캘리브레이션 첫 시도가 실패하면 한 번 더 응시를 요청한다.
- 재시도도 실패하면 직전 세션 ear_baseline으로 타이머에 가고,
  직전 값도 없으면 세션 시작을 거부하고 안내 문구와 함께 대기로 돌아간다.
- 타이머는 25분이 끝나거나 "중간 종료"를 누르면 리포트로 간다.

화면은 QStackedWidget 한 장씩이고, 전환은 MainWindow의 go_*() 메서드로만 한다.
타이머·리포트 화면은 아직 placeholder이며
1-11(timer_view), 1-14(report_view)에서 실제 화면으로 바꾼다.

    python -m gui.main_window                                   # 캘리브레이션 성공
    python -m gui.main_window --scenario fail_twice             # 거부 → 대기
    python -m gui.main_window --scenario fail_twice --previous-baseline 0.28   # 폴백
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.calibration import Calibrator, Outcome, decide
from gui.calibration_view import CalibrationView
from mocks.mock_calibration import SCENARIOS as CALIBRATION_SCENARIOS
from mocks.mock_calibration import MockCalibrator
from schemas import Calibration

WINDOW_TITLE = "FocusPomo"
WINDOW_MIN_SIZE = (480, 360)
REFUSED_NOTICE = "얼굴이 충분히 인식되지 않아 세션을 시작하지 않았어요.\n카메라를 확인하고 다시 시작해 주세요."


class Screen(StrEnum):
    IDLE = "idle"
    CALIBRATION = "calibration"
    TIMER = "timer"
    REPORT = "report"


class IdleView(QWidget):
    """대기 화면: 세션 시작 버튼과, 세션 시작이 거부됐을 때의 안내 문구."""

    start_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel("FocusPomo")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: bold;")

        self.start_button = QPushButton("세션 시작")
        self.start_button.clicked.connect(self.start_requested)

        self.notice_label = QLabel()
        self.notice_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice_label.setStyleSheet("color: #c0392b;")
        self.notice_label.hide()

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(title)
        layout.addSpacing(24)
        layout.addWidget(self.start_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(16)
        layout.addWidget(self.notice_label)
        layout.addStretch()

    def show_notice(self, text: str) -> None:
        self.notice_label.setText(text)
        self.notice_label.show()

    def clear_notice(self) -> None:
        self.notice_label.clear()
        self.notice_label.hide()


class PlaceholderView(QWidget):
    """아직 만들지 않은 화면 자리. 제목과 다음 화면으로 넘어가는 버튼만 보여준다."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 20px;")

        self._layout = QVBoxLayout(self)
        self._layout.addStretch()
        self._layout.addWidget(label)
        self._layout.addSpacing(16)
        self.buttons: dict[str, QPushButton] = {}

    def add_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        self._layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        self.buttons[text] = button
        return button

    def finish_layout(self) -> None:
        self._layout.addStretch()


class MainWindow(QMainWindow):
    """화면 전환과 캘리브레이션 판정을 담당한다. 세션 데이터(SlotLog, 큐, 저장)는 이후 작업에서 붙인다.

    calibrator: 캘리브레이션 시도 1회를 돌려주는 쪽. 기본은 항상 성공하는 mock (2-03에서 CV 스레드로 교체).
    load_previous_baseline: 직전 세션 ear_baseline을 읽는 함수. 기본은 "직전 값 없음"
        (1-20에서 storage 함수로 교체).
    """

    screen_changed = Signal(str)  # Screen 값

    def __init__(
        self,
        calibrator: Calibrator | None = None,
        load_previous_baseline: Callable[[], float | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._calibrator = calibrator or MockCalibrator("success")
        self._load_previous_baseline = load_previous_baseline or (lambda: None)
        self._attempt = 0
        self._attempt_started_at = 0.0
        self.calibration: Calibration | None = None  # 이번 세션에 쓸 결과 (성공 또는 폴백)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(*WINDOW_MIN_SIZE)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self.idle_view = IdleView()
        self.idle_view.start_requested.connect(self.go_calibration)

        self.calibration_view = CalibrationView()
        self.calibration_view.countdown_finished.connect(self._on_calibration_finished)
        self.calibration_view.cancel_requested.connect(self.go_idle)

        # TODO(1-11): TimerView로 교체 (25분 종료 / 중간 종료 → go_report)
        self.timer_view = PlaceholderView("타이머 (1-11에서 구현)")
        self.timer_view.add_button("중간 종료").clicked.connect(self.go_report)
        self.timer_view.finish_layout()

        # TODO(1-14): ReportView로 교체. 1-15에서 휴식 타이머를 리포트 뒤에 붙인다.
        self.report_view = PlaceholderView("리포트 (1-14에서 구현)")
        self.report_view.add_button("처음으로").clicked.connect(self.go_idle)
        self.report_view.finish_layout()

        self._views: dict[Screen, QWidget] = {
            Screen.IDLE: self.idle_view,
            Screen.CALIBRATION: self.calibration_view,
            Screen.TIMER: self.timer_view,
            Screen.REPORT: self.report_view,
        }
        for view in self._views.values():
            self._stack.addWidget(view)

        self._screen = Screen.IDLE
        self._stack.setCurrentWidget(self.idle_view)

    @property
    def screen(self) -> Screen:
        return self._screen

    def go_idle(self) -> None:
        self._show(Screen.IDLE)

    def go_calibration(self) -> None:
        self.idle_view.clear_notice()
        self.calibration = None
        self._attempt = 0
        self._show(Screen.CALIBRATION)
        self._attempt_started_at = time.time()
        self.calibration_view.start()

    def go_timer(self) -> None:
        self._show(Screen.TIMER)

    def go_report(self) -> None:
        self._show(Screen.REPORT)

    def _on_calibration_finished(self) -> None:
        self._attempt += 1
        # TODO(2-03): CV 스레드에서 결과를 받는 방식이 정해지면 교체 (지금은 mock이 바로 돌려준다)
        result = self._calibrator.run(self._attempt_started_at)
        decision = decide(result, self._attempt, self._load_previous_baseline())
        if decision.outcome is Outcome.RETRY:
            self._attempt_started_at = time.time()
            self.calibration_view.retry()
        elif decision.starts_session:
            # TODO(1-10): self.calibration["ended_at"]을 session_start로 확정
            self.calibration = decision.calibration
            self.go_timer()
        else:  # REFUSED
            self.go_idle()
            self.idle_view.show_notice(REFUSED_NOTICE)

    def _show(self, screen: Screen) -> None:
        if screen is not Screen.CALIBRATION:
            self.calibration_view.stop()
        self._screen = screen
        self._stack.setCurrentWidget(self._views[screen])
        self.screen_changed.emit(screen.value)


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusPomo 화면 흐름 확인 (mock 캘리브레이션)")
    parser.add_argument("--scenario", choices=tuple(CALIBRATION_SCENARIOS), default="success")
    parser.add_argument("--previous-baseline", type=float, default=None,
                        help="직전 세션 ear_baseline (없으면 첫 세션으로 본다)")
    args, qt_args = parser.parse_known_args()

    app = QApplication(sys.argv[:1] + qt_args)
    window = MainWindow(
        calibrator=MockCalibrator(args.scenario),
        load_previous_baseline=lambda: args.previous_baseline,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
