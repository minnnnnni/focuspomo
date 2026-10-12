# 세션 시작 시 화면 응시로 ear_baseline을 구한다.

from __future__ import annotations

import statistics

from config.aggregation import CALIBRATION_MIN_FACE_RATIO, CALIBRATION_SEC
from schemas import Calibration


class Calibrator:
    # 프레임마다 add_frame()을 호출하고, done이 되면 finish()로 결과를 받는다.

    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self.ended_at: float | None = None
        self._ears: list[float] = []
        self._total_frames = 0

    @property
    def done(self) -> bool:
        return self.ended_at is not None

    def add_frame(self, timestamp: float, ear: float | None) -> None:
        # ear=None은 얼굴 미검출 프레임.
        if self.done:
            return
        if timestamp - self.started_at >= CALIBRATION_SEC:
            self.ended_at = timestamp
            return
        self._total_frames += 1
        if ear is not None:
            self._ears.append(ear)

    @property
    def face_detected_ratio(self) -> float:
        return len(self._ears) / self._total_frames if self._total_frames else 0.0

    @property
    def is_valid(self) -> bool:
        return (
            self.face_detected_ratio >= CALIBRATION_MIN_FACE_RATIO
            and bool(self._ears)
            and statistics.median(self._ears) > 0
        )

    def finish(self, fallback_baseline: float | None = None) -> Calibration | None:
        """
        측정 성공 → 측정값.
        실패 + fallback_baseline 있음 → 직전 세션 기준값(fallback_used=True).
        실패 + fallback 없음 → None (GUI가 재응시를 요청).
        """
        if not self.done:
            raise RuntimeError("캘리브레이션이 아직 끝나지 않았다")

        if self.is_valid:
            baseline, fallback_used = statistics.median(self._ears), False
        elif fallback_baseline is not None:
            baseline, fallback_used = fallback_baseline, True
        else:
            return None

        return Calibration(
            ear_baseline=float(baseline),
            valid_frames=len(self._ears),
            face_detected_ratio=self.face_detected_ratio,
            started_at=self.started_at,
            ended_at=self.ended_at,  # = session_start
            fallback_used=fallback_used,
        )
