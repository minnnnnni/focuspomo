"""core/pomodoro.py PomodoroSettings, storage/settings_store.py 테스트 (세션 설정)."""
import json
import tempfile
import unittest
from pathlib import Path

from core.pomodoro import (
    DEFAULT_SETTINGS,
    SETTING_LIMITS,
    BreakKind,
    PomodoroSettings,
    break_after,
    cycle_position,
)
from storage import session_store, settings_store


class PomodoroSettingsTest(unittest.TestCase):
    def test_defaults_match_classic_pomodoro(self):
        s = PomodoroSettings()
        self.assertEqual((s.focus_min, s.short_break_min, s.long_break_min, s.long_break_every), (25, 5, 15, 4))
        self.assertEqual(s.focus_sec, 1500)
        self.assertEqual(s.break_seconds(BreakKind.SHORT), 300)
        self.assertEqual(s.break_seconds(BreakKind.LONG), 900)

    def test_clamped(self):
        s = PomodoroSettings(focus_min=0, short_break_min=999, long_break_min=-3, long_break_every=50).clamped()
        self.assertEqual(s.focus_min, SETTING_LIMITS["focus_min"][0])
        self.assertEqual(s.short_break_min, SETTING_LIMITS["short_break_min"][1])
        self.assertEqual(s.long_break_min, SETTING_LIMITS["long_break_min"][0])
        self.assertEqual(s.long_break_every, SETTING_LIMITS["long_break_every"][1])

    def test_from_dict_fills_missing_and_bad_values(self):
        s = PomodoroSettings.from_dict({"focus_min": 50, "short_break_min": "10", "long_break_every": True})
        self.assertEqual(s, PomodoroSettings(focus_min=50))
        self.assertEqual(PomodoroSettings.from_dict(["not", "a", "dict"]), DEFAULT_SETTINGS)

    def test_dict_round_trip(self):
        s = PomodoroSettings(40, 8, 20, 3)
        self.assertEqual(PomodoroSettings.from_dict(s.to_dict()), s)


class CustomCycleTest(unittest.TestCase):
    def test_break_after_custom_every(self):
        kinds = [break_after(True, n, every=3) for n in range(1, 7)]
        self.assertEqual(kinds, [BreakKind.SHORT, BreakKind.SHORT, BreakKind.LONG] * 2)

    def test_every_one_is_always_long(self):
        self.assertEqual(break_after(True, 1, every=1), BreakKind.LONG)
        self.assertEqual(break_after(False, 1, every=1), BreakKind.SHORT)

    def test_cycle_position_custom_every(self):
        self.assertEqual([cycle_position(n, 2) for n in range(5)], [1, 2, 1, 2, 1])
        self.assertEqual(cycle_position(3, 0), 1)  # 0 이하 주기는 1로 본다


class SettingsStoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"

    def test_missing_file_is_default(self):
        self.assertEqual(settings_store.load_settings(self.data_dir), DEFAULT_SETTINGS)

    def test_save_and_load(self):
        s = PomodoroSettings(45, 10, 25, 3)
        path = settings_store.save_settings(s, self.data_dir)
        self.assertEqual(path, self.data_dir / "settings.json")
        self.assertEqual(settings_store.load_settings(self.data_dir), s)
        self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_corrupt_file_is_default(self):
        self.data_dir.mkdir(parents=True)
        settings_store.settings_path(self.data_dir).write_text("{not json", encoding="utf-8")
        self.assertEqual(settings_store.load_settings(self.data_dir), DEFAULT_SETTINGS)

    def test_out_of_range_values_clamped_on_load(self):
        self.data_dir.mkdir(parents=True)
        settings_store.settings_path(self.data_dir).write_text(json.dumps({"focus_min": 10_000}), encoding="utf-8")
        self.assertEqual(settings_store.load_settings(self.data_dir).focus_min, SETTING_LIMITS["focus_min"][1])

    def test_delete_records_keeps_settings(self):
        settings_store.save_settings(PomodoroSettings(focus_min=50), self.data_dir)
        session_store.delete_all_records(self.data_dir)
        self.assertEqual(settings_store.load_settings(self.data_dir).focus_min, 50)


if __name__ == "__main__":
    unittest.main()
