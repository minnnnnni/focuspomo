"""캘리브레이션 화면 (개발계획서 2.0).

"화면을 N초간 바라봐 주세요" 안내와 N초 카운트다운을 보여준다 (N = CALIBRATION_SEC).
EAR 측정은 CV 스레드(김시언)가 하고, 이 화면은 안내와 시간 흐름만 담당한다.

- start(): 기본 안내로 카운트다운을 처음부터 시작한다. 화면에 들어올 때마다 MainWindow가 부른다.
- retry(): 재시도 안내로 카운트다운을 다시 시작한다 (첫 시도 실패 시 MainWindow가 부른다).
- 0초가 되면 countdown_finished를 보낸다.
- "취소"를 누르면 카운트다운을 멈추고 cancel_requested를 보낸다.

결과 판정(성공/재시도/폴백/거부)은 core.calibration.decide()로 MainWindow가 한다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from config.aggregation import CALIBRATION_SEC

TICK_MS = 1000
START_MESSAGE = f"화면을 {CALIBRATION_SEC}초간 바라봐 주세요"
RETRY_MESSAGE = f"얼굴이 잘 보이지 않았어요.\n화면을 {CALIBRATION_SEC}초간 한 번 더 바라봐 주세요"


class CalibrationView(QWidget):
    countdown_finished = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._remaining = CALIBRATION_SEC

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

        self.message_label = QLabel(START_MESSAGE)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setStyleSheet("font-size: 20px;")

        self.countdown_label = QLabel()
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown_label.setStyleSheet("font-size: 48px; font-weight: bold;")

        self.cancel_button = QPushButton("취소")
        self.cancel_button.clicked.connect(self.cancel)

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self.message_label)
        layout.addSpacing(16)
        layout.addWidget(self.countdown_label)
        layout.addSpacing(24)
        layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

        self._update_countdown()

    @property
    def remaining(self) -> int:
        return self._remaining

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        self._begin(START_MESSAGE)

    def retry(self) -> None:
        self._begin(RETRY_MESSAGE)

    def _begin(self, message: str) -> None:
        self.message_label.setText(message)
        self._remaining = CALIBRATION_SEC
        self._update_countdown()
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

    def _update_countdown(self) -> None:
        self.countdown_label.setText(str(self._remaining))
