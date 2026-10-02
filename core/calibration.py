"""캘리브레이션 결과 판정 (개발계획서 2.0 "실패 처리").

CV 스레드가 돌려준 시도 1회의 Calibration을 보고 다음에 할 일을 정한다.
GUI 없이 테스트할 수 있도록 PySide6를 쓰지 않는다.

- 성공: face_detected_ratio >= CALIBRATION_MIN_FACE_RATIO → 측정값 그대로 세션 시작
- 첫 시도 실패 → 응시를 한 번 더 요청 (재시도)
- 재시도도 실패 → 직전 세션 ear_baseline으로 바꾸고 fallback_used=True로 세션 시작
- 직전 값도 없음 → 세션을 시작하지 않음 (거부)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from config.aggregation import CALIBRATION_MIN_FACE_RATIO
from schemas import Calibration

MAX_ATTEMPTS = 2  # 첫 시도 + 재시도 1회 (개발계획서 2.0)


class Calibrator(Protocol):
    """캘리브레이션 시도 1회를 돌려주는 쪽 (지금은 mocks.MockCalibrator, 2주차에 CV 스레드)."""

    def run(self, started_at: float) -> Calibration: ...


class Outcome(StrEnum):
    SUCCESS = "success"
    RETRY = "retry"
    FALLBACK = "fallback"
    REFUSED = "refused"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    calibration: Calibration | None  # 세션에 쓸 값. RETRY·REFUSED면 None

    @property
    def starts_session(self) -> bool:
        return self.calibration is not None


def is_successful(calibration: Calibration) -> bool:
    """기준 비율과 같으면 성공 ("미만"일 때만 실패)."""
    return calibration["face_detected_ratio"] >= CALIBRATION_MIN_FACE_RATIO


def decide(calibration: Calibration, attempt: int, previous_baseline: float | None) -> Decision:
    """attempt는 1부터 센다. previous_baseline이 None이거나 0 이하면 직전 값이 없는 것으로 본다."""
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1: {attempt}")
    if is_successful(calibration):
        return Decision(Outcome.SUCCESS, calibration)
    if attempt < MAX_ATTEMPTS:
        return Decision(Outcome.RETRY, None)
    if previous_baseline is None or previous_baseline <= 0:
        return Decision(Outcome.REFUSED, None)
    # 측정 시각·검출 비율은 마지막 시도 값을 남기고 기준값만 바꾼다 (ended_at = session_start)
    fallback: Calibration = {**calibration, "ear_baseline": previous_baseline, "fallback_used": True}
    return Decision(Outcome.FALLBACK, fallback)
