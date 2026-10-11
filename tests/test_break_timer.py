"""core/pomodoro.py, gui/break_timer.py 테스트 (휴식 타이머 1-15, offscreen)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.pomodoro import LONG_BREAK_EVERY, LONG_BREAK_SEC, SHORT_BREAK_SEC, BreakKind, break_after  # noqa: E402
from gui.break_timer import BREAK_TITLE, FINISHED_TEXT, BreakTimerBar  # noqa: E402

app = QApplication.instance() or QApplication([])

START = 1_000_000.0


class BreakAfterTest(unittest.TestCase):
    def test_every_fourth_completed_session_is_long(self):
        kinds = [break_after(True, n) for n in range(1, 9)]
        self.assertEqual(kinds, [BreakKind.SHORT] * 3 + [BreakKind.LONG] + [BreakKind.SHORT] * 3 + [BreakKind.LONG])
        self.assertEqual(LONG_BREAK_EVERY, 4)

    def test_ended_early_is_short_even_on_multiple(self):
        self.assertEqual(break_after(False, 4), BreakKind.SHORT)
        self.assertEqual(break_after(False, 0), BreakKind.SHORT)

    def test_lengths(self):
        self.assertEqual((SHORT_BREAK_SEC, LONG_BREAK_SEC), (300, 900))


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class BreakTimerBarTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(START)
        self.bar = BreakTimerBar(clock=self.clock)
        self.addCleanup(self.bar.deleteLater)
        self.addCleanup(self.bar.stop)
        self.finished: list[bool] = []
        self.bar.finished.connect(lambda: self.finished.append(True))

    def advance(self, seconds: float) -> None:
        self.clock.now += seconds
        self.bar.tick()

    def test_short_break_countdown(self):
        self.bar.start(BreakKind.SHORT, completed_sessions=1)
        self.assertTrue(self.bar.is_running)
        self.assertEqual(self.bar.time_label.text(), "05:00")
        self.assertEqual(self.bar.title_label.text(), BREAK_TITLE[BreakKind.SHORT])
        self.assertIn("1개", self.bar.info_label.text())
        self.advance(0.4)
        self.assertEqual(self.bar.remaining, 300)  # 올림
        self.advance(60)
        self.assertEqual(self.bar.time_label.text(), "04:00")

    def test_long_break_length(self):
        self.bar.start(BreakKind.LONG, completed_sessions=4)
        self.assertEqual(self.bar.time_label.text(), "15:00")
        self.assertEqual(self.bar.title_label.text(), BREAK_TITLE[BreakKind.LONG])

    def test_finished_once(self):
        self.bar.start(BreakKind.SHORT)
        self.advance(299)
        self.assertEqual(self.finished, [])
        self.advance(5)
        self.assertEqual(self.finished, [True])
        self.assertTrue(self.bar.is_finished)
        self.assertFalse(self.bar.is_running)
        self.assertEqual(self.bar.time_label.text(), "00:00")
        self.assertEqual(self.bar.title_label.text(), FINISHED_TEXT)
        self.advance(10)
        self.assertEqual(self.finished, [True])

    def test_late_tick_does_not_go_negative(self):
        self.bar.start(BreakKind.SHORT)
        self.advance(10_000)
        self.assertEqual(self.bar.remaining, 0)
        self.assertEqual(self.finished, [True])

    def test_stop_prevents_finish(self):
        self.bar.start(BreakKind.SHORT)
        self.bar.stop()
        self.advance(400)
        self.assertEqual(self.finished, [])

    def test_restart_resets_finished(self):
        self.bar.start(BreakKind.SHORT)
        self.advance(300)
        self.bar.start(BreakKind.LONG)
        self.assertFalse(self.bar.is_finished)
        self.assertEqual(self.bar.title_label.text(), BREAK_TITLE[BreakKind.LONG])


if __name__ == "__main__":
    unittest.main()
