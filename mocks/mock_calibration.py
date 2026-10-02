"""Calibration mock 생성기 (CV 스레드 캘리브레이션 대역, 2주차에 실제 cv_module로 교체).

CV 스레드가 CALIBRATION_SEC초 응시 동안 측정해 돌려줄 Calibration 한 건(= 시도 1회)을 만든다.
성공/실패 판정, 1회 재시도, 직전 ear_baseline 폴백은 GUI(1-09)가 하므로
여기서 만드는 값은 항상 fallback_used=False인 측정 원본이다.

- make_calibration(): 얼굴 검출 비율을 정해 시도 1회 분량을 만든다.
- MockCalibrator: 시나리오대로 시도 결과를 순서대로 돌려준다.
  시나리오의 시도를 다 쓰면 마지막 결과를 반복한다 (세션 시작 거부 후 다시 캘리브레이션하는 경우).

결과를 GUI로 넘기는 실제 방식(반환값/큐/시그널)은 김시언과 2주차 전에 정한다.

    python -m mocks.mock_calibration       # 시나리오별 시도 결과 출력
"""
from __future__ import annotations

import time

from config.aggregation import CALIBRATION_MIN_FACE_RATIO, CALIBRATION_SEC
from mocks.mock_cv_slots import DEFAULT_EAR_BASELINE
from schemas import Calibration

MOCK_FPS = 10                # 처리 fps는 김시언이 정한다. mock용 임시값
GOOD_FACE_RATIO = 0.96       # 성공하는 시도
BAD_FACE_RATIO = 0.4         # 실패하는 시도 (CALIBRATION_MIN_FACE_RATIO 미만)
NO_FACE_EAR_BASELINE = 0.0   # 얼굴이 한 프레임도 없을 때. 실제 CV 동작은 미정 (김시언 확인 필요)

# 시나리오 이름 → 시도별 face_detected_ratio
SCENARIOS: dict[str, tuple[float, ...]] = {
    "success": (GOOD_FACE_RATIO,),
    "retry_success": (BAD_FACE_RATIO, GOOD_FACE_RATIO),
    "fail_twice": (BAD_FACE_RATIO, BAD_FACE_RATIO),
    "no_face": (0.0, 0.0),
    "borderline": (CALIBRATION_MIN_FACE_RATIO,),  # 기준값과 같으면 성공 ("미만"일 때만 실패)
}


def make_calibration(
    started_at: float,
    face_detected_ratio: float,
    ear_baseline: float = DEFAULT_EAR_BASELINE,
) -> Calibration:
    """시도 1회의 Calibration. ended_at = started_at + CALIBRATION_SEC."""
    if not 0.0 <= face_detected_ratio <= 1.0:
        raise ValueError(f"face_detected_ratio must be in [0, 1]: {face_detected_ratio}")
    total_frames = MOCK_FPS * CALIBRATION_SEC
    valid_frames = round(face_detected_ratio * total_frames)
    return {
        "ear_baseline": ear_baseline if valid_frames > 0 else NO_FACE_EAR_BASELINE,
        "valid_frames": valid_frames,
        "face_detected_ratio": face_detected_ratio,
        "started_at": started_at,
        "ended_at": started_at + CALIBRATION_SEC,
        "fallback_used": False,
    }


class MockCalibrator:
    """시나리오대로 캘리브레이션 시도 결과를 돌려주는 CV 스레드 대역."""

    def __init__(self, scenario: str = "success", ear_baseline: float = DEFAULT_EAR_BASELINE) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario: {scenario!r} (choose from {tuple(SCENARIOS)})")
        self.scenario = scenario
        self.ear_baseline = ear_baseline
        self.attempts = 0

    def run(self, started_at: float) -> Calibration:
        ratios = SCENARIOS[self.scenario]
        ratio = ratios[min(self.attempts, len(ratios) - 1)]
        self.attempts += 1
        return make_calibration(started_at, ratio, self.ear_baseline)


if __name__ == "__main__":
    start = time.time()
    for name, ratios in SCENARIOS.items():
        calibrator = MockCalibrator(name)
        print(f"\n## {name}")
        for i in range(len(ratios) + 1):
            c = calibrator.run(start + i * CALIBRATION_SEC)
            ok = c["face_detected_ratio"] >= CALIBRATION_MIN_FACE_RATIO
            print(f"  try {i + 1}: ratio={c['face_detected_ratio']:.2f} frames={c['valid_frames']:>2} "
                  f"ear_baseline={c['ear_baseline']:.2f} -> {'ok' if ok else 'fail'}")
