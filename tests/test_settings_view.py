"""gui/settings_view.py, gui/nav_bar.py 테스트 (offscreen)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.pomodoro import DEFAULT_SETTINGS, SETTING_LIMITS, PomodoroSettings  # noqa: E402
from gui.nav_bar import TAB_HISTORY, TAB_SETTINGS, TAB_TIMER, NavBar  # noqa: E402
from gui.settings_view import SAVED_NOTICE, SettingsView, cycle_text, format_total  # noqa: E402

app = QApplication.instance() or QApplication([])


class CycleTextTest(unittest.TestCase):
    def test_default(self):
        self.assertEqual(cycle_text(DEFAULT_SETTINGS),
                         "집중 25분 × 4 → 짧은 휴식 5분 × 3 → 긴 휴식 15분  (총 2시간 10분)")

    def test_every_one_has_no_short_break(self):
        self.assertEqual(cycle_text(PomodoroSettings(30, 5, 20, 1)), "집중 30분 × 1 → 긴 휴식 20분  (총 50분)")

    def test_format_total(self):
        self.assertEqual([format_total(m) for m in (45, 60, 125)], ["45분", "1시간", "2시간 5분"])


class SettingsViewTest(unittest.TestCase):
    def setUp(self):
        self.view = SettingsView()
        self.saved: list[PomodoroSettings] = []
        self.view.save_requested.connect(self.saved.append)

    def tearDown(self):
        self.view.deleteLater()

    def test_starts_clean(self):
        self.assertEqual(self.view.current(), DEFAULT_SETTINGS)
        self.assertFalse(self.view.is_dirty)
        self.assertFalse(self.view.save_button.isEnabled())
        self.assertFalse(self.view.reset_button.isEnabled())

    def test_edit_enables_save_and_emits(self):
        self.view.steppers["focus_min"].plus_button.click()
        self.assertTrue(self.view.is_dirty)
        self.assertTrue(self.view.save_button.isEnabled())
        self.assertIn("집중 26분", self.view.cycle_label.text())
        self.view.save_button.click()
        self.assertEqual(self.saved, [PomodoroSettings(focus_min=26)])

    def test_show_saved(self):
        s = PomodoroSettings(50, 10, 30, 2)
        self.view.show_saved(s)
        self.assertEqual(self.view.current(), s)
        self.assertFalse(self.view.is_dirty)
        self.assertFalse(self.view.notice_label.isHidden())
        self.assertEqual(self.view.notice_label.text(), SAVED_NOTICE)
        self.view.steppers["long_break_every"].plus_button.click()
        self.assertTrue(self.view.notice_label.isHidden())  # 새 입력 위에 "저장했어요"가 남지 않는다

    def test_reset_to_defaults(self):
        self.view.set_settings(PomodoroSettings(50, 10, 30, 2))
        self.assertTrue(self.view.reset_button.isEnabled())
        self.view.reset_button.click()
        self.assertEqual(self.view.current(), DEFAULT_SETTINGS)
        self.assertTrue(self.view.save_button.isEnabled())  # 기본값으로 되돌린 것도 저장해야 적용된다
        self.assertEqual(self.saved, [])

    def test_limits(self):
        for name, (low, high) in SETTING_LIMITS.items():
            stepper = self.view.steppers[name]
            stepper.set_value(low)
            self.assertFalse(stepper.minus_button.isEnabled())
            stepper.set_value(high + 5)
            self.assertEqual(stepper.value(), high)
            self.assertFalse(stepper.plus_button.isEnabled())


class NavBarTest(unittest.TestCase):
    def test_click_emits_and_set_current(self):
        bar = NavBar()
        self.addCleanup(bar.deleteLater)
        got: list[str] = []
        bar.navigate.connect(got.append)
        self.assertEqual(bar.current, TAB_TIMER)
        bar.buttons[TAB_HISTORY].click()
        self.assertEqual(got, [TAB_HISTORY])
        bar.set_current(TAB_SETTINGS)
        self.assertEqual(bar.current, TAB_SETTINGS)
        self.assertEqual(got, [TAB_HISTORY])  # set_current는 신호를 보내지 않는다


if __name__ == "__main__":
    unittest.main()
