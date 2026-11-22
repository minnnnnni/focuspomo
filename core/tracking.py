"""추적 모드: 세션 동안 웹캠·활성 창 추적 중 무엇을 쓸지 (사용자가 대기 화면에서 고른다).

GUI 없이 테스트할 수 있도록 PySide6를 import하지 않는다.

| 모드          | 웹캠 | 창 추적 | 캘리브레이션 | 점수 (점수 모듈 score_session)                         |
| ------------- | ---- | ------- | ------------ | ------------------------------------------------------ |
| `both`        | O    | O       | O            | 지금과 같다 (full, 데이터 상태에 따라 폴백)            |
| `cam_only`    | O    | X       | O            | 창 판정 없이 웹캠 신호만 (cam_only, 웹캠이 끊기면 none) |
| `window_only` | X    | O       | X            | 창 사용만 (window_only, LLM 분류가 실패하면 none)      |
| `timer_only`  | X    | X       | X            | 계산하지 않는다 (none). 집중·정지 시간만 기록           |

- 웹캠을 쓰지 않는 모드에서는 카메라 스레드를 멈춰 웹캠을 놓는다 (main.App). 캘리브레이션도 건너뛰므로
  session_start는 "세션 시작"을 누른 시각이고, Calibration은 측정 없음 값(no_camera_calibration)을 쓴다.
  ear_baseline = 0.0은 "얼굴 0프레임" 결과와 같은 값이라, 직전 기준값 읽기(load_previous_baseline)가 건너뛴다.
- 창 추적을 쓰지 않는 모드에서는 창 스레드를 만들지 않고, 창 목록을 LLM에 보내지 않는다.
"""
from __future__ import annotations

from enum import StrEnum

from schemas import Calibration


class TrackingMode(StrEnum):
    BOTH = "both"
    CAM_ONLY = "cam_only"
    WINDOW_ONLY = "window_only"
    TIMER_ONLY = "timer_only"

    @property
    def uses_camera(self) -> bool:
        return self in (TrackingMode.BOTH, TrackingMode.CAM_ONLY)

    @property
    def uses_window(self) -> bool:
        return self in (TrackingMode.BOTH, TrackingMode.WINDOW_ONLY)

    @classmethod
    def parse(cls, value: object) -> TrackingMode:
        """저장된 값에서 읽는다. 모르는 값이면 기본값(BOTH)."""
        try:
            return cls(value)
        except ValueError:
            return DEFAULT_TRACKING_MODE


DEFAULT_TRACKING_MODE = TrackingMode.BOTH

TRACKING_MODE_LABEL_KO: dict[TrackingMode, str] = {  # 대기 화면 선택 칸
    TrackingMode.BOTH: "웹캠 + 창",
    TrackingMode.CAM_ONLY: "웹캠만",
    TrackingMode.WINDOW_ONLY: "창만",
    TrackingMode.TIMER_ONLY: "타이머만",
}

TRACKING_MODE_HINT_KO: dict[TrackingMode, str] = {  # 선택 칸 툴팁·대기 화면 안내
    TrackingMode.BOTH: "웹캠과 활성 창으로 집중도를 판정해요",
    TrackingMode.CAM_ONLY: "활성 창은 기록하지 않고 웹캠으로만 판정해요",
    TrackingMode.WINDOW_ONLY: "웹캠을 끄고 활성 창으로만 판정해요",
    TrackingMode.TIMER_ONLY: "웹캠과 활성 창 없이 타이머만 써요 (점수 없음)",
}


def no_camera_calibration(now: float) -> Calibration:
    """웹캠을 쓰지 않는 세션의 Calibration. 측정하지 않았으므로 기준값 0.0, 프레임 0 (ended_at = session_start)."""
    return {
        "ear_baseline": 0.0,
        "valid_frames": 0,
        "face_detected_ratio": 0.0,
        "started_at": now,
        "ended_at": now,
        "fallback_used": False,
    }
