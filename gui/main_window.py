"""메인 윈도우와 화면 전환 구조 (개발계획서 1장, 2.4).

화면 흐름: 대기 → 캘리브레이션 → 타이머 → 리포트 → 대기
- 캘리브레이션 첫 시도가 실패하면 한 번 더 응시를 요청한다.
- 재시도도 실패하면 직전 세션 ear_baseline으로 타이머에 가고,
  직전 값도 없으면 세션 시작을 거부하고 안내 문구와 함께 대기로 돌아간다.
- 세션을 시작할 때 캘리브레이션 종료 시각(ended_at)을 session_start로 확정하고
  session_started로 알린 뒤 타이머로 간다 (캘리브레이션 시간은 집중 시간에 넣지 않는다).
- 타이머는 session_start부터 25분을 센다. 다 되거나 "중간 종료"를 누르면 세션 종료 흐름으로 간다.
- 세션이 시작되면 SlotLog(session_start)를 새로 만들고, QTimer로 slot_queue를 SLOT_POLL_MS마다
  비워 반영한다 (위젯은 건드리지 않음). CV·창 스레드는 큐에 쓰기만 하고, 큐를 읽는 것은 메인 스레드뿐이다.
- 세션 종료 (1-13): session_end를 정하고 session_ending으로 알린 뒤, CV 마지막 슬롯 flush를
  FLUSH_WAIT_MS 동안 기다린다 (큐는 계속 비운다, 타이머 화면은 "결과 정리 중").
  기다린 뒤 큐를 마지막으로 비우고 core.session_end.finish_session()으로 슬롯 로그 확정 →
  정지 구간 슬롯 제외 → 점수 계산(지금은 mock)을 하고, data/에 저장한 다음 리포트로 간다.
  저장에 실패해도 리포트는 보여준다 (save_failed).

화면은 QStackedWidget 한 장씩이고, 전환은 MainWindow의 go_*() 메서드로만 한다.
리포트 화면(gui.report_view)은 complete_session()에서 SessionResult를 받아 채운다.

    python -m gui.main_window                                   # 캘리브레이션 성공
    python -m gui.main_window --scenario fail_twice             # data/sessions.jsonl에 직전 값이 없으면 거부 → 대기
    python -m gui.main_window --scenario fail_twice --previous-baseline 0.28   # 폴백
    python -m gui.main_window --slots normal --speed 10       # mock 슬롯 메시지 수신 (콘솔에 로그)
    python -m gui.main_window --slots normal --llm-fail       # 점수 mock을 LLM 실패 폴백으로
    python -m gui.main_window --data-dir C:\\temp\\fp-data      # 개발 중 기록을 다른 폴더에 저장
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
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
from core.session_end import ScoreFn, SessionOutput, finish_session
from core.slot_log import SlotLog, discard_queue, drain_queue
from gui.calibration_view import CalibrationView
from gui.report_view import ReportView
from gui.timer_view import SESSION_SEC, TimerView
from mocks.mock_calibration import SCENARIOS as CALIBRATION_SCENARIOS
from mocks.mock_calibration import MockCalibrator
from mocks.mock_scoring import MockScorer
from mocks.mock_session import SCENARIOS as SLOT_SCENARIOS
from mocks.mock_session import build_scenario, feed_queue
from schemas import Calibration, SessionResult, pause_total
from storage import session_store
from storage.session_store import load_previous_baseline as load_previous_baseline_from_store

WINDOW_TITLE = "FocusPomo"
WINDOW_MIN_SIZE = (480, 360)
SLOT_POLL_MS = 500  # 메시지는 슬롯당 2개(10초)라 0.5초면 충분하고 GUI 부담도 없다
FLUSH_WAIT_MS = 2000  # 세션 종료 후 CV 마지막 슬롯을 기다리는 시간 (임시값, 2.4)

logger = logging.getLogger(__name__)

REFUSED_NOTICE = "얼굴이 충분히 인식되지 않아 세션을 시작하지 않았어요.\n카메라를 확인하고 다시 시작해 주세요."
SCORE_FAILED_NOTICE = "세션 결과를 계산하지 못했어요. 다시 시작해 주세요."


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


class MainWindow(QMainWindow):
    """화면 전환, 캘리브레이션 판정, slot_queue 수신, 세션 종료·저장을 담당한다.

    calibrator: 캘리브레이션 시도 1회를 돌려주는 쪽. 기본은 항상 성공하는 mock (2-03에서 CV 스레드로 교체).
    load_previous_baseline: 직전 세션 ear_baseline을 읽는 함수.
        기본은 data_dir/sessions.jsonl의 마지막 세션 값 (storage.session_store).
    slot_queue: CV·창 스레드가 SlotMessage를 넣는 큐. 기본은 새 queue.Queue (2-01에서 main.py가 만들어 넘긴다).
    score_session: 점수 모듈. 기본은 mocks.mock_scoring.MockScorer (2-05에서 실제 모듈로 교체).
    data_dir: 기록 저장 폴더. 기본은 storage.session_store.DATA_DIR (레포의 data/).
    flush_wait_ms: 세션 종료 후 CV 마지막 슬롯을 기다리는 시간. 기본 FLUSH_WAIT_MS, 0이면 기다리지 않는다.
    """

    screen_changed = Signal(str)  # Screen 값
    session_started = Signal(float)  # session_start. 2-02에서 CV·창 스레드 공유에 쓴다
    slots_received = Signal(int)  # 큐를 비워 메시지를 하나 이상 반영했을 때, 메시지가 온 슬롯 수
    session_ending = Signal(float)  # session_end. 2-03에서 CV 스레드에 마지막 슬롯 flush를 알리는 데 쓴다
    session_finished = Signal(object)  # 점수 계산까지 끝난 SessionResult (리포트로 가기 직전)

    def __init__(
        self,
        calibrator: Calibrator | None = None,
        load_previous_baseline: Callable[[], float | None] | None = None,
        slot_queue: queue.Queue | None = None,
        score_session: ScoreFn | None = None,
        data_dir: Path | None = None,
        flush_wait_ms: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.slot_queue: queue.Queue = slot_queue if slot_queue is not None else queue.Queue()
        self.slot_log: SlotLog | None = None  # 세션이 시작될 때 만든다
        self._slot_timer = QTimer(self)
        self._slot_timer.setInterval(SLOT_POLL_MS)
        self._slot_timer.timeout.connect(self.drain_slot_queue)
        self.data_dir: Path = data_dir if data_dir is not None else session_store.DATA_DIR
        self._calibrator = calibrator or MockCalibrator("success")
        self._load_previous_baseline = load_previous_baseline or (
            lambda: load_previous_baseline_from_store(self.data_dir)
        )
        self._score_session: ScoreFn = score_session or MockScorer()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_WAIT_MS if flush_wait_ms is None else max(flush_wait_ms, 0))
        self._flush_timer.timeout.connect(self.complete_session)
        self._attempt = 0
        self._attempt_started_at = 0.0
        self.calibration: Calibration | None = None  # 이번 세션에 쓸 결과 (성공 또는 폴백)
        self.session_start: float | None = None  # = calibration["ended_at"]. 세션 시작 전에는 None
        self.session_end: float | None = None  # 타이머가 끝나거나 중간 종료한 시각. 종료 전에는 None
        self.session_output: SessionOutput | None = None  # 점수 계산까지 끝난 결과. 리포트 화면이 쓴다
        self.save_failed = False  # 이번 세션 기록 저장에 실패했는지 (리포트에서 안내)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(*WINDOW_MIN_SIZE)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self.idle_view = IdleView()
        self.idle_view.start_requested.connect(self.go_calibration)

        self.calibration_view = CalibrationView()
        self.calibration_view.countdown_finished.connect(self._on_calibration_finished)
        self.calibration_view.cancel_requested.connect(self.go_idle)

        self.timer_view = TimerView()
        self.timer_view.time_up.connect(self._on_time_up)
        self.timer_view.end_requested.connect(self.end_session)

        self.report_view = ReportView()
        self.report_view.home_requested.connect(self.go_idle)

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
        self.session_start = None
        self.session_end = None
        self.session_output = None
        self.save_failed = False
        self.report_view.clear()
        self.slot_log = None
        self._attempt = 0
        self._show(Screen.CALIBRATION)
        self._attempt_started_at = time.time()
        self.calibration_view.start()

    def go_timer(self) -> None:
        if self.session_start is None:
            raise RuntimeError("session_start가 정해지기 전에는 타이머를 시작할 수 없습니다")
        self._show(Screen.TIMER)
        self.timer_view.start(self.session_start)

    def go_report(self) -> None:
        self._stop_slot_polling()
        self._show(Screen.REPORT)

    @property
    def is_finishing(self) -> bool:
        """세션이 끝나 CV flush를 기다리는 중인지."""
        return self._flush_timer.isActive()

    def end_session(self, session_end: float | None = None) -> None:
        """세션을 끝낸다 ("중간 종료" 또는 타이머 종료). flush를 기다린 뒤 complete_session()으로 이어진다.

        session_end를 주지 않으면 지금 시각. 세션 중이 아니거나 이미 끝내는 중이면 무시한다.
        """
        if self._screen is not Screen.TIMER or self.session_end is not None:
            return
        self.session_end = time.time() if session_end is None else session_end
        self.timer_view.show_finishing()  # 정지 중이었으면 정지 구간도 여기서 닫힌다
        self.session_ending.emit(self.session_end)
        if self._flush_timer.interval() == 0:
            self.complete_session()
        else:
            self._flush_timer.start()

    def complete_session(self) -> None:
        """flush 대기가 끝나면 부른다: 마지막 drain → 점수 계산 → 저장 → 리포트."""
        self._flush_timer.stop()
        if self.session_end is None or self.slot_log is None or self.calibration is None:
            return
        if self.session_output is not None:
            return  # 이미 처리함
        self._stop_slot_polling()
        try:
            output = finish_session(
                self.slot_log, self.calibration, self.timer_view.pause_intervals, self.session_end,
                self._score_session,
            )
        except Exception:
            # 점수 모듈 오류로 "결과 정리 중"에 멈추지 않게 대기로 돌아가 알린다
            logger.exception("세션 결과를 계산하지 못했습니다")
            self.go_idle()
            self.idle_view.show_notice(SCORE_FAILED_NOTICE)
            return
        if output.excluded_slots:
            logger.info("정지 구간과 겹친 슬롯 %d개를 점수에서 뺌: %s", len(output.excluded_slots), output.excluded_slots)
        self.session_output = output
        self.save_failed = not self._save(output)
        self.report_view.show_result(output.result, save_failed=self.save_failed,
                                     excluded_slots=len(output.excluded_slots))
        self.session_finished.emit(output.result)
        self.go_report()

    @property
    def session_result(self) -> SessionResult | None:
        return None if self.session_output is None else self.session_output.result

    def _on_time_up(self) -> None:
        # 타이머가 tick을 늦게 받아도 슬롯이 하나 더 생기지 않게 이론상 종료 시각을 쓴다
        assert self.session_start is not None
        pauses = self.timer_view.pause_intervals
        self.end_session(self.session_start + SESSION_SEC + pause_total(pauses))

    def _save(self, output: SessionOutput) -> bool:
        """sessions.jsonl과 focus_log를 저장한다. 하나라도 실패하면 로그를 남기고 False."""
        ok = True
        try:
            session_store.append_session(output.result, self.data_dir)
        except (OSError, ValueError, TypeError):
            logger.exception("sessions.jsonl 저장 실패: %s", output.result.get("session_id"))
            ok = False
        try:
            session_store.save_focus_log(output.focus_log, self.data_dir)
        except (OSError, ValueError, TypeError):
            logger.exception("focus_log 저장 실패: %s", output.focus_log.get("session_id"))
            ok = False
        return ok

    def drain_slot_queue(self) -> int:
        """slot_queue에 쌓인 메시지를 SlotLog에 반영한다. QTimer가 부르고, 테스트에서는 직접 부른다.

        세션 중이 아니면(slot_log가 None) 아무것도 하지 않는다. 반영한 메시지 수를 돌려준다.
        """
        if self.slot_log is None:
            return 0
        added, skipped = drain_queue(self.slot_queue, self.slot_log)
        if skipped:
            logger.warning("잘못된 slot_queue 메시지 %d개를 건너뜀", skipped)
        if added:
            self.slots_received.emit(len(self.slot_log))
        return added

    @property
    def is_receiving_slots(self) -> bool:
        return self._slot_timer.isActive()

    def _start_slot_polling(self, session_start: float) -> None:
        # 이전 세션(중간 종료 뒤 늦게 온 flush 등) 메시지는 슬롯 번호 기준이 달라 섞으면 안 된다
        dropped = discard_queue(self.slot_queue)
        if dropped:
            logger.info("새 세션 시작 전 남아 있던 slot_queue 메시지 %d개를 버림", dropped)
        self.slot_log = SlotLog(session_start)
        self._slot_timer.start()

    def _stop_slot_polling(self) -> None:
        if not self._slot_timer.isActive():
            return
        self._slot_timer.stop()
        self.drain_slot_queue()

    def _on_calibration_finished(self) -> None:
        self._attempt += 1
        # TODO(2-03): CV 스레드에서 결과를 받는 방식이 정해지면 교체 (지금은 mock이 바로 돌려준다)
        result = self._calibrator.run(self._attempt_started_at)
        decision = decide(result, self._attempt, self._load_previous_baseline())
        if decision.outcome is Outcome.RETRY:
            self._attempt_started_at = time.time()
            self.calibration_view.retry()
        elif decision.starts_session:
            # 폴백도 마지막 시도의 ended_at을 그대로 쓴다 (decide()는 기준값만 바꾼다)
            self.calibration = decision.calibration
            self.session_start = self.calibration["ended_at"]
            self._start_slot_polling(self.session_start)
            self.session_started.emit(self.session_start)
            self.go_timer()
        else:  # REFUSED
            self.go_idle()
            self.idle_view.show_notice(REFUSED_NOTICE)

    def _show(self, screen: Screen) -> None:
        if screen is not Screen.CALIBRATION:
            self.calibration_view.stop()
        if screen is not Screen.TIMER:
            self.timer_view.stop()
        if screen is Screen.IDLE:
            self._flush_timer.stop()
            self._stop_slot_polling()
        self._screen = screen
        self._stack.setCurrentWidget(self._views[screen])
        self.screen_changed.emit(screen.value)


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusPomo 화면 흐름 확인 (mock 캘리브레이션)")
    parser.add_argument("--scenario", choices=tuple(CALIBRATION_SCENARIOS), default="success")
    parser.add_argument("--previous-baseline", type=float, default=None,
                        help="직전 세션 ear_baseline (주지 않으면 data/sessions.jsonl에서 읽는다)")
    parser.add_argument("--slots", choices=SLOT_SCENARIOS, default=None,
                        help="세션이 시작되면 이 mock 시나리오의 슬롯 메시지를 slot_queue에 넣는다")
    parser.add_argument("--speed", type=float, default=1.0, help="--slots 재생 배속 (10이면 10배 빠르게)")
    parser.add_argument("--llm-fail", action="store_true", help="점수 mock이 LLM 실패 폴백 결과를 만든다")
    parser.add_argument("--data-dir", type=Path, default=None, help="기록 저장 폴더 (기본: 레포의 data/)")
    args, qt_args = parser.parse_known_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = QApplication(sys.argv[:1] + qt_args)
    window = MainWindow(
        calibrator=MockCalibrator(args.scenario),
        load_previous_baseline=(
            (lambda: args.previous_baseline) if args.previous_baseline is not None else None
        ),
        score_session=MockScorer(llm_fail=args.llm_fail),  # TODO(2-05): 실제 점수 모듈로 교체
        data_dir=args.data_dir,
    )
    if args.slots is not None:
        # TODO(2-03, 2-04): 실제 CV·창 스레드로 교체
        feeder_stop = threading.Event()

        def start_feeder(start: float) -> None:
            nonlocal feeder_stop
            feeder_stop.set()  # 이전 세션 feeder가 새 세션 큐에 넣지 않게 멈춘다
            feeder_stop = threading.Event()
            feed_queue(window.slot_queue, build_scenario(args.slots, start), start,
                       speed=args.speed, stop_event=feeder_stop)

        window.session_started.connect(start_feeder)
        window.slots_received.connect(lambda n: logger.info("메시지가 온 슬롯 %d개", n))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
