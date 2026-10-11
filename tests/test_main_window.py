"""gui/main_window.py 화면 전환 테스트 (화면 없이 offscreen으로 실행)."""
import os
import queue
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from config.aggregation import CALIBRATION_SEC  # noqa: E402
from gui.calibration_view import RETRY_MESSAGE  # noqa: E402
from core.slot_log import SlotLog  # noqa: E402
from gui.main_window import REFUSED_NOTICE, SLOT_POLL_MS, MainWindow, Screen  # noqa: E402
from mocks.mock_calibration import MockCalibrator  # noqa: E402
from mocks.mock_session import build_scenario, messages_only  # noqa: E402

app = QApplication.instance() or QApplication([])


class ScreenFlowTest(unittest.TestCase):
    def setUp(self):
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
        self.window.report_view.buttons["처음으로"].click()
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


class TimerFlowTest(unittest.TestCase):
    """타이머 화면 연결 (1-11)."""

    def setUp(self):
        self.window = MainWindow(calibrator=MockCalibrator("success"), load_previous_baseline=lambda: None)
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


class CalibrationResultTest(unittest.TestCase):
    """mock 캘리브레이션 시나리오별 화면 흐름 (1-09)."""

    PREVIOUS = 0.28

    def make_window(self, scenario: str, previous: float | None = None) -> MainWindow:
        window = MainWindow(calibrator=MockCalibrator(scenario), load_previous_baseline=lambda: previous)
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
        # 주입하지 않으면 storage.session_store에서 직전 값을 읽는다 (1-20)
        with patch("gui.main_window.load_previous_baseline_from_store", return_value=self.PREVIOUS):
            w = MainWindow(calibrator=MockCalibrator("fail_twice"))
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


class SessionStartTest(unittest.TestCase):
    """캘리브레이션 종료 시각을 session_start로 확정 (1-10)."""

    def make_window(self, scenario: str, previous: float | None = None) -> MainWindow:
        window = MainWindow(calibrator=MockCalibrator(scenario), load_previous_baseline=lambda: previous)
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


class SlotQueueTest(unittest.TestCase):
    """QTimer로 slot_queue를 비워 SlotLog에 반영 (1-12)."""

    def setUp(self):
        self.queue: queue.Queue = queue.Queue()
        self.window = MainWindow(calibrator=MockCalibrator("success"), load_previous_baseline=lambda: None,
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


if __name__ == "__main__":
    unittest.main()
