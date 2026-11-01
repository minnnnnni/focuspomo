"""GUI와 CameraThread 사이의 명령·이벤트 큐 (개발계획서 2.0, 2.4, INT-03).

GUI 없이 테스트할 수 있도록 PySide6를 import하지 않는다.

명령(GUI·세션 워커 → CameraThread)과 이벤트(CameraThread → GUI)는 모두 "type" 키를 가진 dict다.
이벤트 큐는 메인 스레드만 읽는다. 형식이 잘못된 이벤트는 경고 로그만 남기고 버린다.
"""
from __future__ import annotations

import logging
import queue

from schemas import Calibration

logger = logging.getLogger(__name__)

# 명령
START_CALIBRATION = "start_calibration"  # calib_id, attempt
CANCEL_CALIBRATION = "cancel_calibration"  # calib_id
BEGIN_SESSION = "begin_session"  # session_start, ear_baseline
END_SESSION = "end_session"
STOP = "stop"

# 이벤트
CAMERA_OPENED = "camera_opened"
CAMERA_FAILED = "camera_failed"  # reason
CALIBRATION_RAW = "calibration_raw"  # calib_id, attempt, raw (Calibration)

_EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    CAMERA_OPENED: (),
    CAMERA_FAILED: ("reason",),
    CALIBRATION_RAW: ("calib_id", "attempt", "raw"),
}


def _check_event(event: dict) -> None:
    """형식이 잘못되면 KeyError·TypeError를 낸다."""
    for field in _EVENT_FIELDS[event["type"]]:
        event[field]
    if event["type"] == CALIBRATION_RAW:
        for field in Calibration.__required_keys__:
            event["raw"][field]


class CameraLink:
    """명령 큐와 이벤트 큐를 묶는다. CameraThread와 세션 워커에도 같은 큐를 넘긴다."""

    def __init__(self, commands: queue.Queue | None = None, events: queue.Queue | None = None) -> None:
        self.commands: queue.Queue = commands if commands is not None else queue.Queue()
        self.events: queue.Queue = events if events is not None else queue.Queue()

    def start_calibration(self, calib_id: int, attempt: int) -> None:
        self.commands.put({"type": START_CALIBRATION, "calib_id": calib_id, "attempt": attempt})

    def cancel_calibration(self, calib_id: int) -> None:
        self.commands.put({"type": CANCEL_CALIBRATION, "calib_id": calib_id})

    def begin_session(self, session_start: float, ear_baseline: float) -> None:
        self.commands.put({"type": BEGIN_SESSION, "session_start": session_start, "ear_baseline": ear_baseline})

    def end_session(self) -> None:
        self.commands.put({"type": END_SESSION})

    def stop(self) -> None:
        self.commands.put({"type": STOP})

    def drain_events(self) -> list[dict]:
        """쌓인 이벤트를 기다리지 않고 도착 순서대로 꺼낸다."""
        events: list[dict] = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return events
            try:
                _check_event(event)
            except (KeyError, TypeError) as exc:
                logger.warning("카메라 이벤트를 건너뜀 (%s: %s): %r", type(exc).__name__, exc, event)
            else:
                events.append(event)
