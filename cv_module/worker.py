"""CV 스레드 (CV-05): 앱 수명의 CameraThread + 세션마다 만드는 CvSessionWorker.

GUI와는 core.camera_link.CameraLink의 명령 큐·이벤트 큐로만 주고받는다 (개발계획서 2.0, 2.4, INT-03).

    명령 (GUI·세션 워커 → CameraThread)   start_calibration / cancel_calibration / begin_session / end_session / stop
    이벤트 (CameraThread → GUI)            camera_opened / camera_failed(reason) / calibration_raw(calib_id, attempt, raw)
    10초 슬롯 (CameraThread → slot_queue)  세션 중에만, CvSlotMessage

모듈 간 약속 (PROGRESS.md "CV 스레드 동작 규칙")
- 시각은 time.time(). session_start는 calibration["ended_at"]을 그대로 받는다.
- 카메라 열기·읽기는 _CameraReader 스레드에서 한다. 명령 처리 루프는 카메라를 기다리지 않으므로
  웹캠을 다시 여는 중에도 end_session을 바로 처리해 2초 안에 마지막 슬롯을 넣는다.
- 끊김 = LOST_SEC(1초) 동안 프레임을 하나도 못 읽음. REOPEN_INTERVAL_SEC 간격으로 다시 열어 본다.
- camera_opened / camera_failed는 앱 시작 때 첫 상태를 1회, 이후 상태가 바뀔 때만 보낸다.
- 취소된 시도, 측정 중 끊긴 시도는 calibration_raw를 보내지 않는다.
- 세션 중 끊긴 구간은 프레임이 없으므로 슬롯 메시지가 나가지 않는다 (GUI가 empty_cv_part()로 채움).
- 캘리브레이션·세션 밖에서는 프레임을 읽기만 하고 분석하지 않는다.
- 얼굴 검출 모델은 앱 시작 때 미리 불러온다 (Windows에서 첫 로딩이 10초 넘게 걸릴 수 있음).
  모델이 준비되고 웹캠이 열린 뒤에 camera_opened를 보내므로, 시작 버튼도 그때 켜진다.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

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
from cv_module.calibration import Calibrator
from cv_module.pipeline import CvPipeline

logger = logging.getLogger(__name__)

TARGET_FPS = 15
LOST_SEC = 1.0  # 이 시간 동안 프레임이 하나도 없으면 끊김
REOPEN_INTERVAL_SEC = 2.0  # 열기 실패·끊김 뒤 다시 열어 보는 간격
LOOP_WAIT_SEC = 0.05  # 명령 처리 루프가 새 프레임을 기다리는 최대 시간
READER_JOIN_SEC = 1.0

REASON_OPEN_FAILED = "open_failed"
REASON_LOST = "lost"
REASON_DETECTOR_FAILED = "detector_failed"


# ── 프레임 소스 ─────────────────────────────────────────

class EarSource(Protocol):
    """open/read/close는 _CameraReader 스레드에서, estimate/close_estimator는 CameraThread에서 불린다."""

    def open(self) -> bool: ...

    def read(self) -> Any | None: ...  # 실패하면 None

    def close(self) -> None: ...

    def prepare(self) -> None: ...  # 얼굴 검출 모델을 미리 불러온다 (CameraThread에서 한 번)

    def estimate(self, frame: Any) -> float | None: ...  # 얼굴이 없으면 None

    def close_estimator(self) -> None: ...


class WebcamEarSource:
    """실제 웹캠 + MediaPipe Face Landmarker (demo_live.py와 같은 방식으로 EAR을 구한다)."""

    def __init__(self, camera_index: int = 0) -> None:
        self.camera_index = camera_index
        self._cap = None
        self._detector = None

    def open(self) -> bool:
        import cv2

        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            return False
        self._cap = cap
        return True

    def read(self):
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def prepare(self) -> None:
        """MediaPipe는 쓰는 스레드(CameraThread)에서 만든다. import와 모델 로딩이 오래 걸린다."""
        if self._detector is None:
            from cv_module.face_landmarks import FaceLandmarkDetector

            self._detector = FaceLandmarkDetector()
            # 첫 추론도 그래프 초기화로 오래 걸리므로 빈 화면으로 한 번 미리 돌린다
            import numpy as np

            self._detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))

    def estimate(self, frame) -> float | None:
        from cv_module.ear import compute_ear

        self.prepare()
        points = self._detector.detect(frame)
        return compute_ear(points) if points else None

    def close_estimator(self) -> None:
        if self._detector is not None:
            self._detector.close()
            self._detector = None


class FakeEarSource:
    """웹캠 없이 테스트용. 프레임 = 읽은 시각, EAR = ear_fn(시각) (None = 얼굴 없음).

    fail_open / fail_read를 켜면 열기 실패·끊김을 흉내 낸다.
    """

    def __init__(self, ear_fn: Callable[[float], float | None], fps: float = 30) -> None:
        self.ear_fn = ear_fn
        self.fps = fps
        self.fail_open = False
        self.fail_read = False
        self.open_calls = 0
        self._opened = False

    def open(self) -> bool:
        self.open_calls += 1
        self._opened = not self.fail_open
        return self._opened

    def read(self):
        time.sleep(1 / self.fps)
        if not self._opened or self.fail_read:
            return None
        return time.time()

    def close(self) -> None:
        self._opened = False

    def prepare(self) -> None:
        pass

    def estimate(self, frame) -> float | None:
        return self.ear_fn(frame)

    def close_estimator(self) -> None:
        pass


# ── 카메라 읽기 스레드 ──────────────────────────────────

class _CameraReader(threading.Thread):
    """카메라 열기·읽기만 한다. 오래 걸릴 수 있는 카메라 호출은 전부 여기서 일어난다."""

    def __init__(self, source: EarSource, reopen_sec: float, lost_sec: float) -> None:
        super().__init__(name="CameraReader", daemon=True)
        self.source = source
        self.reopen_sec = reopen_sec
        self.lost_sec = lost_sec
        self._stop_event = threading.Event()
        self._cond = threading.Condition()
        self._latest: tuple[int, float, Any] | None = None  # (번호, 읽은 시각, 프레임)
        # 아래 값은 CameraThread가 읽기만 한다
        self.attempted = False  # 첫 열기 시도가 끝났는지
        self.opened = False
        self.ever_opened = False
        self.last_frame_at = 0.0

    def stop(self) -> None:
        self._stop_event.set()
        with self._cond:
            self._cond.notify_all()

    def latest_after(self, seq: int, timeout: float) -> tuple[int, float, Any] | None:
        """seq보다 새 프레임을 timeout초까지 기다린다. 없으면 None. 밀린 프레임은 건너뛰고 최신 것만 준다."""
        with self._cond:
            if self._latest is None or self._latest[0] <= seq:
                self._cond.wait(timeout)
            if self._latest is not None and self._latest[0] > seq:
                return self._latest
            return None

    def run(self) -> None:
        seq = 0
        try:
            while not self._stop_event.is_set():
                if not self.opened:
                    ok = self.source.open()
                    if ok:
                        self.last_frame_at = time.time()  # 열린 직후 LOST_SEC 동안은 끊김으로 보지 않는다
                        self.opened = self.ever_opened = True
                    self.attempted = True
                    if not ok:
                        self._stop_event.wait(self.reopen_sec)
                    continue
                frame = self.source.read()
                now = time.time()
                if frame is None:
                    if now - self.last_frame_at > self.lost_sec:
                        self.source.close()
                        self.opened = False
                        self._stop_event.wait(self.reopen_sec)
                    else:
                        self._stop_event.wait(0.02)
                    continue
                self.last_frame_at = now
                seq += 1
                with self._cond:
                    self._latest = (seq, now, frame)
                    self._cond.notify_all()
        finally:
            self.source.close()


# ── CameraThread ───────────────────────────────────────

class _Session:
    def __init__(self, session_start: float, ear_baseline: float, slot_queue: queue.Queue,
                 done: threading.Event) -> None:
        self.session_start = session_start
        self.ear_baseline = ear_baseline
        self.slot_queue = slot_queue
        self.done = done  # 마지막 슬롯을 slot_queue에 넣은 뒤 set


class CameraThread(threading.Thread):
    """앱 시작 때 한 번 만들어 start(), 앱 종료 때 link.stop() 후 join().

    slot_queue: begin_session 명령에 큐가 없을 때 쓸 기본 큐 (CvSessionWorker는 자기 큐를 함께 보낸다).
    """

    def __init__(
        self,
        link: CameraLink,
        slot_queue: queue.Queue | None = None,
        source: EarSource | None = None,
        *,
        target_fps: float = TARGET_FPS,
        lost_sec: float = LOST_SEC,
        reopen_sec: float = REOPEN_INTERVAL_SEC,
        calibrator_factory: Callable[[float], Calibrator] = Calibrator,
        pipeline_factory: Callable[[float, float], CvPipeline] = CvPipeline,
    ) -> None:
        super().__init__(name="CameraThread", daemon=True)
        self.link = link
        self._default_slot_queue = slot_queue
        self._source = source if source is not None else WebcamEarSource()
        self._reader = _CameraReader(self._source, reopen_sec, lost_sec)
        self._min_interval = 1.0 / target_fps
        self._lost_sec = lost_sec
        self._make_calibrator = calibrator_factory
        self._make_pipeline = pipeline_factory

        self._camera_ok: bool | None = None  # 마지막으로 GUI에 알린 상태
        self._calib: Calibrator | None = None
        self._calib_id: Any = None
        self._calib_attempt = 0
        self._session: _Session | None = None
        self._pipeline: CvPipeline | None = None
        self._last_processed = 0.0
        self._fps_times: list[float] = []

    @property
    def camera_ok(self) -> bool | None:
        return self._camera_ok

    @property
    def processed_fps(self) -> float:
        """최근 2초 동안 분석한 프레임 수 / 2."""
        now = time.time()
        return sum(1 for t in self._fps_times if now - t <= 2.0) / 2.0

    def run(self) -> None:
        self._reader.start()  # 웹캠 열기와 모델 로딩을 동시에 진행한다
        seq = 0
        try:
            started = time.time()
            try:
                self._source.prepare()
            except Exception:
                logger.exception("얼굴 검출 모델을 불러오지 못했습니다")
                self.link.events.put({"type": CAMERA_FAILED, "reason": REASON_DETECTOR_FAILED})
                return
            logger.info("얼굴 검출 모델 준비 (%.1f초)", time.time() - started)
            while self._handle_commands():
                self._update_camera_state()
                got = self._reader.latest_after(seq, LOOP_WAIT_SEC)
                if got is None:
                    continue
                seq, ts, frame = got
                if self._calib is None and self._pipeline is None:
                    continue  # 캘리브레이션·세션 밖: 분석하지 않는다
                if ts - self._last_processed < self._min_interval:
                    continue  # TARGET_FPS로 제한
                self._last_processed = ts
                self._fps_times = [t for t in self._fps_times if ts - t <= 2.0] + [ts]
                self._on_frame(ts, self._source.estimate(frame))
        except Exception:
            logger.exception("CameraThread 오류로 종료합니다")
        finally:
            self._end_session(self._session)
            self._reader.stop()
            self._reader.join(READER_JOIN_SEC)
            try:
                self._source.close_estimator()
            except Exception:
                logger.exception("얼굴 검출기를 닫지 못했습니다")

    # ---- 명령 ----
    def _handle_commands(self) -> bool:
        """쌓인 명령을 처리한다. stop이면 False."""
        while True:
            try:
                cmd = self.link.commands.get_nowait()
            except queue.Empty:
                return True
            kind = cmd.get("type")
            if kind == START_CALIBRATION:
                self._start_calibration(cmd["calib_id"], cmd["attempt"])
            elif kind == CANCEL_CALIBRATION:
                if self._calib is not None and self._calib_id == cmd["calib_id"]:
                    self._calib = None
            elif kind == BEGIN_SESSION:
                self._begin_session(cmd)
            elif kind == END_SESSION:
                done = cmd.get("done")
                if done is None or (self._session is not None and self._session.done is done):
                    self._end_session(self._session)
                else:
                    done.set()  # 이미 끝났거나 시작되지 않은 세션
            elif kind == STOP:
                return False
            else:
                logger.warning("알 수 없는 카메라 명령을 건너뜀: %r", cmd)

    def _start_calibration(self, calib_id: Any, attempt: int) -> None:
        if self._session is not None:
            logger.warning("세션 중 캘리브레이션 요청은 무시합니다")
            return
        if not self._camera_ok:
            # GUI는 camera_opened 전에는 시작 버튼을 꺼 두므로 보통 오지 않는다. 결과 없이 무시한다.
            logger.warning("웹캠이 열려 있지 않아 캘리브레이션 요청을 무시합니다: %s", calib_id)
            return
        self._calib = self._make_calibrator(time.time())
        self._calib_id = calib_id
        self._calib_attempt = attempt

    def _begin_session(self, cmd: dict) -> None:
        if self._session is not None:
            self._end_session(self._session)  # 이전 세션이 남아 있으면 먼저 마감
        slot_queue = cmd.get("slot_queue") or self._default_slot_queue
        if slot_queue is None:
            logger.error("슬롯 큐가 없어 세션을 시작하지 않습니다")
            if cmd.get("done") is not None:
                cmd["done"].set()
            return
        self._calib = None
        self._session = _Session(cmd["session_start"], cmd["ear_baseline"], slot_queue,
                                 cmd.get("done") or threading.Event())
        self._pipeline = self._make_pipeline(cmd["session_start"], cmd["ear_baseline"])

    def _end_session(self, session: _Session | None) -> None:
        if session is None:
            return
        try:
            if self._pipeline is not None:
                for msg in self._pipeline.finish():
                    session.slot_queue.put(msg)
                    _log_slot(msg)
        except Exception:
            logger.exception("마지막 CV 슬롯을 만들지 못했습니다")
        finally:
            self._pipeline = None
            self._session = None
            session.done.set()

    # ---- 카메라 상태 ----
    def _update_camera_state(self) -> None:
        if not self._reader.attempted:
            return
        ok = self._reader.opened and time.time() - self._reader.last_frame_at <= self._lost_sec
        if ok == self._camera_ok:
            return
        self._camera_ok = ok
        if ok:
            logger.info("웹캠 열림")
            self.link.events.put({"type": CAMERA_OPENED})
            return
        reason = REASON_LOST if self._reader.ever_opened else REASON_OPEN_FAILED
        logger.warning("웹캠 사용 불가: %s", reason)
        self.link.events.put({"type": CAMERA_FAILED, "reason": reason})
        self._calib = None  # 캘리브레이션 중 끊김: 결과 없이 끝낸다

    # ---- 프레임 ----
    def _on_frame(self, ts: float, ear: float | None) -> None:
        if self._calib is not None:
            self._calib.add_frame(ts, ear)
            if self._calib.done:
                raw = self._calib.finish()
                logger.info("캘리브레이션 측정 끝 (시도 %s): ear_baseline=%.3f, 얼굴 비율 %.2f",
                            self._calib_attempt, raw["ear_baseline"], raw["face_detected_ratio"])
                self.link.events.put({"type": CALIBRATION_RAW, "calib_id": self._calib_id,
                                      "attempt": self._calib_attempt, "raw": raw})
                self._calib = None
        elif self._pipeline is not None and self._session is not None:
            if ts < self._session.session_start:
                return
            for msg in self._pipeline.add_frame(ts, ear):
                self._session.slot_queue.put(msg)
                _log_slot(msg)


def _log_slot(msg: dict) -> None:
    """앱을 띄워 놓고 터미널에서 CV 판정을 눈으로 확인할 수 있게 슬롯마다 한 줄 남긴다."""
    slot = msg.get("slot") if isinstance(msg, dict) else None
    if not isinstance(slot, dict):
        return
    ear = slot.get("ear_mean")
    logger.info("CV slot %s | 얼굴 %s | 졸음 %s | 감김연속 최대 %s초 | 깜빡임 %s | EAR %s",
                slot.get("slot"), slot.get("face_present"), slot.get("drowsy"), slot.get("closed_run_max"),
                slot.get("blink_count"), "-" if ear is None else f"{ear:.3f}")


# ── 세션 워커 ───────────────────────────────────────────

class CvSessionWorker:
    """세션마다 새로 만든다 (core.session_workers.SessionWorker 형태).

    실제 처리는 CameraThread가 하고, 이 객체는 세션 시작·종료를 명령 큐로 전달한다.
    is_alive()/join()은 CameraThread가 이 세션의 마지막 슬롯을 slot_queue에 넣을 때까지 살아 있는 것으로 보인다.
    camera_thread를 주면 그 스레드가 죽었을 때 기다리지 않는다.
    """

    def __init__(self, link: CameraLink, slot_queue: queue.Queue, session_start: float, ear_baseline: float,
                 camera_thread: threading.Thread | None = None) -> None:
        self.link = link
        self.slot_queue = slot_queue
        self.session_start = session_start
        self.ear_baseline = ear_baseline
        self._camera_thread = camera_thread
        self._done = threading.Event()
        self._started = False
        self._stopped = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("이미 시작한 세션 워커입니다")
        self._started = True
        self.link.commands.put({"type": BEGIN_SESSION, "session_start": self.session_start,
                                "ear_baseline": self.ear_baseline, "slot_queue": self.slot_queue,
                                "done": self._done})

    def stop(self) -> None:
        if self._started and not self._stopped:
            self._stopped = True
            self.link.commands.put({"type": END_SESSION, "done": self._done})

    def _camera_alive(self) -> bool:
        return self._camera_thread is None or self._camera_thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        if not self._started:
            return
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._done.is_set() and self._camera_alive():
            left = 0.05 if deadline is None else min(0.05, deadline - time.monotonic())
            if left <= 0:
                return
            self._done.wait(left)

    def is_alive(self) -> bool:
        return self._started and not self._done.is_set() and self._camera_alive()