"""gui/mini_timer.py 미니 모드 창 테스트 (화면 없이 offscreen으로 실행).

가짜 clock을 넣은 TimerView를 움직이고, 미니 창이 따라 그리는지 본다.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.mini_timer import PAUSE_TIP, RESUME_TIP, MiniTimerWindow  # noqa: E402
from gui.timer_view import SESSION_SEC, TimerView  # noqa: E402

app = QApplication.instance() or QApplication([])

START = 1_000_000.0


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class MiniTimerTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(START)
        self.view = TimerView(clock=self.clock)
        self.mini = MiniTimerWindow(self.view)
        self.restores: list[bool] = []
        self.mini.restore_requested.connect(lambda: self.restores.append(True))
        self.view.start(START)

    def tearDown(self):
        self.view.stop()
        self.mini.hide()
        self.mini.deleteLater()
        self.view.deleteLater()

    def advance(self, seconds: float) -> None:
        self.clock.now += seconds
        self.view.tick()

    def test_follows_timer(self):
        self.assertEqual(self.mini.time_label.text(), "25:00")
        self.advance(SESSION_SEC / 4)
        self.assertEqual(self.mini.time_label.text(), "18:45")
        self.assertAlmostEqual(self.mini.progress_bar.progress, 0.25)

    def test_toggle_button_pauses_timer(self):
        self.mini.toggle_button.click()
        self.assertTrue(self.view.is_paused)
        self.assertEqual(self.mini.toggle_button.toolTip(), RESUME_TIP)
        self.assertTrue(self.mini.time_label.property("paused"))
        self.mini.toggle_button.click()
        self.assertFalse(self.view.is_paused)
        self.assertEqual(self.mini.toggle_button.toolTip(), PAUSE_TIP)
        self.assertFalse(self.mini.time_label.property("paused"))

    def test_pause_in_mini_records_interval(self):
        self.advance(10)
        self.mini.toggle_button.click()
        self.clock.now += 5
        self.mini.toggle_button.click()
        self.assertEqual(self.view.pause_intervals, [{"start": START + 10, "end": START + 15}])

    def test_toggle_disabled_while_finishing(self):
        self.view.show_finishing()
        self.assertFalse(self.mini.toggle_button.isEnabled())
        self.mini._toggle_pause_if_running()  # Space도 무시
        self.assertFalse(self.view.is_paused)
        self.view.start(START + 100)
        self.assertTrue(self.mini.toggle_button.isEnabled())

    def test_restore_button_and_double_click(self):
        self.mini.restore_button.click()
        self.mini.show()
        QTest.mouseDClick(self.mini, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
        self.assertEqual(self.restores, [True, True])

    def test_close_requests_restore_instead(self):
        self.mini.show()
        self.mini.close()
        self.assertEqual(self.restores, [True])
        self.assertTrue(self.mini.isVisible())  # 닫히지 않고, 숨기는 건 MainWindow 몫

    def test_pin_toggles_always_on_top(self):
        self.assertTrue(self.mini.always_on_top)
        self.assertTrue(self.mini.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.mini.pin_button.click()
        self.assertFalse(self.mini.always_on_top)
        self.assertFalse(self.mini.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.mini.set_always_on_top(True)
        self.assertTrue(self.mini.pin_button.isChecked())
        self.assertTrue(self.mini.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_paints_without_error(self):
        self.advance(600)
        self.assertFalse(self.mini.grab().isNull())


if __name__ == "__main__":
    unittest.main()
