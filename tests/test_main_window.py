"""gui/main_window.py 화면 전환 테스트 (화면 없이 offscreen으로 실행).

모든 테스트는 임시 data 폴더를 쓰고(레포의 data/에 쓰지 않음), 따로 주지 않으면 flush 대기 없이 바로 끝낸다.
"""
import json
import os
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from config.aggregation import CALIBRATION_SEC  # noqa: E402
from core.pomodoro import LONG_BREAK_EVERY, BreakKind  # noqa: E402
from gui.calibration_view import RETRY_MESSAGE  # noqa: E402
from core.slot_log import SlotLog  # noqa: E402
from gui.calibration_view import WAITING_MESSAGE  # noqa: E402
from gui.main_window import (  # noqa: E402
    CALIBRATION_TIMEOUT_NOTICE,
    CAMERA_LOST_NOTICE,
    CAMERA_NOT_READY_NOTICE,
    REFUSED_NOTICE,
    SCORE_FAILED_NOTICE,
    SLOT_POLL_MS,
    MainWindow,
    Screen,
)
from gui.timer_view import SESSION_SEC  # noqa: E402
from core.camera_link import (  # noqa: E402
    CALIBRATION_RAW,
    CAMERA_FAILED,
    CAMERA_OPENED,
    CANCEL_CALIBRATION,
    START_CALIBRATION,
    CameraLink,
)
from mocks.mock_calibration import MockCalibrator, make_calibration  # noqa: E402
from mocks.mock_camera import InstantMockCamera  # noqa: E402
from mocks.mock_scoring import MockScorer  # noqa: E402
from mocks.mock_session import build_scenario, messages_only  # noqa: E402
from schemas import SLOT_SECONDS, make_session_id  # noqa: E402
from storage import session_store  # noqa: E402

app = QApplication.instance() or QApplication([])

class WindowTestCase(unittest.TestCase):
    """테스트마다 새 임시 data 폴더를 기본 저장 위치로 쓰고, flush 대기는 0으로 둔다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        for p in (patch.object(session_store, "DATA_DIR", self.data_dir),
                  patch("gui.main_window.FLUSH_WAIT_MS", 0)):
            p.start()
            self.addCleanup(p.stop)


class ScreenFlowTest(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window = MainWindow()
        self.changes: list[str] = []
        self.window.screen_changed.connect(self.changes.append)

    def tearDown(self):
        self.window.deleteLater()

    def current(self):
        return self.window._stack.currentWidget()

    def test_starts_idle(self):
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertIs(self.current(), self.window.idle_view)

    def test_full_cycle_by_buttons(self):
        self.window.idle_view.start_button.click()
        self.assertEqual(self.window.screen, Screen.CALIBRATION)
        for _ in range(CALIBRATION_SEC):
            self.window.calibration_view.tick()
        self.assertEqual(self.window.screen, Screen.TIMER)
        self.window.timer_view.end_button.click()
        self.assertEqual(self.window.screen, Screen.REPORT)
        self.assertIs(self.current(), self.window.report_view)
        self.window.report_view.home_button.click()
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertEqual(self.changes, ["calibration", "timer", "report", "idle"])

    def test_calibration_cancel_returns_idle(self):
        self.window.go_calibration()
        self.window.calibration_view.cancel_button.click()
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertIs(self.current(), self.window.idle_view)
        self.assertFalse(self.window.calibration_view.is_running)

    def test_entering_calibration_starts_countdown(self):
        self.window.go_calibration()
        self.assertTrue(self.window.calibration_view.is_running)
        self.assertEqual(self.window.calibration_view.remaining, CALIBRATION_SEC)

    def test_leaving_calibration_stops_countdown(self):
        self.window.go_calibration()
        self.window.go_idle()
        self.assertFalse(self.window.calibration_view.is_running)


class TimerFlowTest(WindowTestCase):
    """타이머 화면 연결 (1-11)."""

    def setUp(self):
        super().setUp()
        self.window = MainWindow(camera=InstantMockCamera(MockCalibrator("success")), load_previous_baseline=lambda: None)
        self.addCleanup(self.window.deleteLater)
        self.window.go_calibration()
        for _ in range(CALIBRATION_SEC):
            self.window.calibration_view.tick()

    def test_timer_starts_from_session_start(self):
        self.assertEqual(self.window.screen, Screen.TIMER)
        self.assertTrue(self.window.timer_view.is_running)
        self.assertEqual(self.window.timer_view._session_start, self.window.session_start)

    def test_time_up_goes_to_report(self):
        self.window.timer_view.time_up.emit()
        self.assertEqual(self.window.screen, Screen.REPORT)

    def test_end_button_goes_to_report_and_stops(self):
        self.window.timer_view.end_button.click()
        self.assertEqual(self.window.screen, Screen.REPORT)
        self.assertFalse(self.window.timer_view.is_running)

    def test_leaving_timer_stops_countdown(self):
        self.window.go_idle()
        self.assertFalse(self.window.timer_view.is_running)

    def test_leaving_while_paused_closes_pause(self):
        self.window.timer_view.pause()
        self.window.go_idle()
        self.assertFalse(self.window.timer_view.is_paused)
        self.assertEqual(len(self.window.timer_view.pause_intervals), 1)

    def test_go_timer_without_session_start_raises(self):
        self.window.go_idle()
        self.window.go_calibration()  # session_start가 None으로 초기화된다
        with self.assertRaises(RuntimeError):
            self.window.go_timer()


class CalibrationResultTest(WindowTestCase):
    """mock 캘리브레이션 시나리오별 화면 흐름 (1-09)."""

    PREVIOUS = 0.28

    def make_window(self, scenario: str, previous: float | None = None) -> MainWindow:
        window = MainWindow(camera=InstantMockCamera(MockCalibrator(scenario)), load_previous_baseline=lambda: previous)
        self.addCleanup(window.deleteLater)
        return window

    def finish_countdown(self, window: MainWindow) -> None:
        for _ in range(CALIBRATION_SEC):
            window.calibration_view.tick()

    def test_success_goes_to_timer(self):
        w = self.make_window("success")
        w.go_calibration()
        self.finish_countdown(w)
        self.assertEqual(w.screen, Screen.TIMER)
        self.assertFalse(w.calibration["fallback_used"])

    def test_retry_then_success(self):
        w = self.make_window("retry_success")
        w.go_calibration()
        self.finish_countdown(w)
        self.assertEqual(w.screen, Screen.CALIBRATION)
        self.assertEqual(w.calibration_view.message_label.text(), RETRY_MESSAGE)
        self.assertTrue(w.calibration_view.is_running)
        self.assertIsNone(w.calibration)
        self.finish_countdown(w)
        self.assertEqual(w.screen, Screen.TIMER)
        self.assertFalse(w.calibration["fallback_used"])

    def test_fail_twice_with_previous_falls_back(self):
        w = self.make_window("fail_twice", self.PREVIOUS)
        w.go_calibration()
        self.finish_countdown(w)
        self.finish_countdown(w)
        self.assertEqual(w.screen, Screen.TIMER)
        self.assertTrue(w.calibration["fallback_used"])
        self.assertEqual(w.calibration["ear_baseline"], self.PREVIOUS)

    def test_fail_twice_without_previous_refuses(self):
        w = self.make_window("no_face")
        w.go_calibration()
        self.finish_countdown(w)
        self.finish_countdown(w)
        self.assertEqual(w.screen, Screen.IDLE)
        self.assertIsNone(w.calibration)
        self.assertFalse(w.calibration_view.is_running)
        self.assertFalse(w.idle_view.notice_label.isHidden())
        self.assertEqual(w.idle_view.notice_label.text(), REFUSED_NOTICE)

    def test_default_reads_previous_from_store(self):
        # 주입하지 않으면 data_dir/sessions.jsonl에서 직전 값을 읽는다 (1-20)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data_dir = Path(tmp.name)
        session_store.sessions_path(data_dir).write_text(
            json.dumps({"session_id": "20261003-100000", "ear_baseline": self.PREVIOUS}) + "\n", encoding="utf-8")
        w = MainWindow(camera=InstantMockCamera(MockCalibrator("fail_twice")), data_dir=data_dir)
        self.addCleanup(w.deleteLater)
        w.go_calibration()
        self.finish_countdown(w)
        self.finish_countdown(w)
        self.assertEqual(w.screen, Screen.TIMER)
        self.assertEqual(w.calibration["ear_baseline"], self.PREVIOUS)

    def test_restart_after_refusal_clears_notice_and_attempts(self):
        w = self.make_window("fail_twice")
        w.go_calibration()
        self.finish_countdown(w)
        self.finish_countdown(w)
        w.idle_view.start_button.click()
        self.assertTrue(w.idle_view.notice_label.isHidden())
        self.finish_countdown(w)  # 새 세션의 첫 시도이므로 거부가 아니라 재시도
        self.assertEqual(w.screen, Screen.CALIBRATION)
        self.assertEqual(w.calibration_view.message_label.text(), RETRY_MESSAGE)

    def test_cancel_during_retry_resets_attempts(self):
        w = self.make_window("fail_twice")
        w.go_calibration()
        self.finish_countdown(w)
        w.calibration_view.cancel_button.click()
        self.assertEqual(w.screen, Screen.IDLE)
        w.go_calibration()
        self.finish_countdown(w)
        self.assertEqual(w.screen, Screen.CALIBRATION)  # 다시 첫 시도부터


class SessionStartTest(WindowTestCase):
    """캘리브레이션 종료 시각을 session_start로 확정 (1-10)."""

    def make_window(self, scenario: str, previous: float | None = None) -> MainWindow:
        window = MainWindow(camera=InstantMockCamera(MockCalibrator(scenario)), load_previous_baseline=lambda: previous)
        self.addCleanup(window.deleteLater)
        self.events: list[tuple[str, object]] = []
        window.session_started.connect(lambda t: self.events.append(("session_started", t)))
        window.screen_changed.connect(lambda s: self.events.append(("screen", s)))
        return window

    def finish_countdown(self, window: MainWindow) -> None:
        for _ in range(CALIBRATION_SEC):
            window.calibration_view.tick()

    def test_none_before_session(self):
        w = self.make_window("success")
        self.assertIsNone(w.session_start)
        w.go_calibration()
        self.assertIsNone(w.session_start)

    def test_success_uses_calibration_ended_at(self):
        w = self.make_window("success")
        w.go_calibration()
        self.finish_countdown(w)
        self.assertEqual(w.session_start, w.calibration["ended_at"])
        self.assertGreaterEqual(w.session_start, w.calibration["started_at"])

    def test_session_started_emitted_once_before_timer(self):
        w = self.make_window("success")
        w.go_calibration()
        self.finish_countdown(w)
        self.assertEqual(self.events, [
            ("screen", "calibration"),
            ("session_started", w.session_start),
            ("screen", "timer"),
        ])

    def test_retry_uses_second_attempt_end(self):
        w = self.make_window("retry_success")
        w.go_calibration()
        self.finish_countdown(w)
        self.assertIsNone(w.session_start)  # 재시도 중에는 아직 확정하지 않는다
        self.finish_countdown(w)
        self.assertEqual(w.session_start, w.calibration["ended_at"])
        self.assertEqual([e for e in self.events if e[0] == "session_started"],
                         [("session_started", w.session_start)])

    def test_fallback_uses_last_attempt_end(self):
        w = self.make_window("fail_twice", 0.28)
        w.go_calibration()
        self.finish_countdown(w)
        self.finish_countdown(w)
        self.assertTrue(w.calibration["fallback_used"])
        self.assertEqual(w.session_start, w.calibration["ended_at"])

    def test_refused_leaves_no_session_start(self):
        w = self.make_window("no_face")
        w.go_calibration()
        self.finish_countdown(w)
        self.finish_countdown(w)
        self.assertIsNone(w.session_start)
        self.assertNotIn("session_started", [e[0] for e in self.events])

    def test_new_session_resets_session_start(self):
        w = self.make_window("success")
        w.go_calibration()
        self.finish_countdown(w)
        w.go_report()
        w.go_idle()
        w.go_calibration()
        self.assertIsNone(w.session_start)
        self.assertIsNone(w.calibration)


class SlotQueueTest(WindowTestCase):
    """QTimer로 slot_queue를 비워 SlotLog에 반영 (1-12)."""

    def setUp(self):
        super().setUp()
        self.queue: queue.Queue = queue.Queue()
        self.window = MainWindow(camera=InstantMockCamera(MockCalibrator("success")), load_previous_baseline=lambda: None,
                                 slot_queue=self.queue)
        self.addCleanup(self.window.deleteLater)
        self.received: list[int] = []
        self.window.slots_received.connect(self.received.append)

    def start_session(self) -> float:
        self.window.go_calibration()
        for _ in range(CALIBRATION_SEC):
            self.window.calibration_view.tick()
        self.assertEqual(self.window.screen, Screen.TIMER)
        return self.window.session_start

    def put_all(self, messages) -> None:
        for msg in messages:
            self.queue.put(msg)

    def expected(self, session_start, messages):
        log = SlotLog(session_start)
        for msg in messages:
            log.add(msg)
        return log.finalize()

    def test_uses_injected_queue_or_creates_one(self):
        self.assertIs(self.window.slot_queue, self.queue)
        other = MainWindow()
        self.addCleanup(other.deleteLater)
        self.assertIsInstance(other.slot_queue, queue.Queue)

    def test_idle_before_session(self):
        self.assertIsNone(self.window.slot_log)
        self.assertFalse(self.window.is_receiving_slots)
        self.queue.put({"source": "cv"})
        self.assertEqual(self.window.drain_slot_queue(), 0)
        self.assertEqual(self.queue.qsize(), 1)  # 세션 전에는 꺼내지 않는다

    def test_session_start_creates_log_and_starts_polling(self):
        start = self.start_session()
        self.assertIsNotNone(self.window.slot_log)
        self.assertEqual(self.window.slot_log.session_start, start)
        self.assertTrue(self.window.is_receiving_slots)

    def test_slot_log_ready_when_session_started_emitted(self):
        seen = []
        self.window.session_started.connect(lambda t: seen.append(self.window.slot_log))
        self.start_session()
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0], self.window.slot_log)

    def test_leftover_messages_discarded_at_session_start(self):
        self.put_all(messages_only(build_scenario("normal", 1_000.0)))
        self.start_session()
        self.assertTrue(self.queue.empty())
        self.assertEqual(len(self.window.slot_log), 0)

    def test_drain_merges_messages(self):
        start = self.start_session()
        messages = messages_only(build_scenario("out_of_order", start, seed=1))
        self.put_all(messages)
        self.assertEqual(self.window.drain_slot_queue(), len(messages))
        self.assertTrue(self.queue.empty())
        self.assertEqual(self.window.slot_log.finalize(), self.expected(start, messages))
        self.assertEqual(self.received, [6])

    def test_no_signal_when_nothing_new(self):
        self.start_session()
        self.window.drain_slot_queue()
        self.assertEqual(self.received, [])

    def test_qtimer_drains_queue(self):
        start = self.start_session()
        messages = messages_only(build_scenario("normal", start))
        self.put_all(messages)
        QTest.qWait(SLOT_POLL_MS * 3)
        self.assertTrue(self.queue.empty())
        self.assertEqual(len(self.window.slot_log), 6)

    def test_report_drains_last_messages_and_stops(self):
        start = self.start_session()
        messages = messages_only(build_scenario("short_last_slot", start))
        self.put_all(messages)
        self.window.timer_view.end_button.click()
        self.assertEqual(self.window.screen, Screen.REPORT)
        self.assertFalse(self.window.is_receiving_slots)
        self.assertTrue(self.queue.empty())
        self.assertEqual(self.window.slot_log.finalize(), self.expected(start, messages))

    def test_time_up_also_stops(self):
        self.start_session()
        self.window.timer_view.time_up.emit()
        self.assertFalse(self.window.is_receiving_slots)
        self.assertIsNotNone(self.window.slot_log)  # 1-13에서 finalize()할 수 있게 남겨 둔다

    def test_leaving_to_idle_stops(self):
        self.start_session()
        self.window.go_idle()
        self.assertFalse(self.window.is_receiving_slots)

    def test_new_session_gets_new_log(self):
        self.start_session()
        self.put_all(messages_only(build_scenario("normal", self.window.session_start)))
        first = self.window.slot_log
        self.window.go_report()
        self.window.go_idle()
        self.window.go_calibration()
        self.assertIsNone(self.window.slot_log)
        self.start_session()
        self.assertIsNot(self.window.slot_log, first)
        self.assertEqual(len(self.window.slot_log), 0)

    def test_bad_message_does_not_stop_polling(self):
        start = self.start_session()
        messages = messages_only(build_scenario("normal", start))
        self.queue.put({"source": "audio", "slot": {}})
        self.put_all(messages)
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.window.drain_slot_queue(), len(messages))
        self.assertTrue(self.window.is_receiving_slots)
        self.assertEqual(len(self.window.slot_log), 6)


class HourlyCalibrator(MockCalibrator):
    """시도마다 한 시간씩 뒤의 시각으로 성공하는 캘리브레이션 (테스트에서 세션을 연달아 돌릴 때)."""

    def run(self, started_at):
        return super().run(started_at + 3600 * (self.attempts + 1))


class SessionEndTest(WindowTestCase):
    """세션 종료 흐름: flush 대기 → finalize → 정지 슬롯 제외 → mock 점수 → 저장 → 리포트 (1-13)."""

    def make_window(self, flush_wait_ms: int | None = None, scorer=None, calibrator=None) -> MainWindow:
        self.queue: queue.Queue = queue.Queue()
        self.scorer = scorer or MockScorer()
        w = MainWindow(camera=InstantMockCamera(calibrator or MockCalibrator("success")), load_previous_baseline=lambda: None,
                       slot_queue=self.queue, score_session=self.scorer, flush_wait_ms=flush_wait_ms)
        self.addCleanup(w.deleteLater)
        self.events: list[tuple[str, object]] = []
        w.session_ending.connect(lambda t: self.events.append(("ending", t)))
        w.session_finished.connect(lambda r: self.events.append(("finished", r)))
        w.screen_changed.connect(lambda s: self.events.append(("screen", s)))
        return w

    def start_session(self, w: MainWindow) -> float:
        w.go_calibration()
        for _ in range(CALIBRATION_SEC):
            w.calibration_view.tick()
        self.assertEqual(w.screen, Screen.TIMER)
        return w.session_start

    def saved_sessions(self) -> list[dict]:
        path = session_store.sessions_path(self.data_dir)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_end_button_scores_saves_and_shows_report(self):
        w = self.make_window()
        start = self.start_session(w)
        for msg in messages_only(build_scenario("normal", start)):
            self.queue.put(msg)
        w.end_session(start + 60)
        self.assertEqual(w.screen, Screen.REPORT)
        result = w.session_result
        self.assertEqual(result["session_id"], make_session_id(start))
        self.assertEqual(result["score_total"], -3)  # normal 시나리오 (test_session_end 참고)
        self.assertEqual(self.saved_sessions(), [result])
        log_path = session_store.focus_log_path(result["session_id"], self.data_dir)
        self.assertEqual(len(json.loads(log_path.read_text(encoding="utf-8"))["merged_slots"]), 6)
        self.assertFalse(w.save_failed)
        self.assertEqual([e[0] for e in self.events if e[0] != "screen" or e[1] == "report"],
                         ["ending", "finished", "screen"])

    def test_end_button_uses_now(self):
        w = self.make_window()
        self.start_session(w)
        with patch("gui.main_window.time.time", return_value=12_345.0):
            w.timer_view.end_button.click()
        self.assertEqual(w.session_end, 12_345.0)
        self.assertEqual(w.screen, Screen.REPORT)

    def test_waits_for_flush_and_takes_late_messages(self):
        w = self.make_window(flush_wait_ms=100)
        start = self.start_session(w)
        messages = messages_only(build_scenario("short_last_slot", start))
        w.end_session(start + 60)
        self.assertTrue(w.is_finishing)
        self.assertEqual(w.screen, Screen.TIMER)
        self.assertFalse(w.timer_view.end_button.isEnabled())
        self.assertFalse(w.timer_view.pause_button.isEnabled())
        self.assertFalse(w.timer_view.is_running)
        self.assertEqual(self.events[-1], ("ending", start + 60))
        for msg in messages:  # flush 대기 중에 도착한 메시지
            self.queue.put(msg)
        QTest.qWait(300)
        self.assertEqual(w.screen, Screen.REPORT)
        self.assertFalse(w.is_finishing)
        self.assertEqual(w.session_output.focus_log["merged_slots"],
                         self.expected_merged(start, messages, start + 60))

    def expected_merged(self, start, messages, end):
        log = SlotLog(start)
        for msg in messages:
            log.add(msg)
        return log.finalize(end)

    def test_default_flush_wait(self):
        w = MainWindow(flush_wait_ms=None)
        self.addCleanup(w.deleteLater)
        self.assertEqual(w._flush_timer.interval(), 0)  # 테스트에서는 FLUSH_WAIT_MS를 0으로 바꿔 둠
        with patch("gui.main_window.FLUSH_WAIT_MS", 2000):
            w2 = MainWindow()
        self.addCleanup(w2.deleteLater)
        self.assertEqual(w2._flush_timer.interval(), 2000)

    def test_time_up_uses_planned_end_with_pauses(self):
        w = self.make_window()
        start = self.start_session(w)
        now = [start]
        w.timer_view._clock = lambda: now[0]
        now[0] = start + 100
        w.timer_view.pause()
        now[0] = start + 130
        w.timer_view.resume()
        now[0] = start + SESSION_SEC + 30 + 0.7  # tick이 0.7초 늦게 옴
        w.timer_view.tick()
        self.assertEqual(w.session_end, start + SESSION_SEC + 30)
        merged = w.session_output.focus_log["merged_slots"]
        self.assertEqual(len(merged), (SESSION_SEC + 30) // SLOT_SECONDS)
        self.assertEqual(w.session_output.excluded_slots, [10, 11, 12])

    def test_paused_slots_not_scored_but_logged(self):
        w = self.make_window()
        start = self.start_session(w)
        for msg in messages_only(build_scenario("normal", start)):
            self.queue.put(msg)
        now = [start + 25]
        w.timer_view._clock = lambda: now[0]
        w.timer_view.pause()
        now[0] = start + 31
        w.end_session(start + 60)  # 정지 중 종료 → 정지 구간은 종료 처리 시각으로 닫힘
        self.assertEqual(w.session_result["pause_intervals"], [{"start": start + 25, "end": start + 31}])
        self.assertEqual([s["slot"] for s in self.scorer.calls[0]], [0, 1, 4, 5])
        self.assertEqual(len(w.session_output.focus_log["merged_slots"]), 6)

    def test_end_twice_ignored(self):
        w = self.make_window(flush_wait_ms=50)
        start = self.start_session(w)
        w.end_session(start + 30)
        w.end_session(start + 40)
        w.timer_view.end_requested.emit()
        QTest.qWait(150)
        w.complete_session()
        self.assertEqual(w.session_end, start + 30)
        self.assertEqual(len(self.scorer.calls), 1)
        self.assertEqual(len(self.saved_sessions()), 1)

    def test_end_outside_timer_ignored(self):
        w = self.make_window()
        w.end_session()
        self.assertIsNone(w.session_end)
        self.assertEqual(w.screen, Screen.IDLE)

    def test_going_idle_during_flush_cancels(self):
        w = self.make_window(flush_wait_ms=100)
        start = self.start_session(w)
        w.end_session(start + 30)
        w.go_idle()
        QTest.qWait(200)
        self.assertEqual(w.screen, Screen.IDLE)
        self.assertIsNone(w.session_output)
        self.assertEqual(self.saved_sessions(), [])

    def test_save_failure_still_shows_report(self):
        w = self.make_window()
        start = self.start_session(w)
        with patch.object(session_store, "append_session", side_effect=OSError("disk full")):
            with self.assertLogs("gui.main_window", level="ERROR"):
                w.end_session(start + 30)
        self.assertTrue(w.save_failed)
        self.assertEqual(w.screen, Screen.REPORT)
        self.assertIsNotNone(w.session_result)
        # focus_log는 따로 저장된다
        self.assertTrue(session_store.focus_log_path(w.session_result["session_id"], self.data_dir).exists())

    def test_scorer_error_returns_idle_with_notice(self):
        def broken(*args, **kwargs):
            raise RuntimeError("boom")

        w = self.make_window(scorer=broken)
        start = self.start_session(w)
        with self.assertLogs("gui.main_window", level="ERROR"):
            w.end_session(start + 30)
        self.assertEqual(w.screen, Screen.IDLE)
        self.assertEqual(w.idle_view.notice_label.text(), SCORE_FAILED_NOTICE)
        self.assertFalse(w.is_receiving_slots)

    def test_new_session_resets_result(self):
        w = self.make_window()
        start = self.start_session(w)
        w.end_session(start + 30)
        w.go_idle()
        w.go_calibration()
        self.assertIsNone(w.session_end)
        self.assertIsNone(w.session_output)
        self.assertFalse(w.save_failed)
        self.start_session(w)
        self.assertTrue(w.timer_view.end_button.isEnabled())
        self.assertTrue(w.timer_view.pause_button.isEnabled())

    def test_report_view_filled_and_cleared_on_new_session(self):
        w = self.make_window()
        start = self.start_session(w)
        now = [start + 12]
        w.timer_view._clock = lambda: now[0]
        w.timer_view.pause()
        now[0] = start + 14
        w.timer_view.resume()
        w.end_session(start + 30)
        self.assertIs(w.report_view.result, w.session_result)
        self.assertIn("1구간", w.report_view.pause_label.text())
        w.report_view.home_button.click()
        w.go_calibration()
        self.assertIsNone(w.report_view.result)

    def finish_by_time_up(self, w: MainWindow) -> None:
        start = self.start_session(w)
        w.timer_view._clock = lambda: start + SESSION_SEC
        w.timer_view.tick()
        self.assertEqual(w.screen, Screen.REPORT)

    def test_short_break_starts_with_report(self):
        w = self.make_window()
        self.finish_by_time_up(w)
        self.assertEqual(w.completed_sessions, 1)
        self.assertEqual(w.break_kind, BreakKind.SHORT)
        self.assertTrue(w.report_view.break_bar.is_running)

    def test_long_break_after_four_completed_sessions(self):
        w = self.make_window(calibrator=HourlyCalibrator())  # session_id(초 단위)가 겹치지 않게
        kinds = []
        for _ in range(LONG_BREAK_EVERY):
            self.finish_by_time_up(w)
            kinds.append(w.break_kind)
            w.report_view.next_button.click()
        self.assertEqual(kinds, [BreakKind.SHORT] * (LONG_BREAK_EVERY - 1) + [BreakKind.LONG])
        self.assertEqual(len(self.saved_sessions()), LONG_BREAK_EVERY)
        self.assertFalse(w.save_failed)

    def test_ended_early_not_counted(self):
        w = self.make_window()
        start = self.start_session(w)
        w.end_session(start + 30)
        self.assertEqual(w.completed_sessions, 0)
        self.assertEqual(w.break_kind, BreakKind.SHORT)

    def test_next_session_button_starts_calibration_and_stops_break(self):
        w = self.make_window()
        self.finish_by_time_up(w)
        w.report_view.next_button.click()
        self.assertEqual(w.screen, Screen.CALIBRATION)
        self.assertFalse(w.report_view.break_bar.is_running)
        self.assertIsNone(w.break_kind)

    def test_idle_shows_next_session_in_cycle(self):
        w = self.make_window()
        self.assertEqual(w.idle_view.cycle_label.text(), f"세션 1/{LONG_BREAK_EVERY}")
        self.finish_by_time_up(w)
        w.report_view.home_button.click()
        self.assertEqual(w.idle_view.cycle_label.text(), f"세션 2/{LONG_BREAK_EVERY}")

    def test_timer_shows_session_number_in_cycle(self):
        w = self.make_window()
        self.finish_by_time_up(w)
        self.assertEqual(w.timer_view.session_label.text(), f"Session 1 / {LONG_BREAK_EVERY}")
        self.start_session(w)
        self.assertEqual(w.timer_view.session_label.text(), f"Session 2 / {LONG_BREAK_EVERY}")

    def test_home_stops_break(self):
        w = self.make_window()
        self.finish_by_time_up(w)
        w.report_view.home_button.click()
        self.assertEqual(w.screen, Screen.IDLE)
        self.assertFalse(w.report_view.break_bar.is_running)

    def test_break_finished_alerts(self):
        w = self.make_window()
        self.finish_by_time_up(w)
        with patch("gui.main_window.QApplication.beep") as beep:
            w.report_view.break_bar.finished.emit()
        beep.assert_called_once()
        self.assertEqual(w.screen, Screen.REPORT)  # 다음 세션은 자동으로 시작하지 않는다

    def test_previous_baseline_read_from_saved_session(self):
        w = MainWindow(camera=InstantMockCamera(MockCalibrator("success")), score_session=MockScorer())
        self.addCleanup(w.deleteLater)
        start = self.start_session(w)
        w.end_session(start + 30)
        self.assertEqual(session_store.load_previous_baseline(self.data_dir), w.calibration["ear_baseline"])



class CameraEventsTest(WindowTestCase):
    """이벤트 큐로 결과가 늦게 오는 실제 흐름 (INT-03). 이벤트는 테스트가 직접 넣는다."""

    def setUp(self):
        super().setUp()
        self.camera = CameraLink()
        self.window = MainWindow(camera=self.camera, load_previous_baseline=lambda: None)
        self.addCleanup(self.window.deleteLater)

    def opened(self) -> None:
        self.camera.events.put({"type": CAMERA_OPENED})
        self.window.poll_camera_events()

    def failed(self) -> None:
        self.camera.events.put({"type": CAMERA_FAILED, "reason": "test"})
        with self.assertLogs("gui.main_window", level="WARNING"):
            self.window.poll_camera_events()

    def commands(self) -> list[dict]:
        out = []
        while not self.camera.commands.empty():
            out.append(self.camera.commands.get_nowait())
        return out

    def begin(self) -> int:
        """캘리브레이션을 시작하고 이번 시도의 calib_id를 돌려준다."""
        self.opened()
        self.window.go_calibration()
        command = self.commands()[-1]
        self.assertEqual(command["type"], START_CALIBRATION)
        return command["calib_id"]

    def raw(self, calib_id: int, ratio: float = 0.96, attempt: int = 1) -> None:
        self.camera.events.put({"type": CALIBRATION_RAW, "calib_id": calib_id, "attempt": attempt,
                                "raw": make_calibration(time.time(), ratio)})

    def finish_countdown(self) -> None:
        for _ in range(CALIBRATION_SEC):
            self.window.calibration_view.tick()

    def test_start_button_follows_camera_state(self):
        button = self.window.idle_view.start_button
        self.assertIsNone(self.window.idle_view.camera_ready)
        self.assertFalse(button.isEnabled())
        self.opened()
        self.assertTrue(self.window.idle_view.camera_ready)
        self.assertTrue(button.isEnabled())
        self.failed()
        self.assertFalse(self.window.idle_view.camera_ready)
        self.assertFalse(button.isEnabled())
        self.opened()
        self.assertTrue(button.isEnabled())

    def test_events_read_by_timer(self):
        self.camera.events.put({"type": CAMERA_OPENED})
        QTest.qWait(300)
        self.assertTrue(self.window.idle_view.camera_ready)

    def test_commands_carry_new_id_and_attempt(self):
        first = self.begin()
        self.finish_countdown()
        self.raw(first, ratio=0.1)
        self.window.poll_camera_events()
        command = self.commands()[-1]
        self.assertEqual((command["type"], command["attempt"]), (START_CALIBRATION, 2))
        self.assertNotEqual(command["calib_id"], first)

    def test_result_before_countdown_end_waits_for_countdown(self):
        calib_id = self.begin()
        self.raw(calib_id)
        self.window.poll_camera_events()
        self.assertEqual(self.window.screen, Screen.CALIBRATION)
        self.finish_countdown()
        self.assertEqual(self.window.screen, Screen.TIMER)
        self.assertEqual(self.window.session_start, self.window.calibration["ended_at"])

    def test_result_after_countdown_end_starts_session(self):
        calib_id = self.begin()
        self.finish_countdown()
        self.assertEqual(self.window.screen, Screen.CALIBRATION)
        self.assertEqual(self.window.calibration_view.message_label.text(), WAITING_MESSAGE)
        self.raw(calib_id)
        self.window.poll_camera_events()
        self.assertEqual(self.window.screen, Screen.TIMER)
        self.assertFalse(self.window._result_timer.isActive())

    def test_retry_result_arriving_at_countdown_end_keeps_new_attempt_running(self):
        calib_id = self.begin()
        self.raw(calib_id, ratio=0.1)  # 카운트다운 끝의 poll에서 받아 판정 → 재시도
        self.finish_countdown()
        self.assertEqual(self.window.calibration_view.message_label.text(), RETRY_MESSAGE)
        self.assertTrue(self.window.calibration_view.is_running)
        self.assertFalse(self.window._result_timer.isActive())

    def test_old_calib_id_is_dropped(self):
        first = self.begin()
        self.window.calibration_view.cancel_button.click()
        second = self.begin()
        self.assertNotEqual(first, second)
        self.raw(first)
        self.finish_countdown()
        self.assertEqual(self.window.screen, Screen.CALIBRATION)  # 이전 결과로 세션을 시작하지 않는다
        self.assertIsNone(self.window.calibration)

    def test_cancel_sends_cancel_command(self):
        calib_id = self.begin()
        self.window.calibration_view.cancel_button.click()
        self.assertEqual(self.commands(), [{"type": CANCEL_CALIBRATION, "calib_id": calib_id}])

    def test_no_cancel_after_result_used(self):
        calib_id = self.begin()
        self.raw(calib_id)
        self.finish_countdown()
        self.assertEqual(self.commands(), [])

    def test_camera_failed_during_calibration_returns_idle_without_retry(self):
        self.begin()
        self.failed()
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertEqual(self.window.idle_view.notice_label.text(), CAMERA_LOST_NOTICE)
        self.assertFalse(self.window.calibration_view.is_running)
        self.assertEqual(self.commands(), [])  # 끊긴 시도는 취소 명령을 보내지 않는다

    def test_camera_failed_while_waiting_for_result(self):
        self.begin()
        self.finish_countdown()
        self.failed()
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertFalse(self.window._result_timer.isActive())

    def test_camera_failed_during_session_keeps_session(self):
        calib_id = self.begin()
        self.raw(calib_id)
        self.finish_countdown()
        self.failed()
        self.assertEqual(self.window.screen, Screen.TIMER)
        self.assertTrue(self.window.timer_view.is_running)
        self.assertFalse(self.window.idle_view.notice_label.text())

    def test_result_timeout_cancels_and_returns_idle(self):
        calib_id = self.begin()
        self.window._result_timer.setInterval(20)
        self.finish_countdown()
        with self.assertLogs("gui.main_window", level="WARNING"):
            QTest.qWait(100)
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertEqual(self.window.idle_view.notice_label.text(), CALIBRATION_TIMEOUT_NOTICE)
        self.assertEqual(self.commands(), [{"type": CANCEL_CALIBRATION, "calib_id": calib_id}])
        self.raw(calib_id)
        self.window.poll_camera_events()
        self.assertEqual(self.window.screen, Screen.IDLE)

    def test_next_session_refused_when_camera_lost_during_break(self):
        calib_id = self.begin()
        self.raw(calib_id)
        self.finish_countdown()
        self.window.end_session(self.window.session_start + 30)
        self.assertEqual(self.window.screen, Screen.REPORT)
        self.failed()
        self.window.report_view.next_button.click()
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertEqual(self.window.idle_view.notice_label.text(), CAMERA_NOT_READY_NOTICE)


class MiniModeTest(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.window = MainWindow()
        self.window.show()

    def tearDown(self):
        self.window.mini_window.hide()
        self.window.close()
        self.window.deleteLater()

    def start_session(self) -> float:
        self.window.go_calibration()
        for _ in range(CALIBRATION_SEC):
            self.window.calibration_view.tick()
        self.assertEqual(self.window.screen, Screen.TIMER)
        return self.window.session_start

    def test_only_from_timer(self):
        self.window.enter_mini_mode()
        self.assertFalse(self.window.is_mini_mode)
        self.assertTrue(self.window.isVisible())

    def test_mini_button_hides_main_and_restore_returns(self):
        self.start_session()
        self.window.timer_view.mini_button.click()
        self.assertTrue(self.window.is_mini_mode)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.timer_view.is_running)  # 타이머는 계속 간다
        self.window.mini_window.restore_button.click()
        self.assertFalse(self.window.is_mini_mode)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.screen, Screen.TIMER)

    def test_end_session_from_mini_returns_to_main(self):
        start = self.start_session()
        self.window.enter_mini_mode()
        self.window.end_session(start + 30)
        self.assertFalse(self.window.is_mini_mode)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.screen, Screen.REPORT)

    def test_time_up_in_mini_returns_to_main(self):
        start = self.start_session()
        self.window.enter_mini_mode()
        self.window.timer_view._clock = lambda: start + SESSION_SEC
        self.window.timer_view.tick()
        self.assertFalse(self.window.is_mini_mode)
        self.assertEqual(self.window.screen, Screen.REPORT)

    def test_no_mini_mode_while_finishing(self):
        start = self.start_session()
        self.window._flush_timer.setInterval(10_000)  # flush 대기 중에 멈춰 둔다
        self.window.end_session(start + 30)
        self.assertTrue(self.window.timer_view.is_finishing)
        self.window.enter_mini_mode()
        self.assertFalse(self.window.is_mini_mode)
        self.window._flush_timer.stop()

if __name__ == "__main__":
    unittest.main()
