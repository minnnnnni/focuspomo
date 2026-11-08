"""main.py 연결 테스트: 세션 시작·종료 시그널이 세션 워커까지 가는지 (offscreen)."""
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main  # noqa: E402
from gui.main_window import Screen  # noqa: E402
from gui.timer_view import SESSION_SEC  # noqa: E402
from mocks.mock_calibration import make_calibration  # noqa: E402
from mocks.mock_camera import MockCameraThread, MockCvSessionWorker  # noqa: E402
from mocks.mock_cv_slots import FULL_FOCUS  # noqa: E402
from mocks.mock_session import build_session, messages_only  # noqa: E402
from mocks.mock_window_slots import APP_VSCODE  # noqa: E402
from cv_module.worker import CameraThread, FakeEarSource  # noqa: E402
from window_tracker.tracker import WindowTracker, empty_sample  # noqa: E402

app = QApplication.instance() or QApplication([])
JOIN_TIMEOUT = 5.0


def fast_camera(link):
    return MockCameraThread(link, measure_sec=0.01)


def wait_until(condition, timeout_ms: int = 3000) -> None:
    waited = 0
    while not condition() and waited < timeout_ms:
        QTest.qWait(10)
        waited += 10
    assert condition(), "조건이 시간 안에 참이 되지 않았습니다"


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
        self.app = main.build_app(Path(tmp.name), factories=[ScriptedWorker], camera_thread_factory=fast_camera)
        self.window, self.workers = self.app.window, self.app.workers
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.app.shutdown)
        self.app.start()
        wait_until(lambda: self.window.idle_view.camera_ready is True)

    def start_session(self) -> ScriptedWorker:
        self.window.go_calibration()
        self.window._on_calibration_finished()
        wait_until(lambda: self.window.screen is Screen.TIMER)
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
    def test_window_factory_turns_on_domain_media_and_youtube(self):
        # 유튜브 카테고리 기본 켬 (2026-11-09 결정, 점수 명세 2.1)
        tracker = main.window_worker_factory()(queue.Queue(), 1.0)
        self.assertIsInstance(tracker, WindowTracker)
        self.assertEqual(tracker.session_start, 1.0)
        self.assertTrue(tracker.use_domain_probe)
        self.assertTrue(tracker.use_media)
        self.assertTrue(tracker.use_youtube)

    def test_youtube_can_be_turned_off(self):
        tracker = main.window_worker_factory(use_youtube=False)(queue.Queue(), 1.0)
        self.assertFalse(tracker.use_youtube)
        self.assertTrue(tracker.use_domain_probe)

    def test_default_app_uses_mock_cv_and_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = main.build_app(Path(tmp))
            self.addCleanup(built.window.deleteLater)
            built.window.calibration = make_calibration(0.0, 1.0)
            with patch.object(threading.Thread, "start"):
                built.workers.start(1.0)
            self.assertEqual(built.workers.running_count, 2)
            built.workers.stop()

    def test_cv_worker_gets_baseline_from_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = main.build_app(Path(tmp))
            self.addCleanup(built.window.deleteLater)
            built.window.calibration = {**make_calibration(0.0, 1.0), "ear_baseline": 0.27}
            worker = main.cv_worker_factory(built.camera, built.window)(queue.Queue(), 1.0)
            self.assertIsInstance(worker, MockCvSessionWorker)
            self.assertEqual((worker.session_start, worker.ear_baseline), (1.0, 0.27))


class ShutdownTest(unittest.TestCase):
    def test_stops_session_worker_then_camera_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = main.build_app(Path(tmp), factories=[ScriptedWorker], camera_thread_factory=fast_camera)
            self.addCleanup(built.window.deleteLater)
            built.start()
            built.workers.start(1.0)
            worker = ScriptedWorker.created[-1]
            built.shutdown()
            self.assertFalse(worker.is_alive())
            self.assertFalse(built.camera_thread.is_alive())

    def test_shutdown_before_start_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = main.build_app(Path(tmp))
            self.addCleanup(built.window.deleteLater)
            built.shutdown()


class TrackingEarSource(FakeEarSource):
    """웹캠을 몇 번 열고 닫았는지 센다. prepare_sec만큼 모델 로딩을 흉내 낸다."""

    def __init__(self, prepare_sec: float = 0.0) -> None:
        super().__init__(lambda t: 0.3)
        self.prepare_sec = prepare_sec
        self.close_calls = 0
        self.estimator_closed = False

    def prepare(self) -> None:
        time.sleep(self.prepare_sec)

    def close(self) -> None:
        self.close_calls += 1
        super().close()

    def close_estimator(self) -> None:
        self.estimator_closed = True

    @property
    def is_open(self) -> bool:
        return self._opened


class RealThreadShutdownTest(unittest.TestCase):
    """INT-06 (개인 2-07): 실제 CameraThread·CvSessionWorker·WindowTracker로 앱 종료 정리를 확인한다.

    웹캠은 FakeEarSource, 창 조회는 가짜 함수라 하드웨어·Windows API는 쓰지 않는다.
    """

    def build(self, source: TrackingEarSource):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = source
        self.trackers: list[WindowTracker] = []

        def window_factory(slot_queue, session_start):
            tracker = WindowTracker(slot_queue, session_start, sample_fn=lambda ts: empty_sample(ts))
            self.trackers.append(tracker)
            return tracker

        with patch("main.window_worker_factory", lambda use_youtube=True: window_factory):
            built = main.build_app(Path(tmp.name), camera_thread_factory=lambda link: CameraThread(
                link, source=source, lost_sec=0.3, reopen_sec=0.2))
        self.addCleanup(built.window.deleteLater)
        self.addCleanup(lambda: (built.camera.stop(), built.camera_thread.join(5)))
        return built

    def assert_all_released(self, built) -> None:
        self.assertFalse(built.camera_thread.is_alive())
        self.assertFalse(built.camera_thread._reader.is_alive())
        self.assertFalse(self.source.is_open, "웹캠이 닫히지 않았습니다")
        self.assertGreaterEqual(self.source.close_calls, 1)
        self.assertTrue(self.source.estimator_closed)
        self.assertTrue(all(not t.is_alive() for t in self.trackers))

    def test_quit_while_idle_releases_webcam(self):
        built = self.build(TrackingEarSource())
        built.start()
        wait_until(lambda: built.window.idle_view.camera_ready is True)
        self.assertTrue(self.source.is_open)
        started = time.monotonic()
        built.shutdown()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assert_all_released(built)

    def test_quit_during_session_flushes_and_releases(self):
        built = self.build(TrackingEarSource())
        built.start()
        wait_until(lambda: built.window.idle_view.camera_ready is True)
        built.window.calibration = {**make_calibration(0.0, 1.0), "ear_baseline": 0.3}
        built.workers.start(time.time())
        self.assertEqual(built.workers.running_count, 2)
        QTest.qWait(1300)  # CV가 1초 요약을 하나 이상 만들도록
        started = time.monotonic()
        built.shutdown()
        self.assertLess(time.monotonic() - started, 2.5)
        self.assert_all_released(built)
        sources = sorted({msg["source"] for msg in messages_from(built.workers.slot_queue)})
        self.assertEqual(sources, ["cv", "window"])  # 두 워커 모두 마지막 슬롯을 넣고 끝남

    def test_quit_while_model_loading_does_not_hang(self):
        """모델 로딩 중에는 stop 명령을 처리하지 못한다. 종료는 기다림 상한에서 끝나고, 로딩 뒤 웹캠을 놓는다."""
        built = self.build(TrackingEarSource(prepare_sec=3.0))
        built.start()
        wait_until(lambda: self.source.is_open)
        started = time.monotonic()
        built.shutdown()
        self.assertLess(time.monotonic() - started, main.CAMERA_JOIN_TIMEOUT_SEC + 1.0)
        built.camera_thread.join(5)
        self.assert_all_released(built)


def messages_from(q: queue.Queue) -> list:
    return [q.get_nowait() for _ in range(q.qsize())]


if __name__ == "__main__":
    unittest.main()
