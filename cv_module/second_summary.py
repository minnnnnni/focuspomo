"""
프레임별 EAR → 1초 요약(CvSecond).

- 감김: closure(ear, ear_baseline) > CLOSURE_THRESHOLD. 단 평소보다 BOWED_EYES_PITCH 이상 숙인 프레임은
  노트를 내려다보느라 눈이 작아 보이는 것이라 감김으로 세지 않는다 (진행 중이던 감김도 깜빡임으로 세지 않고 버림, CV-06)
- eyes_closed: 그 1초의 모든 프레임이 감김 (얼굴 없는 프레임이 섞이면 False)
- blink_count: BLINK_MAX_SEC 이하로 끝난 감김 수. 끝난 시점이 속한 초에 센다.
  감김은 초 경계를 넘어 이어질 수 있으므로 상태를 초 단위로 끊지 않는다.
- pitch_rel / face_y / person_ratio / person_max (v6, CV-06): 고개 숙임 판정용. 웹캠 프레임에서만 들어온다.
  posture는 세션 종료 후 cv_module.head_down이 채운다 (여기서는 None).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from config.aggregation import BLINK_MAX_SEC, BOWED_EYES_PITCH, CLOSURE_THRESHOLD
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
    pitches: list[float] = field(default_factory=list)   # 얼굴 프레임의 평소 대비 각도
    face_y: float | None = None                          # 마지막 얼굴 프레임의 얼굴 높이
    persons: list[float] = field(default_factory=list)   # 얼굴을 놓친 프레임의 사람 영역 비율


class SecondAggregator:
    # add_frame()이 1초가 끝날 때마다 완성된 CvSecond를 돌려준다.

    def __init__(self, session_start: float, ear_baseline: float, pitch_baseline: float | None = None) -> None:
        if ear_baseline <= 0:
            raise ValueError("ear_baseline must be positive")
        self.session_start = session_start
        self.ear_baseline = ear_baseline
        self.pitch_baseline = pitch_baseline  # 캘리브레이션 때 잰 평소 고개 각도 (없으면 pitch_rel은 None)
        self._bucket: _Bucket | None = None
        self._closed_since: float | None = None   # 진행 중인 감김의 시작 시각

    def add_frame(self, timestamp: float, ear: float | None, *, pitch: float | None = None,
                  face_y: float | None = None, person: float | None = None) -> list[CvSecond]:
        # ear=None은 얼굴 미검출 프레임. 완성된 1초 요약(보통 0개 또는 1개)을 반환.
        # pitch·face_y는 얼굴 프레임에서, person은 얼굴을 놓친 프레임에서만 들어온다 (웹캠, CV-06).
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
            if person is not None:
                b.persons.append(person)
            return out

        b.face_frames += 1
        b.ear_sum += ear
        pitch_rel = None if pitch is None or self.pitch_baseline is None else pitch - self.pitch_baseline
        if pitch_rel is not None:
            b.pitches.append(pitch_rel)
        if face_y is not None:
            b.face_y = face_y

        if pitch_rel is not None and pitch_rel >= BOWED_EYES_PITCH:
            # 숙여서 아래를 봄: 눈이 작아도 감김이 아니다. 진행 중이던 감김은 깜빡임으로 세지 않는다
            self._closed_since = None
        elif is_closed(ear, self.ear_baseline):
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
            pitch_rel=statistics.median(b.pitches) if b.pitches else None,
            face_y=b.face_y,
            person_ratio=statistics.median(b.persons) if b.persons else None,
            person_max=max(b.persons) if b.persons else None,
            posture=None,
        )
