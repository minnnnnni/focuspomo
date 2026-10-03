"""gui/timer_view.py 카운트다운 테스트 (화면 없이 offscreen으로 실행).

QTimer를 실제로 기다리지 않고, 가짜 clock을 움직인 뒤 tick()을 직접 부른다.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.timer_view import (  # noqa: E402
    PAUSE_BUTTON_TEXT,
    PAUSED_STATUS_TEXT,
    RESUME_BUTTON_TEXT,
    SESSION_SEC,
    STATUS_TEXT,
    TimerView,
    format_mmss,
)

app = QApplication.instance() or QApplication([])

START = 1_000_000.0


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FormatTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_mmss(SESSION_SEC), "25:00")
        self.assertEqual(format_mmss(61), "01:01")
        self.assertEqual(format_mmss(0), "00:00")
        self.assertEqual(format_mmss(-3), "00:00")


class ViewTestCase(unittest.TestCase):
    """가짜 clock을 넣은 TimerView와 시그널 기록을 준비한다."""

    def setUp(self):
        self.clock = FakeClock(START)
        self.view = TimerView(clock=self.clock)
        self.time_up: list[bool] = []
        self.ended: list[bool] = []
        self.view.time_up.connect(lambda: self.time_up.append(True))
        self.view.end_requested.connect(lambda: self.ended.append(True))

    def tearDown(self):
        self.view.stop()
        self.view.deleteLater()

    def advance(self, seconds: float) -> None:
        self.clock.now += seconds
        self.view.tick()


class TimerViewTest(ViewTestCase):
    def test_start_shows_full_session(self):
        self.view.start(START)
        self.assertTrue(self.view.is_running)
        self.assertEqual(self.view.remaining, SESSION_SEC)
        self.assertEqual(self.view.time_label.text(), "25:00")
        self.assertEqual(self.view.ring.progress, 0.0)

    def test_counts_from_session_start_not_from_start_call(self):
        # 타이머 화면이 session_start보다 늦게 열려도 session_start 기준으로 센다
        self.clock.now = START + 60
        self.view.start(START)
        self.assertEqual(self.view.time_label.text(), "24:00")

    def test_partial_second_rounds_up(self):
        self.view.start(START)
        self.advance(0.4)
        self.assertEqual(self.view.time_label.text(), "25:00")
        self.advance(0.6)
        self.assertEqual(self.view.time_label.text(), "24:59")

    def test_progress_follows_elapsed(self):
        self.view.start(START)
        self.advance(SESSION_SEC / 4)
        self.assertAlmostEqual(self.view.ring.progress, 0.25)
        self.assertEqual(self.time_up, [])

    def test_clock_before_session_start_is_clamped(self):
        self.clock.now = START - 5
        self.view.start(START)
        self.assertEqual(self.view.remaining, SESSION_SEC)
        self.assertEqual(self.view.ring.progress, 0.0)

    def test_time_up_once_at_zero(self):
        self.view.start(START)
        self.advance(SESSION_SEC - 1)
        self.assertEqual(self.time_up, [])
        self.advance(1)
        self.assertEqual(self.time_up, [True])
        self.assertFalse(self.view.is_running)
        self.assertEqual(self.view.time_label.text(), "00:00")
        self.assertEqual(self.view.ring.progress, 1.0)
        self.advance(5)  # 멈춘 뒤 tick은 무시
        self.assertEqual(self.time_up, [True])

    def test_late_tick_still_finishes(self):
        # QTimer가 한참 밀려도 한 번의 tick으로 끝난다
        self.view.start(START)
        self.advance(SESSION_SEC + 30)
        self.assertEqual(self.time_up, [True])
        self.assertEqual(self.view.remaining, 0)

    def test_end_button_stops_without_time_up(self):
        self.view.start(START)
        self.advance(10)
        self.view.end_button.click()
        self.assertFalse(self.view.is_running)
        self.assertEqual(self.ended, [True])
        self.assertEqual(self.time_up, [])

    def test_restart_with_new_session_start(self):
        self.view.start(START)
        self.advance(SESSION_SEC)
        self.view.start(self.clock.now)
        self.assertTrue(self.view.is_running)
        self.assertEqual(self.view.remaining, SESSION_SEC)
        self.assertEqual(self.view.ring.progress, 0.0)

    def test_renders_offscreen(self):
        self.view.resize(480, 360)
        self.view.start(START)
        self.advance(SESSION_SEC / 3)
        self.assertFalse(self.view.grab().isNull())


class PauseTest(ViewTestCase):
    """일시정지: 멈춘 만큼 종료가 늦어지고, 정지 구간을 기록한다."""

    def wait(self, seconds: float) -> None:
        # 정지 중에는 QTimer가 멈춰 있으므로 tick 없이 시간만 흐른다
        self.clock.now += seconds

    def test_pause_freezes_remaining(self):
        self.view.start(START)
        self.advance(60)
        self.view.pause_button.click()
        self.assertTrue(self.view.is_paused)
        self.assertFalse(self.view.is_running)
        self.wait(300)
        self.view.tick()  # 정지 중 tick은 무시
        self.assertEqual(self.view.time_label.text(), "24:00")

    def test_resume_continues_from_paused_time(self):
        self.view.start(START)
        self.advance(60)
        self.view.pause()
        self.wait(300)
        self.view.pause_button.click()
        self.assertFalse(self.view.is_paused)
        self.assertTrue(self.view.is_running)
        self.assertEqual(self.view.time_label.text(), "24:00")
        self.advance(1)
        self.assertEqual(self.view.time_label.text(), "23:59")

    def test_pause_delays_time_up(self):
        self.view.start(START)
        self.view.pause()
        self.wait(100)
        self.view.resume()
        self.advance(SESSION_SEC - 1)
        self.assertEqual(self.time_up, [])
        self.advance(1)
        self.assertEqual(self.time_up, [True])

    def test_records_pause_intervals(self):
        self.view.start(START)
        self.advance(10)
        self.view.pause()
        self.wait(5)
        self.view.resume()
        self.advance(20)
        self.view.pause()
        self.wait(7)
        self.view.resume()
        self.assertEqual(self.view.pause_intervals, [(START + 10, START + 15), (START + 35, START + 42)])

    def test_open_pause_not_listed_until_closed(self):
        self.view.start(START)
        self.view.pause()
        self.wait(5)
        self.assertEqual(self.view.pause_intervals, [])

    def test_end_while_paused_closes_interval(self):
        self.view.start(START)
        self.advance(10)
        self.view.pause()
        self.wait(30)
        self.view.end_button.click()
        self.assertEqual(self.ended, [True])
        self.assertFalse(self.view.is_paused)
        self.assertEqual(self.view.pause_intervals, [(START + 10, START + 40)])

    def test_labels_switch(self):
        self.view.start(START)
        self.view.pause()
        self.assertEqual(self.view.pause_button.text(), RESUME_BUTTON_TEXT)
        self.assertEqual(self.view.ring.status_label.text(), PAUSED_STATUS_TEXT)
        self.view.resume()
        self.assertEqual(self.view.pause_button.text(), PAUSE_BUTTON_TEXT)
        self.assertEqual(self.view.ring.status_label.text(), STATUS_TEXT)

    def test_pause_before_start_or_after_finish_is_ignored(self):
        self.view.pause()
        self.assertFalse(self.view.is_paused)
        self.view.start(START)
        self.advance(SESSION_SEC)
        self.view.pause()
        self.assertFalse(self.view.is_paused)

    def test_new_session_clears_pause_state(self):
        self.view.start(START)
        self.view.pause()
        self.wait(5)
        self.view.resume()
        self.view.pause()
        self.view.start(self.clock.now)
        self.assertFalse(self.view.is_paused)
        self.assertTrue(self.view.is_running)
        self.assertEqual(self.view.pause_intervals, [])
        self.assertEqual(self.view.pause_button.text(), PAUSE_BUTTON_TEXT)


if __name__ == "__main__":
    unittest.main()
