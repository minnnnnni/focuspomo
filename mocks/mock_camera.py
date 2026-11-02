"""CameraThread·CvSessionWorker 대역 (합의한 명령·이벤트 인터페이스, INT-03에서 실제 cv_module로 교체).

- InstantMockCamera: 스레드 없이 시도 결과를 바로 이벤트 큐에 넣는다 (테스트, gui.main_window 화면 확인).
- MockCameraThread: 실제처럼 measure_sec초 뒤에 결과를 보낸다. 웹캠 없음·끊김도 흉내 낸다.
- MockCvSessionWorker: 세션 워커 형태. 슬롯은 웹캠 상태와 상관없이 패턴대로 보낸다.
"""
from __future__ import annotations

import queue
import threading
import time

from config.aggregation import CALIBRATION_SEC
from core.camera_link import (
    BEGIN_SESSION,
    CALIBRATION_RAW,
    CAMERA_FAILED,
    CAMERA_OPENED,
    CANCEL_CALIBRATION,
    END_SESSION,
    START_CALIBRATION,
    STOP,
    CameraLink,
)
from mocks.mock_calibration import MockCalibrator
from mocks.mock_cv_slots import MockCvWorker

NO_CAMERA_REASON = "mock: no camera"
LOST_CAMERA_REASON = "mock: camera lost"
REOPEN_SEC = 5.0  # 끊긴 뒤 다시 열릴 때까지의 시간
IDLE_WAIT_SEC = 0.5  # 기다릴 일이 없을 때 명령 큐를 기다리는 시간


class InstantMockCamera(CameraLink):
    """웹캠은 항상 열려 있고, start_calibration을 받으면 결과가 바로 이벤트 큐에 들어간다."""

    def __init__(self, calibrator: MockCalibrator | None = None) -> None:
        super().__init__()
        self.calibrator = calibrator or MockCalibrator("success")
        self.events.put({"type": CAMERA_OPENED})

    def start_calibration(self, calib_id: int, attempt: int) -> None:
        super().start_calibration(calib_id, attempt)
        raw = self.calibrator.run(time.time())
        self.events.put({"type": CALIBRATION_RAW, "calib_id": calib_id, "attempt": attempt, "raw": raw})


class MockCameraThread(threading.Thread):
    """명령을 받아 이벤트를 보낸다. 앱 수명 동안 돌고 stop 명령으로 끝난다.

    has_camera: False면 웹캠을 끝내 열지 못한다.
    lost_after_sec: 시작 후 이 시간이 지나면 끊기고, REOPEN_SEC 뒤 다시 열린다 (한 번만).
    """

    def __init__(
        self,
        link: CameraLink,
        scenario: str = "success",
        measure_sec: float = CALIBRATION_SEC,
        has_camera: bool = True,
        lost_after_sec: float | None = None,
    ) -> None:
        super().__init__(name="MockCameraThread", daemon=True)
        self.link = link
        self.calibrator = MockCalibrator(scenario)
        self.measure_sec = measure_sec
        self.has_camera = has_camera
        self.lost_after_sec = lost_after_sec
        self.session: tuple[float, float] | None = None  # 진행 중 세션의 (session_start, ear_baseline)
        self._attempt: dict | None = None  # 측정 중인 시도: calib_id, attempt, started_at, deadline
        self._lost_at: float | None = None
        self._reopen_at: float | None = None

    def run(self) -> None:
        if self.lost_after_sec is not None:
            self._lost_at = time.time() + self.lost_after_sec
        if self.has_camera:
            self.link.events.put({"type": CAMERA_OPENED})
        else:
            self.link.events.put({"type": CAMERA_FAILED, "reason": NO_CAMERA_REASON})
        while True:
            try:
                command = self.link.commands.get(timeout=self._wait_sec())
            except queue.Empty:
                pass
            else:
                if command["type"] == STOP:
                    return
                self._handle(command)
            self._advance(time.time())

    def _wait_sec(self) -> float:
        times = [self._lost_at, self._reopen_at, self._attempt["deadline"] if self._attempt else None]
        pending = [t for t in times if t is not None]
        return max(0.0, min(pending) - time.time()) if pending else IDLE_WAIT_SEC

    def _handle(self, command: dict) -> None:
        kind = command["type"]
        if kind == START_CALIBRATION:
            if self.has_camera:  # 웹캠이 닫혀 있으면 측정하지 않고 결과도 보내지 않는다
                now = time.time()
                self._attempt = {"calib_id": command["calib_id"], "attempt": command["attempt"],
                                 "started_at": now, "deadline": now + self.measure_sec}
        elif kind == CANCEL_CALIBRATION:
            if self._attempt is not None and self._attempt["calib_id"] == command["calib_id"]:
                self._attempt = None
        elif kind == BEGIN_SESSION:
            self.session = (command["session_start"], command["ear_baseline"])
        elif kind == END_SESSION:
            self.session = None

    def _advance(self, now: float) -> None:
        if self._lost_at is not None and now >= self._lost_at:
            self._lost_at = None
            self._reopen_at = now + REOPEN_SEC
            self.has_camera = False
            self._attempt = None  # 끊긴 시도는 결과를 보내지 않는다
            self.link.events.put({"type": CAMERA_FAILED, "reason": LOST_CAMERA_REASON})
        if self._reopen_at is not None and now >= self._reopen_at:
            self._reopen_at = None
            self.has_camera = True
            self.link.events.put({"type": CAMERA_OPENED})
        if self._attempt is not None and now >= self._attempt["deadline"]:
            raw = self.calibrator.run(self._attempt["started_at"])
            raw["ended_at"] = now
            self.link.events.put({"type": CALIBRATION_RAW, "calib_id": self._attempt["calib_id"],
                                  "attempt": self._attempt["attempt"], "raw": raw})
            self._attempt = None


class MockCvSessionWorker(MockCvWorker):
    """CvSessionWorker 대역: 세션 시작·종료를 CameraThread에 전달하고, 슬롯은 MockCvWorker처럼 보낸다."""

    def __init__(self, link: CameraLink, slot_queue: queue.Queue, session_start: float, ear_baseline: float) -> None:
        super().__init__(slot_queue, session_start, ear_baseline=ear_baseline)
        self.link = link

    def start(self) -> None:
        self.link.begin_session(self.session_start, self.ear_baseline)
        super().start()

    def stop(self) -> None:
        self.link.end_session()
        super().stop()
