# 세션 시작 시 화면 응시로 ear_baseline을 구한다.

from __future__ import annotations

import statistics

from config.aggregation import CALIBRATION_SEC
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

    def finish(self) -> Calibration:
        """측정 원본만 돌려준다. 성공·실패 판정은 core/calibration.py에서 한다."""
        if not self.done:
            raise RuntimeError("캘리브레이션이 아직 끝나지 않았다")
        baseline = statistics.median(self._ears) if self._ears else 0.0
        return Calibration(
            ear_baseline=float(baseline),
            valid_frames=len(self._ears),
            face_detected_ratio=self.face_detected_ratio,
            started_at=self.started_at,
            ended_at=self.ended_at,
            fallback_used=False,
        )
