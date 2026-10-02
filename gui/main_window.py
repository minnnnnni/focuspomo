"""메인 윈도우와 화면 전환 구조 (개발계획서 1장, 2.4).

화면 흐름: 대기 → 캘리브레이션 → 타이머 → 리포트 → 대기
- 캘리브레이션이 세션 시작을 거부하면(직전 기준값도 없는 재실패) 대기로 돌아간다.
- 타이머는 25분이 끝나거나 "중간 종료"를 누르면 리포트로 간다.

화면은 QStackedWidget 한 장씩이고, 전환은 MainWindow의 go_*() 메서드로만 한다.
캘리브레이션·타이머·리포트 화면은 아직 placeholder이며
1-08(calibration_view), 1-11(timer_view), 1-14(report_view)에서 실제 화면으로 바꾼다.

    python -m gui.main_window      # 화면 전환만 확인
"""
from __future__ import annotations

import sys
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

WINDOW_TITLE = "FocusPomo"
WINDOW_MIN_SIZE = (480, 360)


class Screen(StrEnum):
    IDLE = "idle"
    CALIBRATION = "calibration"
    TIMER = "timer"
    REPORT = "report"


class IdleView(QWidget):
    """대기 화면: 세션 시작 버튼만 있다."""

    start_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel("FocusPomo")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: bold;")

        self.start_button = QPushButton("세션 시작")
        self.start_button.clicked.connect(self.start_requested)

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(title)
        layout.addSpacing(24)
        layout.addWidget(self.start_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()


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
    """화면 전환만 담당한다. 세션 데이터(SlotLog, 큐, 저장)는 이후 작업에서 붙인다."""

    screen_changed = Signal(str)  # Screen 값

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(*WINDOW_MIN_SIZE)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self.idle_view = IdleView()
        self.idle_view.start_requested.connect(self.go_calibration)

        # TODO(1-08): CalibrationView로 교체 (성공 → go_timer, 세션 시작 거부 → go_idle)
        self.calibration_view = PlaceholderView("캘리브레이션 (1-08에서 구현)")
        self.calibration_view.add_button("캘리브레이션 성공").clicked.connect(self.go_timer)
        self.calibration_view.add_button("세션 시작 거부").clicked.connect(self.go_idle)
        self.calibration_view.finish_layout()

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
        self._show(Screen.CALIBRATION)

    def go_timer(self) -> None:
        self._show(Screen.TIMER)

    def go_report(self) -> None:
        self._show(Screen.REPORT)

    def _show(self, screen: Screen) -> None:
        self._screen = screen
        self._stack.setCurrentWidget(self._views[screen])
        self.screen_changed.emit(screen.value)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
