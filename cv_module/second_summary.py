"""
프레임별 EAR → 1초 요약(CvSecond).

- 감김: closure(ear, ear_baseline) > CLOSURE_THRESHOLD
- eyes_closed: 그 1초의 모든 프레임이 감김 (얼굴 없는 프레임이 섞이면 False)
- blink_count: BLINK_MAX_SEC 이하로 끝난 감김 수. 끝난 시점이 속한 초에 센다.
  감김은 초 경계를 넘어 이어질 수 있으므로 상태를 초 단위로 끊지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.aggregation import BLINK_MAX_SEC, CLOSURE_THRESHOLD
from schemas import CvSecond, closure


def is_closed(ear: float, ear_baseline: float) -> bool:
    return closure(ear, ear_baseline) > CLOSURE_THRESHOLD


@dataclass
class _Bucket:
    index: int                # session_start 기준 몇 번째 초인지
    frames: int = 0
    face_frames: int = 0
    closed_frames: int = 0
    ear_sum: float = 0.0
    blinks: int = 0


class SecondAggregator:
    # add_frame()이 1초가 끝날 때마다 완성된 CvSecond를 돌려준다.

    def __init__(self, session_start: float, ear_baseline: float) -> None:
        if ear_baseline <= 0:
            raise ValueError("ear_baseline must be positive")
        self.session_start = session_start
        self.ear_baseline = ear_baseline
        self._bucket: _Bucket | None = None
        self._closed_since: float | None = None   # 진행 중인 감김의 시작 시각

    def add_frame(self, timestamp: float, ear: float | None) -> list[CvSecond]:
        # ear=None은 얼굴 미검출 프레임. 완성된 1초 요약(보통 0개 또는 1개)을 반환.
        if timestamp < self.session_start:
            return []

        out: list[CvSecond] = []
        index = int(timestamp - self.session_start)
        if self._bucket is not None and index != self._bucket.index:
            out.append(self._finalize(self._bucket))
            self._bucket = None
        if self._bucket is None:
            self._bucket = _Bucket(index)

        b = self._bucket
        b.frames += 1

        if ear is None:
            # 얼굴을 놓치면 진행 중이던 감김은 깜빡임으로 세지 않고 버린다.
            self._closed_since = None
            return out

        b.face_frames += 1
        b.ear_sum += ear

        if is_closed(ear, self.ear_baseline):
            b.closed_frames += 1
            if self._closed_since is None:
                self._closed_since = timestamp
        elif self._closed_since is not None:
            if timestamp - self._closed_since <= BLINK_MAX_SEC:
                b.blinks += 1
            self._closed_since = None

        return out

    def flush(self) -> list[CvSecond]:
        # 세션 종료 시 남은 1초를 마무리한다.
        if self._bucket is None:
            return []
        sec = self._finalize(self._bucket)
        self._bucket = None
        return [sec]

    def _finalize(self, b: _Bucket) -> CvSecond:
        face_present = b.face_frames * 2 > b.frames      # 과반 프레임
        return CvSecond(
            timestamp=self.session_start + b.index,
            face_present=face_present,
            eyes_closed=face_present and b.closed_frames == b.frames,
            blink_count=b.blinks if face_present else 0,
            ear_mean=(b.ear_sum / b.face_frames) if face_present else None,
            gaze_off_screen=None,
        )
