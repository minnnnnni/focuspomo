"""gui/main_window.py 화면 전환 테스트 (화면 없이 offscreen으로 실행)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config.aggregation import CALIBRATION_SEC  # noqa: E402
from gui.calibration_view import RETRY_MESSAGE  # noqa: E402
from gui.main_window import REFUSED_NOTICE, MainWindow, Screen  # noqa: E402
from mocks.mock_calibration import MockCalibrator  # noqa: E402

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
        self.window.timer_view.buttons["중간 종료"].click()
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


if __name__ == "__main__":
    unittest.main()
