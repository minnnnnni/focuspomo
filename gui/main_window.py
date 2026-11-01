"""메인 윈도우와 화면 전환 구조 (개발계획서 1장, 2.4).

화면 흐름: 대기 → 캘리브레이션 → 타이머 → 리포트 → 대기
- 캘리브레이션은 시도마다 새 calib_id로 CameraThread에 명령을 보내고, 결과는 이벤트 큐로 받는다 (INT-03).
  현재 시도와 다른 calib_id는 버린다. 카운트다운이 끝나도 결과가 없으면 CALIBRATION_RESULT_TIMEOUT_MS까지
  기다리고, 끝내 안 오거나 측정 중 웹캠이 끊기면 시도를 중단하고 대기로 돌아간다 (재시도·폴백 없음).
- 웹캠이 열려 있을 때만 세션을 시작할 수 있다. 세션 중에 끊기면 세션은 계속한다 (그 구간은 None).
- 캘리브레이션 첫 시도가 실패하면 한 번 더 응시를 요청한다.
- 재시도도 실패하면 직전 세션 ear_baseline으로 타이머에 가고,
  직전 값도 없으면 세션 시작을 거부하고 안내 문구와 함께 대기로 돌아간다.
- 세션을 시작할 때 캘리브레이션 종료 시각(ended_at)을 session_start로 확정하고
  session_started로 알린 뒤 타이머로 간다 (캘리브레이션 시간은 집중 시간에 넣지 않는다).
- 타이머는 session_start부터 25분을 센다. 다 되거나 "세션 종료" 버튼으로 중간 종료하면 세션 종료 흐름으로 간다.
- 세션이 시작되면 SlotLog(session_start)를 새로 만들고, QTimer로 slot_queue를 SLOT_POLL_MS마다
  비워 반영한다 (위젯은 건드리지 않음). CV·창 스레드는 큐에 쓰기만 하고, 큐를 읽는 것은 메인 스레드뿐이다.
- 세션 종료 (1-13): session_end를 정하고 session_ending으로 알린 뒤, CV 마지막 슬롯 flush를
  FLUSH_WAIT_MS 동안 기다린다 (큐는 계속 비운다, 타이머 화면은 "결과 정리 중").
  기다린 뒤 큐를 마지막으로 비우고 core.session_end.finish_session()으로 슬롯 로그 확정 →
  정지 구간 슬롯 제외 → 점수 계산(지금은 mock)을 하고, data/에 저장한 다음 리포트로 간다.
  저장에 실패해도 리포트는 보여준다 (save_failed).
- 휴식 (1-15): 리포트가 곧 휴식 화면이다. 리포트로 가면서 짧은 휴식(5분)을, 25분을 끝까지 마친 세션이
  4개 쌓일 때마다 긴 휴식(15분)을 센다 (core.pomodoro). 휴식이 끝나면 알림음을 내고 창을 앞으로 올린다.
  다음 세션은 "다음 세션 시작"을 눌러야 시작한다 (캘리브레이션부터).

화면은 QStackedWidget 한 장씩이고, 전환은 MainWindow의 go_*() 메서드로만 한다.
미니 모드(3-06): 타이머 화면에서 메인 창을 숨기고 gui.mini_timer 창을 띄운다 (Alt+M, "미니 모드" 버튼).
  세션이 끝나면(타이머 종료·세션 종료) 메인 창으로 돌아온다.
대기 화면은 gui.idle_view에 있다.
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

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from core.calibration import Outcome, decide
from core.camera_link import CALIBRATION_RAW, CAMERA_FAILED, CAMERA_OPENED, CameraLink
from core.pomodoro import BreakKind, break_after, cycle_position
from core.session_end import ScoreFn, SessionOutput, finish_session
from core.slot_log import SlotLog, discard_queue, drain_queue
from gui.calibration_view import CalibrationView
from gui.idle_view import IdleView
from gui.mini_timer import TOGGLE_SHORTCUT, MiniTimerWindow
from gui.report_view import ReportView
from gui.style import APP_STYLE, load_fonts
from gui.timer_view import SESSION_SEC, TimerView
from mocks.mock_calibration import SCENARIOS as CALIBRATION_SCENARIOS
from mocks.mock_calibration import MockCalibrator
from mocks.mock_camera import InstantMockCamera
from mocks.mock_scoring import MockScorer
from mocks.mock_session import SCENARIOS as SLOT_SCENARIOS
from mocks.mock_session import build_scenario, feed_queue
from schemas import Calibration, SessionResult, pause_total
from storage import session_store
from storage.session_store import load_previous_baseline as load_previous_baseline_from_store

WINDOW_TITLE = "FocusPomo"
WINDOW_MIN_SIZE = (480, 420)
WINDOW_DEFAULT_SIZE = (580, 580)  # 대기 화면 디자인 기준 크기
SLOT_POLL_MS = 500  # 메시지는 슬롯당 2개(10초)라 0.5초면 충분하고 GUI 부담도 없다
FLUSH_WAIT_MS = 2000  # 세션 종료 후 CV 마지막 슬롯을 기다리는 시간 (임시값, 2.4)
CAMERA_POLL_MS = 100  # 캘리브레이션 결과를 카운트다운 끝에 바로 쓰도록 슬롯보다 자주 읽는다
CALIBRATION_RESULT_TIMEOUT_MS = 3000  # 카운트다운이 끝난 뒤 CV 결과를 기다리는 시간 (임시값)

logger = logging.getLogger(__name__)

REFUSED_NOTICE = "얼굴이 충분히 인식되지 않아 세션을 시작하지 않았어요.\n카메라를 확인하고 다시 시작해 주세요."
CAMERA_LOST_NOTICE = "웹캠 연결이 끊겨 캘리브레이션을 중단했어요.\n웹캠을 확인하고 다시 시작해 주세요."
CAMERA_NOT_READY_NOTICE = "웹캠이 준비되지 않았어요.\n웹캠을 확인한 뒤 시작해 주세요."
CALIBRATION_TIMEOUT_NOTICE = "캘리브레이션 결과를 받지 못했어요.\n잠시 후 다시 시작해 주세요."
SCORE_FAILED_NOTICE = "세션 결과를 계산하지 못했어요. 다시 시작해 주세요."


class Screen(StrEnum):
    IDLE = "idle"
    CALIBRATION = "calibration"
    TIMER = "timer"
    REPORT = "report"


class MainWindow(QMainWindow):
    """화면 전환, 캘리브레이션 판정, slot_queue 수신, 세션 종료·저장을 담당한다.

    camera: CameraThread와 주고받는 명령·이벤트 큐. 기본은 항상 성공하는 mock (CV-05 이후 main.py에서 실제 스레드와 연결).
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
        camera: CameraLink | None = None,
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
        self.camera: CameraLink = camera if camera is not None else InstantMockCamera()
        self._load_previous_baseline = load_previous_baseline or (
            lambda: load_previous_baseline_from_store(self.data_dir)
        )
        self._score_session: ScoreFn = score_session or MockScorer()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_WAIT_MS if flush_wait_ms is None else max(flush_wait_ms, 0))
        self._flush_timer.timeout.connect(self.complete_session)
        self._camera_timer = QTimer(self)
        self._camera_timer.setInterval(CAMERA_POLL_MS)
        self._camera_timer.timeout.connect(self.poll_camera_events)
        self._result_timer = QTimer(self)
        self._result_timer.setSingleShot(True)
        self._result_timer.setInterval(CALIBRATION_RESULT_TIMEOUT_MS)
        self._result_timer.timeout.connect(self._on_calibration_timeout)
        self._attempt = 0
        self._last_calib_id = 0
        self._calib_id: int | None = None  # 진행 중인 시도. 없으면 None
        self._pending_raw: Calibration | None = None  # 카운트다운이 끝나기 전에 먼저 온 결과
        self._countdown_done = False  # 이번 시도의 카운트다운이 끝났는지
        self.calibration: Calibration | None = None  # 이번 세션에 쓸 결과 (성공 또는 폴백)
        self.session_start: float | None = None  # = calibration["ended_at"]. 세션 시작 전에는 None
        self.session_end: float | None = None  # 타이머가 끝나거나 중간 종료한 시각. 종료 전에는 None
        self.session_output: SessionOutput | None = None  # 점수 계산까지 끝난 결과. 리포트 화면이 쓴다
        self.save_failed = False  # 이번 세션 기록 저장에 실패했는지 (리포트에서 안내)
        self._session_completed = False  # 이번 세션을 25분 끝까지 마쳤는지 (중간 종료면 False)
        self.completed_sessions = 0  # 앱을 켠 뒤 끝까지 마친 세션 수 (긴 휴식 판단용)
        self.break_kind: BreakKind | None = None  # 지금 리포트에서 세는 휴식 종류
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(*WINDOW_MIN_SIZE)
        self.resize(*WINDOW_DEFAULT_SIZE)
        self.setStyleSheet(APP_STYLE)  # 공통 컴포넌트 (동적 속성을 정한 위젯에만 적용)

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
        self.timer_view.mini_mode_requested.connect(self.enter_mini_mode)

        # 미니 모드 창: 메인 창과 따로 떠 있는 최상위 창 (부모를 두지 않아 메인 창을 숨겨도 남는다)
        self.mini_window = MiniTimerWindow(self.timer_view)
        self.mini_window.restore_requested.connect(self.exit_mini_mode)
        QShortcut(QKeySequence(TOGGLE_SHORTCUT), self, activated=self.enter_mini_mode)

        self.report_view = ReportView()
        self.report_view.home_requested.connect(self.go_idle)
        self.report_view.next_session_requested.connect(self.go_calibration)
        self.report_view.break_bar.finished.connect(self._on_break_finished)

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

        self.poll_camera_events()
        self._camera_timer.start()

    @property
    def screen(self) -> Screen:
        return self._screen

    def go_idle(self) -> None:
        self.idle_view.set_completed_sessions(self.completed_sessions)
        self._show(Screen.IDLE)

    def go_calibration(self) -> None:
        self.poll_camera_events()
        if self.idle_view.camera_ready is not True:  # 리포트의 "다음 세션 시작"은 시작 버튼을 거치지 않는다
            self.go_idle()
            self.idle_view.show_notice(CAMERA_NOT_READY_NOTICE)
            return
        self.idle_view.clear_notice()
        self.calibration = None
        self.session_start = None
        self.session_end = None
        self.session_output = None
        self.save_failed = False
        self._session_completed = False
        self.break_kind = None
        self.report_view.clear()
        self.slot_log = None
        self._attempt = 0
        self._show(Screen.CALIBRATION)
        self._begin_calibration_attempt()
        self.calibration_view.start()

    def go_timer(self) -> None:
        if self.session_start is None:
            raise RuntimeError("session_start가 정해지기 전에는 타이머를 시작할 수 없습니다")
        self.timer_view.set_session_number(cycle_position(self.completed_sessions))
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
        """세션을 끝낸다 ("세션 종료" 버튼 또는 타이머 종료). flush를 기다린 뒤 complete_session()으로 이어진다.

        session_end를 주지 않으면 지금 시각. 세션 중이 아니거나 이미 끝내는 중이면 무시한다.
        """
        if self._screen is not Screen.TIMER or self.session_end is not None:
            return
        self.session_end = time.time() if session_end is None else session_end
        self.timer_view.show_finishing()  # 정지 중이었으면 정지 구간도 여기서 닫힌다
        self.exit_mini_mode()  # 결과 정리·리포트는 메인 창에서 보여 준다
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
        if self._session_completed:
            self.completed_sessions += 1
        self.break_kind = break_after(self._session_completed, self.completed_sessions)
        self.session_finished.emit(output.result)
        self.go_report()
        self.report_view.break_bar.start(self.break_kind, self.completed_sessions)

    @property
    def session_result(self) -> SessionResult | None:
        return None if self.session_output is None else self.session_output.result

    def _on_time_up(self) -> None:
        # 타이머가 tick을 늦게 받아도 슬롯이 하나 더 생기지 않게 이론상 종료 시각을 쓴다
        assert self.session_start is not None
        pauses = self.timer_view.pause_intervals
        self._session_completed = True
        self.end_session(self.session_start + SESSION_SEC + pause_total(pauses))

    def _on_break_finished(self) -> None:
        QApplication.beep()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

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
            logger.warning("쓸 수 없는 slot_queue 메시지 %d개를 건너뜀", skipped)
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

    def poll_camera_events(self) -> None:
        """카메라 이벤트를 비워 반영한다. QTimer가 부르고, 테스트에서는 직접 부른다."""
        for event in self.camera.drain_events():
            kind = event["type"]
            if kind == CAMERA_OPENED:
                self.idle_view.set_camera_ready(True)
            elif kind == CAMERA_FAILED:
                self._on_camera_failed(event["reason"])
            elif kind == CALIBRATION_RAW:
                self._on_calibration_raw(event)

    def _on_camera_failed(self, reason: object) -> None:
        logger.warning("웹캠을 쓸 수 없습니다: %s", reason)
        self.idle_view.set_camera_ready(False)
        if self._calib_id is not None:
            self._calib_id = None  # 끊긴 시도는 CV가 취소 처리했으므로 명령을 보내지 않는다
            self.go_idle()
            self.idle_view.show_notice(CAMERA_LOST_NOTICE)

    def _on_calibration_raw(self, event: dict) -> None:
        if event["calib_id"] != self._calib_id:
            logger.info("현재 시도가 아닌 캘리브레이션 결과를 버림: %s", event["calib_id"])
            return
        self._pending_raw = event["raw"]
        if self._countdown_done:
            self._apply_calibration_result()

    def _begin_calibration_attempt(self) -> None:
        self._attempt += 1
        self._last_calib_id += 1
        self._calib_id = self._last_calib_id
        self._pending_raw = None
        self._countdown_done = False
        self.camera.start_calibration(self._calib_id, self._attempt)

    def _end_calibration_attempt(self) -> None:
        """캘리브레이션 화면을 떠날 때 부른다. 결과를 받지 못한 시도가 남아 있으면 취소한다."""
        self._result_timer.stop()
        if self._calib_id is not None:
            self.camera.cancel_calibration(self._calib_id)
            self._calib_id = None

    def _on_calibration_finished(self) -> None:
        """카운트다운이 끝났다. 결과가 왔으면 판정하고, 아니면 올 때까지 기다린다."""
        self.poll_camera_events()
        if self._calib_id is None:
            return  # 방금 이벤트에서 웹캠이 끊겨 시도가 중단됐다
        self._countdown_done = True
        if self._pending_raw is not None:
            self._apply_calibration_result()
        else:
            self.calibration_view.show_waiting()
            self._result_timer.start()

    def _on_calibration_timeout(self) -> None:
        logger.warning("캘리브레이션 결과가 오지 않아 시도를 중단합니다: %s", self._calib_id)
        self.go_idle()  # 받지 못한 시도는 여기서 취소된다
        self.idle_view.show_notice(CALIBRATION_TIMEOUT_NOTICE)

    def _apply_calibration_result(self) -> None:
        assert self._pending_raw is not None
        self._result_timer.stop()
        decision = decide(self._pending_raw, self._attempt, self._load_previous_baseline())
        if decision.outcome is Outcome.RETRY:
            self._begin_calibration_attempt()
            self.calibration_view.retry()
            return
        self._calib_id = None  # 결과를 받았으니 이 시도는 취소할 것이 없다
        if decision.starts_session:
            # 폴백도 마지막 시도의 ended_at을 그대로 쓴다 (decide()는 기준값만 바꾼다)
            self.calibration = decision.calibration
            self.session_start = self.calibration["ended_at"]
            self._start_slot_polling(self.session_start)
            self.session_started.emit(self.session_start)
            self.go_timer()
        else:  # REFUSED
            self.go_idle()
            self.idle_view.show_notice(REFUSED_NOTICE)

    @property
    def is_mini_mode(self) -> bool:
        return self.mini_window.isVisible()

    def enter_mini_mode(self) -> None:
        """메인 창을 숨기고 미니 모드 창을 띄운다. 세션 진행 중(타이머 화면, 결과 정리 전)에만."""
        if self._screen is not Screen.TIMER or self.timer_view.is_finishing or self.is_mini_mode:
            return
        self.mini_window.sync()
        self.mini_window.place_near(self)
        self.hide()
        self.mini_window.show()
        self.mini_window.raise_()
        self.mini_window.activateWindow()  # Space·Alt+M 단축키를 바로 받게

    def exit_mini_mode(self) -> None:
        """미니 모드 창을 닫고 메인 창을 다시 띄운다. 미니 모드가 아니면 아무것도 하지 않는다."""
        if not self.is_mini_mode:
            return
        self.mini_window.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self.mini_window.hide()  # 미니 창이 남아 앱이 안 꺼지는 일이 없게
        super().closeEvent(event)

    def _show(self, screen: Screen) -> None:
        if screen is not Screen.TIMER:
            self.exit_mini_mode()  # 세션이 끝나 다른 화면으로 가면 메인 창으로 돌아와 보여 준다
        if screen is not Screen.CALIBRATION:
            self.calibration_view.stop()
            self._end_calibration_attempt()
        if screen is not Screen.TIMER:
            self.timer_view.stop()
        if screen is not Screen.REPORT:
            self.report_view.break_bar.stop()
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
    load_fonts()
    window = MainWindow(
        camera=InstantMockCamera(MockCalibrator(args.scenario)),
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
