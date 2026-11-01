"""core/camera_link.py와 mocks/mock_camera.py 테스트 (GUI 없이)."""
import queue
import time
import unittest
from unittest.mock import patch

from core.camera_link import (
    BEGIN_SESSION,
    CALIBRATION_RAW,
    CAMERA_FAILED,
    CAMERA_OPENED,
    END_SESSION,
    CameraLink,
)
from mocks.mock_calibration import make_calibration
from mocks.mock_camera import (
    LOST_CAMERA_REASON,
    NO_CAMERA_REASON,
    InstantMockCamera,
    MockCameraThread,
    MockCvSessionWorker,
)
from schemas import slot_start

TIMEOUT = 3.0


class DrainEventsTest(unittest.TestCase):
    def test_keeps_arrival_order(self):
        link = CameraLink()
        link.events.put({"type": CAMERA_FAILED, "reason": "x"})
        link.events.put({"type": CAMERA_OPENED})
        self.assertEqual([e["type"] for e in link.drain_events()], [CAMERA_FAILED, CAMERA_OPENED])
        self.assertEqual(link.drain_events(), [])

    def test_bad_events_are_skipped(self):
        link = CameraLink()
        good = {"type": CAMERA_OPENED}
        for bad in ({"type": "nope"}, {}, None, {"type": CAMERA_FAILED},
                    {"type": CALIBRATION_RAW, "calib_id": 1, "attempt": 1},
                    {"type": CALIBRATION_RAW, "calib_id": 1, "attempt": 1, "raw": {"ear_baseline": 0.3}}):
            link.events.put(bad)
        link.events.put(good)
        with self.assertLogs("core.camera_link", level="WARNING") as logs:
            self.assertEqual(link.drain_events(), [good])
        self.assertEqual(len(logs.output), 6)


class InstantMockCameraTest(unittest.TestCase):
    def test_opens_and_answers_immediately(self):
        camera = InstantMockCamera()
        camera.start_calibration(7, 1)
        opened, raw = camera.drain_events()
        self.assertEqual(opened["type"], CAMERA_OPENED)
        self.assertEqual((raw["type"], raw["calib_id"], raw["attempt"]), (CALIBRATION_RAW, 7, 1))
        self.assertFalse(raw["raw"]["fallback_used"])


class MockCameraThreadTest(unittest.TestCase):
    def start(self, **kwargs) -> tuple[CameraLink, MockCameraThread]:
        link = CameraLink()
        thread = MockCameraThread(link, **kwargs)
        thread.start()
        self.addCleanup(thread.join, TIMEOUT)
        self.addCleanup(link.stop)
        return link, thread

    def next_event(self, link: CameraLink) -> dict:
        return link.events.get(timeout=TIMEOUT)

    def test_reports_camera_opened_then_result(self):
        link, _ = self.start(measure_sec=0.01)
        self.assertEqual(self.next_event(link)["type"], CAMERA_OPENED)
        before = time.time()
        link.start_calibration(1, 1)
        event = self.next_event(link)
        self.assertEqual((event["type"], event["calib_id"], event["attempt"]), (CALIBRATION_RAW, 1, 1))
        self.assertGreaterEqual(event["raw"]["ended_at"], before)
        self.assertLessEqual(event["raw"]["ended_at"], time.time())

    def test_cancelled_attempt_sends_no_result(self):
        link, _ = self.start(measure_sec=0.1)
        self.next_event(link)
        link.start_calibration(1, 1)
        link.cancel_calibration(1)
        with self.assertRaises(queue.Empty):
            link.events.get(timeout=0.3)

    def test_cancel_of_other_id_keeps_attempt(self):
        link, _ = self.start(measure_sec=0.05)
        self.next_event(link)
        link.start_calibration(2, 1)
        link.cancel_calibration(1)
        self.assertEqual(self.next_event(link)["calib_id"], 2)

    def test_no_camera(self):
        link, _ = self.start(has_camera=False, measure_sec=0.01)
        self.assertEqual(self.next_event(link), {"type": CAMERA_FAILED, "reason": NO_CAMERA_REASON})
        link.start_calibration(1, 1)
        with self.assertRaises(queue.Empty):
            link.events.get(timeout=0.2)

    def test_lost_during_calibration_sends_failed_without_result(self):
        with patch("mocks.mock_camera.REOPEN_SEC", 0.05):
            link, _ = self.start(measure_sec=1.0, lost_after_sec=0.05)
            self.next_event(link)
            link.start_calibration(1, 1)
            self.assertEqual(self.next_event(link), {"type": CAMERA_FAILED, "reason": LOST_CAMERA_REASON})
            self.assertEqual(self.next_event(link), {"type": CAMERA_OPENED})
        with self.assertRaises(queue.Empty):
            link.events.get(timeout=1.2)

    def test_stop_ends_thread(self):
        link, thread = self.start()
        link.stop()
        thread.join(TIMEOUT)
        self.assertFalse(thread.is_alive())


class MockCvSessionWorkerTest(unittest.TestCase):
    def test_forwards_session_to_camera_and_flushes_with_baseline(self):
        link = CameraLink()
        slots: queue.Queue = queue.Queue()
        start = time.time() - 3.5  # 진행 중 슬롯에 3초가 지났다
        worker = MockCvSessionWorker(link, slots, start, 0.2)
        worker.start()
        self.assertEqual(link.commands.get_nowait(),
                         {"type": BEGIN_SESSION, "session_start": start, "ear_baseline": 0.2})
        worker.stop()
        worker.join(TIMEOUT)
        self.assertFalse(worker.is_alive())
        self.assertEqual(link.commands.get_nowait(), {"type": END_SESSION})
        msg = slots.get_nowait()
        self.assertEqual(msg["slot"]["timestamp"], slot_start(0, start))
        self.assertEqual(len(msg["seconds"]), 3)
        self.assertEqual(msg["seconds"][0]["ear_mean"], 0.2)


class MakeCalibrationCompatTest(unittest.TestCase):
    def test_required_keys_accepted(self):
        link = CameraLink()
        event = {"type": CALIBRATION_RAW, "calib_id": 1, "attempt": 1, "raw": make_calibration(1.0, 1.0)}
        link.events.put(event)
        self.assertEqual(link.drain_events(), [event])


if __name__ == "__main__":
    unittest.main()
