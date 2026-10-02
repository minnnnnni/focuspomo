"""gui/calibration_view.py 카운트다운 테스트 (화면 없이 offscreen으로 실행).

QTimer를 실제로 기다리지 않고 tick()을 직접 불러 1초 경과를 흉내 낸다.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config.aggregation import CALIBRATION_SEC  # noqa: E402
from gui.calibration_view import RETRY_MESSAGE, START_MESSAGE, CalibrationView  # noqa: E402

app = QApplication.instance() or QApplication([])


class CalibrationViewTest(unittest.TestCase):
    def setUp(self):
        self.view = CalibrationView()
        self.finished: list[bool] = []
        self.cancelled: list[bool] = []
        self.view.countdown_finished.connect(lambda: self.finished.append(True))
        self.view.cancel_requested.connect(lambda: self.cancelled.append(True))

    def tearDown(self):
        self.view.stop()
        self.view.deleteLater()

    def test_message_uses_calibration_sec(self):
        self.assertIn(f"{CALIBRATION_SEC}초", self.view.message_label.text())

    def test_start_resets_and_runs(self):
        self.view.start()
        self.assertTrue(self.view.is_running)
        self.assertEqual(self.view.remaining, CALIBRATION_SEC)
        self.assertEqual(self.view.countdown_label.text(), str(CALIBRATION_SEC))

    def test_tick_decrements_label(self):
        self.view.start()
        self.view.tick()
        self.assertEqual(self.view.remaining, CALIBRATION_SEC - 1)
        self.assertEqual(self.view.countdown_label.text(), str(CALIBRATION_SEC - 1))
        self.assertEqual(self.finished, [])

    def test_finishes_once_at_zero(self):
        self.view.start()
        for _ in range(CALIBRATION_SEC + 2):  # 0초 뒤 tick은 무시
            self.view.tick()
        self.assertEqual(self.view.remaining, 0)
        self.assertEqual(self.finished, [True])
        self.assertFalse(self.view.is_running)

    def test_restart_after_finish(self):
        self.view.start()
        for _ in range(CALIBRATION_SEC):
            self.view.tick()
        self.view.start()
        self.assertEqual(self.view.remaining, CALIBRATION_SEC)
        self.assertTrue(self.view.is_running)

    def test_cancel_stops_without_finishing(self):
        self.view.start()
        self.view.tick()
        self.view.cancel_button.click()
        self.assertFalse(self.view.is_running)
        self.assertEqual(self.cancelled, [True])
        self.assertEqual(self.finished, [])

    def test_retry_shows_retry_message_and_restarts(self):
        self.view.start()
        for _ in range(CALIBRATION_SEC):
            self.view.tick()
        self.view.retry()
        self.assertEqual(self.view.message_label.text(), RETRY_MESSAGE)
        self.assertEqual(self.view.remaining, CALIBRATION_SEC)
        self.assertTrue(self.view.is_running)

    def test_start_restores_start_message(self):
        self.view.retry()
        self.view.start()
        self.assertEqual(self.view.message_label.text(), START_MESSAGE)


if __name__ == "__main__":
    unittest.main()
