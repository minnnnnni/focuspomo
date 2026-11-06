"""리뷰 반영 확인: 예외가 나도 CameraThread·리더가 살아 있고, GUI가 상태를 알 수 있는지 (웹캠 없이)."""
from __future__ import annotations

import queue
import time
import unittest

from core.camera_link import (CALIBRATION_RAW, CAMERA_FAILED, CAMERA_OPENED, START_CALIBRATION,
                              CameraLink)
from cv_module.worker import CameraThread, FakeEarSource, MAX_FRAME_ERRORS


class FlakySource(FakeEarSource):
    """열기·읽기·추정에서 예외를 내거나, 열리지만 프레임이 안 나오거나, 첫 읽기가 느린 소스."""

    def __init__(self, *, open_exc: int = 0, read_exc: int = 0, est_exc=lambda n: False,
                 no_frames: bool = False, first_read_delay: float = 0.0) -> None:
        super().__init__(lambda t: 0.3)
        self.open_exc, self.read_exc, self.est_exc = open_exc, read_exc, est_exc
        self.no_frames, self.first_read_delay = no_frames, first_read_delay
        self.est_calls = 0
        self._first = True

    def open(self) -> bool:
        if self.open_exc:
            self.open_exc -= 1
            self.open_calls += 1
            raise RuntimeError("open boom")
        self._first = True
        return super().open()

    def read(self):
        if self.read_exc:
            self.read_exc -= 1
            raise RuntimeError("read boom")
        if self._first and self.first_read_delay:
            time.sleep(self.first_read_delay)
        self._first = False
        frame = super().read()
        return None if self.no_frames else frame

    def estimate(self, frame):
        self.est_calls += 1
        if self.est_exc(self.est_calls):
            raise ValueError("estimate boom")
        return super().estimate(frame)


class QuickCalibrator:
    """실제 Calibrator는 CALIBRATION_SEC(5초) 동안 측정하므로, 테스트에서는 프레임 5개로 끝낸다."""

    def __init__(self, started_at: float) -> None:
        self.frames = 0

    def add_frame(self, ts: float, ear: float | None) -> None:
        self.frames += 1

    @property
    def done(self) -> bool:
        return self.frames >= 5

    def finish(self) -> dict:
        return {"ear_baseline": 0.3, "face_detected_ratio": 1.0}


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


def kinds(events: list[dict]) -> list[tuple[str, str | None]]:
    return [(e["type"], e.get("reason")) for e in events if e["type"] in (CAMERA_OPENED, CAMERA_FAILED)]


def is_type(t: str):
    return lambda e: e["type"] == t


class RobustnessTest(unittest.TestCase):
    def make(self, source: FakeEarSource) -> CameraThread:
        self.link = CameraLink()
        cam = CameraThread(self.link, queue.Queue(), source=source, lost_sec=0.3, reopen_sec=0.2,
                           calibrator_factory=QuickCalibrator)
        cam.start()
        self.addCleanup(lambda: (self.link.stop(), cam.join(2)))
        return cam

    def calibrate(self, attempt: int = 1) -> None:
        self.link.commands.put({"type": START_CALIBRATION, "calib_id": "c", "attempt": attempt})

    # 리뷰 1: 프레임 처리 예외
    def test_one_bad_frame_keeps_thread_and_calibration_works(self):
        cam = self.make(FlakySource(est_exc=lambda n: n == 2))
        self.assertTrue(collect(self.link, 1.0, until=is_type(CAMERA_OPENED)))
        self.calibrate()
        ev = collect(self.link, 2.0, until=is_type(CALIBRATION_RAW))
        self.assertEqual(ev[-1]["type"], CALIBRATION_RAW)
        self.assertTrue(cam.is_alive())
        self.calibrate(2)  # 그 뒤 요청에도 응답한다
        self.assertEqual(collect(self.link, 2.0, until=is_type(CALIBRATION_RAW))[-1]["type"], CALIBRATION_RAW)

    def test_estimate_keeps_failing_reports_cv_error(self):
        cam = self.make(FlakySource(est_exc=lambda n: True))
        self.assertTrue(collect(self.link, 1.0, until=is_type(CAMERA_OPENED)))
        self.calibrate()
        ev = collect(self.link, 3.0, until=is_type(CAMERA_FAILED))
        self.assertEqual(kinds(ev), [(CAMERA_FAILED, "cv_error")])
        self.assertTrue(cam.is_alive())

    def test_recovers_after_cv_error(self):
        self.make(FlakySource(est_exc=lambda n: 1 <= n <= MAX_FRAME_ERRORS + 5))
        self.assertTrue(collect(self.link, 1.0, until=is_type(CAMERA_OPENED)))
        self.calibrate()
        ev = collect(self.link, 4.0, until=is_type(CAMERA_OPENED))
        self.assertEqual(kinds(ev), [(CAMERA_FAILED, "cv_error"), (CAMERA_OPENED, None)])

    def test_unexpected_thread_exit_reports_failed(self):
        cam = self.make(FlakySource())
        self.assertTrue(collect(self.link, 1.0, until=is_type(CAMERA_OPENED)))

        def boom():
            raise RuntimeError("cmd boom")

        cam._handle_commands = boom
        ev = collect(self.link, 1.0, until=is_type(CAMERA_FAILED))
        self.assertEqual(kinds(ev), [(CAMERA_FAILED, "cv_error")])
        cam.join(2)
        self.assertFalse(cam.is_alive())

    def test_normal_stop_sends_nothing(self):
        cam = self.make(FlakySource())
        self.assertTrue(collect(self.link, 1.0, until=is_type(CAMERA_OPENED)))
        self.link.stop()
        cam.join(2)
        self.assertEqual(collect(self.link, 0.3), [])

    # 리뷰 2: 프레임을 받기 전에는 준비 완료가 아님
    def test_open_without_frames_does_not_flap(self):
        self.make(FlakySource(no_frames=True))
        ev = collect(self.link, 2.5)
        self.assertEqual(kinds(ev), [(CAMERA_FAILED, "open_failed")])

    def test_slow_first_read_no_fake_loss(self):
        self.make(FlakySource(first_read_delay=0.6))  # lost_sec(0.3)보다 길다
        ev = collect(self.link, 1.5)
        self.assertEqual(kinds(ev), [(CAMERA_OPENED, None)])

    # 리뷰 3: 리더 예외
    def test_open_exception_reports_then_recovers(self):
        src = FlakySource(open_exc=2)
        self.make(src)
        ev = collect(self.link, 2.0, until=is_type(CAMERA_OPENED))
        self.assertEqual(kinds(ev), [(CAMERA_FAILED, "open_failed"), (CAMERA_OPENED, None)])
        self.assertGreaterEqual(src.open_calls, 3)

    def test_read_exception_keeps_reader(self):
        cam = self.make(FlakySource(read_exc=3))
        ev = collect(self.link, 1.5)
        self.assertEqual(kinds(ev), [(CAMERA_OPENED, None)])  # 끊김 없이 열림 한 번
        self.assertTrue(cam._reader.is_alive())


if __name__ == "__main__":
    unittest.main()