"""gui/idle_view.py 대기 화면 테스트 (화면 없이 offscreen으로 실행)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config.aggregation import CALIBRATION_SEC  # noqa: E402
from core.pomodoro import LONG_BREAK_EVERY  # noqa: E402
from core.pomodoro import cycle_position  # noqa: E402
from gui.idle_view import CALIBRATION_HINT, CAMERA_STATUS_TEXT, IdleView  # noqa: E402

app = QApplication.instance() or QApplication([])


class CyclePositionTest(unittest.TestCase):
    def test_wraps_after_long_break(self):
        self.assertEqual([cycle_position(n) for n in range(LONG_BREAK_EVERY + 2)],
                         list(range(1, LONG_BREAK_EVERY + 1)) + [1, 2])

    def test_negative_treated_as_zero(self):
        self.assertEqual(cycle_position(-3), 1)


class IdleViewTest(unittest.TestCase):
    def setUp(self):
        self.view = IdleView()
        self.view.resize(580, 580)

    def tearDown(self):
        self.view.deleteLater()

    def test_start_button_emits(self):
        got: list[bool] = []
        self.view.start_requested.connect(lambda: got.append(True))
        self.view.start_button.click()
        self.assertEqual(got, [True])

    def test_hint_uses_calibration_sec(self):
        self.assertIn(f"{CALIBRATION_SEC}초", CALIBRATION_HINT)

    def test_notice_show_and_clear(self):
        self.view.show()
        self.assertTrue(self.view.notice_label.isHidden())
        self.view.show_notice("안내")
        self.assertFalse(self.view.notice_label.isHidden())
        self.assertEqual(self.view.notice_label.text(), "안내")
        self.view.clear_notice()
        self.assertTrue(self.view.notice_label.isHidden())
        self.assertEqual(self.view.notice_label.text(), "")

    def test_camera_status_defaults_to_unknown(self):
        self.assertIsNone(self.view.camera_ready)
        self.assertEqual(self.view.camera_label.text(), CAMERA_STATUS_TEXT[None])

    def test_camera_status_texts(self):
        for ready in (True, False, None):
            self.view.set_camera_ready(ready)
            self.assertEqual(self.view.camera_ready, ready)
            self.assertEqual(self.view.camera_label.text(), CAMERA_STATUS_TEXT[ready])

    def test_cycle_label(self):
        self.assertEqual(self.view.cycle_label.text(), f"세션 1/{LONG_BREAK_EVERY}")
        self.view.set_completed_sessions(LONG_BREAK_EVERY + 1)
        self.assertEqual(self.view.cycle_label.text(), f"세션 2/{LONG_BREAK_EVERY}")

    def test_paints_without_error(self):
        self.view.set_camera_ready(True)
        self.view.show_notice("안내")
        pixmap = self.view.grab()
        self.assertFalse(pixmap.isNull())


if __name__ == "__main__":
    unittest.main()
