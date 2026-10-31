"""main.py 연결 테스트: 세션 시작·종료 시그널이 세션 워커까지 가는지 (offscreen)."""
import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import main  # noqa: E402
from gui.main_window import Screen  # noqa: E402
from gui.timer_view import SESSION_SEC  # noqa: E402
from mocks.mock_cv_slots import FULL_FOCUS, MockCvWorker  # noqa: E402
from mocks.mock_session import build_session, messages_only  # noqa: E402
from mocks.mock_window_slots import APP_VSCODE  # noqa: E402
from window_tracker.tracker import WindowTracker  # noqa: E402

app = QApplication.instance() or QApplication([])
JOIN_TIMEOUT = 5.0


class ScriptedWorker(threading.Thread):
    """stop() 때 준비된 메시지를 넣는 워커 (실제 스레드처럼 마지막 슬롯을 flush)."""

    created: list["ScriptedWorker"] = []

    def __init__(self, slot_queue: queue.Queue, session_start: float) -> None:
        super().__init__(daemon=True)
        self.slot_queue = slot_queue
        self.session_start = session_start
        self.messages: list = []
        self._stop_event = threading.Event()
        ScriptedWorker.created.append(self)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._stop_event.wait()
        for msg in self.messages:
            self.slot_queue.put(msg)


class BuildAppTest(unittest.TestCase):
    def setUp(self):
        ScriptedWorker.created = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch("gui.main_window.FLUSH_WAIT_MS", 0)
        p.start()
        self.addCleanup(p.stop)
        self.window, self.workers = main.build_app(Path(tmp.name), factories=[ScriptedWorker])
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.workers.shutdown)

    def start_session(self) -> ScriptedWorker:
        self.window.go_calibration()
        self.window._on_calibration_finished()
        self.assertIs(self.window.screen, Screen.TIMER)
        return ScriptedWorker.created[-1]

    def test_session_start_reaches_worker(self):
        worker = self.start_session()
        self.assertTrue(worker.is_alive())
        self.assertIs(worker.slot_queue, self.window.slot_queue)
        self.assertEqual(worker.session_start, self.window.session_start)

    def test_session_end_stops_worker(self):
        worker = self.start_session()
        self.window.end_session()
        worker.join(JOIN_TIMEOUT)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.workers.running_count, 0)

    def test_each_session_gets_new_worker(self):
        first = self.start_session()
        self.window.end_session()
        first.join(JOIN_TIMEOUT)
        self.assertIsNot(self.start_session(), first)
        self.assertEqual(len(ScriptedWorker.created), 2)

    def test_shutdown_stops_running_worker(self):
        worker = self.start_session()
        self.workers.shutdown(timeout=JOIN_TIMEOUT)
        self.assertFalse(worker.is_alive())

    def test_slot_after_time_up_is_not_scored(self):
        # 25분 종료 뒤 stop 전까지 스레드가 다음 슬롯을 만든 경우 (2-04)
        self.start_session()
        start = self.window.session_start
        n_slots = SESSION_SEC // 10
        for msg in messages_only(build_session(start, [FULL_FOCUS] * (n_slots + 1), [APP_VSCODE] * (n_slots + 1))):
            self.window.slot_queue.put(msg)
        self.window._on_time_up()
        slots = [s["slot"] for s in self.window.session_result["slot_scores"]]
        self.assertEqual(slots, list(range(n_slots)))
        self.assertEqual(self.window.session_output.focus_log["merged_slots"][-1]["slot"], n_slots - 1)


class DefaultFactoriesTest(unittest.TestCase):
    def test_window_factory_turns_on_domain_and_media_not_youtube(self):
        tracker = main.window_worker_factory()(queue.Queue(), 1.0)
        self.assertIsInstance(tracker, WindowTracker)
        self.assertEqual(tracker.session_start, 1.0)
        self.assertTrue(tracker.use_domain_probe)
        self.assertTrue(tracker.use_media)
        self.assertFalse(tracker.use_youtube)

    def test_youtube_is_opt_in(self):
        self.assertTrue(main.window_worker_factory(use_youtube=True)(queue.Queue(), 1.0).use_youtube)

    def test_default_app_uses_mock_cv_and_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            window, workers = main.build_app(Path(tmp))
            self.addCleanup(window.deleteLater)
            with patch.object(WindowTracker, "start"), patch.object(MockCvWorker, "start"):
                workers.start(1.0)
            self.assertEqual(workers.running_count, 2)
            workers.stop()


if __name__ == "__main__":
    unittest.main()
