"""gui/main_window.py 화면 전환 테스트 (화면 없이 offscreen으로 실행)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.main_window import MainWindow, Screen  # noqa: E402

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
        self.window.calibration_view.buttons["캘리브레이션 성공"].click()
        self.assertEqual(self.window.screen, Screen.TIMER)
        self.window.timer_view.buttons["중간 종료"].click()
        self.assertEqual(self.window.screen, Screen.REPORT)
        self.assertIs(self.current(), self.window.report_view)
        self.window.report_view.buttons["처음으로"].click()
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertEqual(self.changes, ["calibration", "timer", "report", "idle"])

    def test_calibration_rejected_returns_idle(self):
        self.window.go_calibration()
        self.window.calibration_view.buttons["세션 시작 거부"].click()
        self.assertEqual(self.window.screen, Screen.IDLE)
        self.assertIs(self.current(), self.window.idle_view)


if __name__ == "__main__":
    unittest.main()
