"""CameraThread·CvSessionWorker 단위 테스트 (CV-05). 웹캠 없이 FakeEarSource로 확인한다.

    python -m unittest tests.test_cv_worker -v
"""
from __future__ import annotations

import queue
import time
import unittest

from core.camera_link import CALIBRATION_RAW, CAMERA_FAILED, CAMERA_OPENED, CameraLink
from cv_module.worker import REASON_LOST, REASON_OPEN_FAILED, CameraThread, CvSessionWorker, FakeEarSource

CALIB_SEC = 0.3


class FakeCalibrator:
    """CALIB_SEC 뒤에 끝나는 캘리브레이터 (cv_module.calibration.Calibrator와 같은 메서드)."""

    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self.ended_at: float | None = None
        self.frames = 0

    @property
    def done(self) -> bool:
        return self.ended_at is not None

    def add_frame(self, ts: float, ear: float | None) -> None:
        if self.done:
            return
        if ts - self.started_at >= CALIB_SEC:
            self.ended_at = ts
            return
        self.frames += 1

    def finish(self) -> dict:
        return {"ear_baseline": 0.3, "valid_frames": self.frames, "face_detected_ratio": 1.0,
                "started_at": self.started_at, "ended_at": self.ended_at, "fallback_used": False}


class FakePipeline:
    """프레임 5개마다 슬롯 메시지 하나, finish()에서 마지막 슬롯 하나."""

    def __init__(self, session_start: float, ear_baseline: float) -> None:
        self.session_start = session_start
        self.frames: list[float] = []

    def add_frame(self, ts: float, ear: float | None) -> list[dict]:
        self.frames.append(ts)
        return [{"source": "cv", "n": len(self.frames)}] if len(self.frames) % 5 == 0 else []

    def finish(self) -> list[dict]:
        return [{"source": "cv", "final": True}]


def collect(link: CameraLink, timeout: float, until=None) -> list[dict]:
    out, end = [], time.time() + timeout
    while time.time() < end:
        try:
            event = link.events.get(timeout=0.05)
        except queue.Empty:
            continue
        out.append(event)
        if until and until(event):
            break
    return out


def opened(event: dict) -> bool:
    return event["type"] == CAMERA_OPENED


class Base(unittest.TestCase):
    def make(self, source: FakeEarSource | None = None, **kw) -> CameraThread:
        self.link = CameraLink()
        self.slot_q: queue.Queue = queue.Queue()
        self.source = source or FakeEarSource(lambda t: 0.3)
        self.pipelines: list[FakePipeline] = []

        def pipeline(start: float, base: float) -> FakePipeline:
            self.pipelines.append(FakePipeline(start, base))
            return self.pipelines[-1]

        cam = CameraThread(self.link, source=self.source, lost_sec=0.3, reopen_sec=0.2,
                           calibrator_factory=FakeCalibrator, pipeline_factory=pipeline, **kw)
        cam.start()
        self.addCleanup(lambda: (self.link.stop(), cam.join(2)))
        return cam

    def make_open(self, source: FakeEarSource | None = None, **kw) -> CameraThread:
        cam = self.make(source, **kw)
        self.assertTrue(collect(self.link, 1.0, until=opened))
        return cam

    def worker(self, cam: CameraThread, session_start: float | None = None) -> CvSessionWorker:
        return CvSessionWorker(self.link, self.slot_q, session_start or time.time(), 0.3, camera_thread=cam)


class SlowPrepareSource(FakeEarSource):
    def __init__(self, delay: float) -> None:
        super().__init__(lambda t: 0.3)
        self.delay = delay

    def prepare(self) -> None:
        time.sleep(self.delay)


class BrokenPrepareSource(FakeEarSource):
    def prepare(self) -> None:
        raise RuntimeError("model missing")


class CameraStateTest(Base):
    def test_opened_only_after_detector_ready(self):
        self.make(SlowPrepareSource(0.6))
        t0 = time.time()
        self.assertTrue(collect(self.link, 2.0, until=opened))
        self.assertGreaterEqual(time.time() - t0, 0.55)

    def test_detector_failure_reported(self):
        cam = self.make(BrokenPrepareSource(lambda t: 0.3))
        events = collect(self.link, 1.0)
        self.assertEqual([(e["type"], e.get("reason")) for e in events], [(CAMERA_FAILED, "detector_failed")])
        cam.join(2)
        self.assertFalse(cam.is_alive())

    def test_first_state_sent_once(self):
        self.make()
        self.assertEqual([e["type"] for e in collect(self.link, 1.0)], [CAMERA_OPENED])

    def test_open_failed_then_recovers_only_state_changes(self):
        src = FakeEarSource(lambda t: 0.3)
        src.fail_open = True
        self.make(src)
        time.sleep(0.7)  # 여러 번 다시 열어 봐도 이벤트는 한 번
        src.fail_open = False
        events = collect(self.link, 1.0, until=opened)
        self.assertEqual([(e["type"], e.get("reason")) for e in events],
                         [(CAMERA_FAILED, REASON_OPEN_FAILED), (CAMERA_OPENED, None)])
        self.assertGreater(src.open_calls, 2)

    def test_no_analysis_outside_calibration_and_session(self):
        seen: list[float] = []
        self.make_open(FakeEarSource(lambda t: seen.append(t) or 0.3))
        time.sleep(0.4)
        self.assertEqual(seen, [])


class CalibrationTest(Base):
    def test_raw_returned_with_calib_id_and_attempt(self):
        self.make_open()
        self.link.start_calibration(1, 1)
        event = collect(self.link, 2.0, until=lambda e: e["type"] == CALIBRATION_RAW)[-1]
        self.assertEqual((event["type"], event["calib_id"], event["attempt"]), (CALIBRATION_RAW, 1, 1))
        self.assertGreater(event["raw"]["valid_frames"], 0)
        self.assertEqual(self.link.drain_events(), [])

    def test_cancelled_attempt_sends_nothing(self):
        self.make_open()
        self.link.start_calibration(1, 1)
        time.sleep(0.1)
        self.link.cancel_calibration(1)
        self.link.start_calibration(2, 1)
        raws = [e for e in collect(self.link, 1.2) if e["type"] == CALIBRATION_RAW]
        self.assertEqual([e["calib_id"] for e in raws], [2])

    def test_lost_during_calibration_sends_failed_without_raw(self):
        self.make_open()
        self.source.fail_read = True
        self.link.start_calibration(1, 1)
        events = collect(self.link, 1.2)
        self.assertIn((CAMERA_FAILED, REASON_LOST), [(e["type"], e.get("reason")) for e in events])
        self.assertNotIn(CALIBRATION_RAW, [e["type"] for e in events])

    def test_request_while_camera_closed_is_ignored(self):
        src = FakeEarSource(lambda t: 0.3)
        src.fail_open = True
        self.make(src)
        collect(self.link, 0.5)
        self.link.start_calibration(1, 1)
        self.assertNotIn(CALIBRATION_RAW, [e["type"] for e in collect(self.link, 0.6)])


class SessionTest(Base):
    def test_last_slot_flushed_and_alive_until_then(self):
        cam = self.make_open()
        w = self.worker(cam)
        self.assertFalse(w.is_alive())
        w.start()
        time.sleep(0.6)
        self.assertTrue(w.is_alive())
        t0 = time.time()
        w.stop()
        w.join(2)
        self.assertLess(time.time() - t0, 2.0)
        self.assertFalse(w.is_alive())
        msgs = list(self.slot_q.queue)
        self.assertTrue(any("n" in m for m in msgs))
        self.assertTrue(msgs[-1].get("final"))

    def test_end_session_fast_while_camera_reopening(self):
        cam = self.make_open()
        w = self.worker(cam)
        w.start()
        time.sleep(0.3)
        self.source.fail_read = True
        self.source.fail_open = True
        time.sleep(0.6)
        t0 = time.time()
        w.stop()
        w.join(2)
        self.assertLess(time.time() - t0, 0.5)
        self.assertTrue(list(self.slot_q.queue)[-1].get("final"))

    def test_frames_before_session_start_dropped(self):
        cam = self.make_open()
        start = time.time() + 0.4
        w = self.worker(cam, start)
        w.start()
        time.sleep(0.8)
        w.stop()
        w.join(2)
        self.assertTrue(self.pipelines[0].frames)
        self.assertGreaterEqual(min(self.pipelines[0].frames), start)

    def test_fps_limited(self):
        seen: list[float] = []
        cam = self.make_open(FakeEarSource(lambda t: seen.append(t) or 0.3, fps=60), target_fps=15)
        w = self.worker(cam)
        w.start()
        time.sleep(1.0)
        w.stop()
        w.join(2)
        self.assertGreaterEqual(len(seen), 10)
        self.assertLessEqual(len(seen), 17)

    def test_app_stop_ends_running_session(self):
        cam = self.make_open()
        w = self.worker(cam)
        w.start()
        time.sleep(0.2)
        self.link.stop()
        cam.join(2)
        self.assertFalse(cam.is_alive())
        self.assertFalse(w.is_alive())
        self.assertTrue(list(self.slot_q.queue)[-1].get("final"))

    def test_stale_end_does_not_end_new_session(self):
        cam = self.make_open()
        old = self.worker(cam)
        old.start()
        time.sleep(0.2)
        new = self.worker(cam)
        new.start()  # 이전 세션은 먼저 마감된다
        old.stop()   # 늦게 온 이전 세션의 end_session
        time.sleep(0.3)
        self.assertFalse(old.is_alive())
        self.assertTrue(new.is_alive())
        new.stop()
        new.join(2)
        self.assertFalse(new.is_alive())


if __name__ == "__main__":
    unittest.main()