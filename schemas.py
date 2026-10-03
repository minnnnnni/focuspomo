"""FocusPomo 공용 데이터 스키마 (schema_version 2).

모든 모듈(cv_module, window_tracker, core, gui, storage)과 mocks는
여기서 스키마를 import한다. 필드를 바꿀 때는 이 파일과
`FocusPomo_데이터_스키마_명세_v2.md`를 같은 PR에서 함께 고치고,
파일에 저장되는 필드가 바뀌면 SCHEMA_VERSION을 +1 한다.

표기 규칙
- 시점: `timestamp` (epoch 초, float, time.time())
- 구간: `start` / `end` (SlotScore, PauseInterval에만 사용)
- 슬롯 번호: `slot` (slot_index()로 계산)
- 결측값: 키는 항상 두고 값만 None (= 측정 불가, 감점하지 않음)
- enum: 데이터에는 영문 키, 한글은 *_KO 매핑으로 GUI에서만 표시
"""
from __future__ import annotations

from typing import Literal, TypedDict, get_args

SCHEMA_VERSION = 2  # v2: 일시정지 구간(pause_intervals) 추가
SLOT_SECONDS = 10

# ── enum ─────────────────────────────────────────────
WindowLabel = Literal["work", "distraction", "unknown"]
FinalTag = Literal["away", "drowsy", "distraction", "focus", "unknown"]

WINDOW_LABELS: tuple[str, ...] = get_args(WindowLabel)
FINAL_TAGS: tuple[str, ...] = get_args(FinalTag)

WINDOW_LABEL_KO: dict[str, str] = {
    "work": "작업",
    "distraction": "딴짓",
    "unknown": "미분류",
}
FINAL_TAG_KO: dict[str, str] = {
    "away": "이탈(자리비움)",
    "drowsy": "이탈(졸음)",
    "distraction": "이탈(딴짓)",
    "focus": "집중",
    "unknown": "미분류",
}


# ── 캘리브레이션 (CV 스레드, 세션마다 1회) ───────────
class Calibration(TypedDict):
    ear_baseline: float          # 응시 구간 EAR 중앙값
    valid_frames: int            # 얼굴이 검출된 프레임 수
    face_detected_ratio: float   # 0~1
    started_at: float
    ended_at: float              # = session_start
    fallback_used: bool          # 실패로 직전 세션 기준값을 썼는지


# ── CV 스레드 내부 (1초마다) ─────────────────────────
class CvSecond(TypedDict):
    timestamp: float                 # 해당 1초 구간의 시작 시각
    face_present: bool               # 과반 프레임에서 얼굴 검출
    eyes_closed: bool                # 1초 동안 모든 프레임이 '감김'. 얼굴 없으면 False
    blink_count: int                 # 이 1초에 끝난 짧은 감김 수. 얼굴 없으면 0
    ear_mean: float | None           # 디버깅용. 얼굴 없으면 None
    gaze_off_screen: bool | None     # 예약 필드


# ── 스레드 → GUI 큐 (10초마다) ───────────────────────
class CvSlot(TypedDict):
    slot: int
    timestamp: float                 # session_start + 10*slot
    face_present: bool | None        # 1초 요약이 0개면 None
    drowsy: bool                     # closed_run >= DROWSY_MIN_RUN_SEC인 초가 있었음
    closed_run_max: int              # 슬롯 안 closed_run 최댓값 (앞 슬롯에서 이어진 값 포함)
    blink_count: int                 # 1초 요약 합 (캡은 점수 단계에서 적용)
    ear_mean: float | None
    gaze_off_screen: bool | None     # 예약 필드


class WindowSlot(TypedDict):
    slot: int                        # 창 스레드가 slot_index()로 직접 계산
    timestamp: float                 # 조회 시각
    app_name: str | None             # 조회 실패 시 None
    domain: str | None               # 브라우저일 때 도메인만, 그 외 None


class CvSlotMessage(TypedDict):
    source: Literal["cv"]
    slot: CvSlot
    seconds: list[CvSecond]          # focus_log 저장용 1초 원본


class WindowSlotMessage(TypedDict):
    source: Literal["window"]
    slot: WindowSlot


SlotMessage = CvSlotMessage | WindowSlotMessage


# ── GUI 슬롯 로그 ────────────────────────────────────
class MergedSlot(CvSlot):
    app_name: str | None
    domain: str | None


# ── LLM ──────────────────────────────────────────────
class LlmSlotInput(TypedDict):
    slot: int
    timestamp: float
    app_name: str | None
    domain: str | None
    face_present: bool | None
    drowsy: bool


class SlotLabel(TypedDict):
    slot: int
    window_label: WindowLabel


class LlmResponse(TypedDict):
    slot_labels: list[SlotLabel]
    report_text: str


# ── 점수 결과 ─────────────────────────────────────────
class SlotScore(TypedDict):
    slot: int
    start: float
    end: float                       # 마지막 슬롯은 세션 종료 시각
    window_label: WindowLabel
    final_tag: FinalTag
    blink_count: int                 # 캡 적용 전 (표시용)
    score: int


class PauseInterval(TypedDict):
    start: float                     # 일시정지를 누른 시각
    end: float                       # 다시 시작(또는 정지 중 세션 종료)한 시각


class SessionResult(TypedDict):
    schema_version: int
    session_id: str                  # "YYYYMMDD-HHMMSS"
    session_start: float             # = 캘리브레이션 종료 시각
    session_end: float               # 실제 종료 시각 (정지 시간 포함)
    ear_baseline: float
    pause_intervals: list[PauseInterval]  # 시간순, 없으면 []
    slot_scores: list[SlotScore]     # 정지 구간과 겹친 슬롯은 빠짐 (slot 번호는 그대로라 중간이 빌 수 있음)
    score_total: int
    blink_bonus_total: int
    drowsy_count: int
    away_count: int
    distraction_count: int
    report_text: str
    llm_call_failed: bool


class FocusLogFile(TypedDict):
    schema_version: int
    session_id: str
    session_start: float
    calibration: Calibration
    pause_intervals: list[PauseInterval]
    cv_seconds: list[CvSecond]
    merged_slots: list[MergedSlot]   # 정지 구간 슬롯도 포함한 원본 전체


# ── 기본값 (세션 종료 시 한쪽 메시지가 없는 슬롯용) ──
def empty_cv_part() -> dict:
    """CV 메시지가 오지 않은 슬롯의 CV 쪽 기본값. 매번 새 dict를 돌려준다."""
    return {
        "face_present": None,
        "drowsy": False,
        "closed_run_max": 0,
        "blink_count": 0,
        "ear_mean": None,
        "gaze_off_screen": None,
    }


def empty_window_part() -> dict:
    """창 메시지가 오지 않은 슬롯의 창 쪽 기본값. 매번 새 dict를 돌려준다."""
    return {"app_name": None, "domain": None}


# ── 헬퍼 ─────────────────────────────────────────────
def slot_index(timestamp: float, session_start: float) -> int:
    """시각을 슬롯 번호로 바꾼다. 모든 모듈이 이 함수만 쓴다."""
    return int((timestamp - session_start) // SLOT_SECONDS)


def slot_start(slot: int, session_start: float) -> float:
    """슬롯 번호의 시작 시각."""
    return session_start + slot * SLOT_SECONDS


def slot_overlaps_pause(slot: int, session_start: float, pause_intervals: list[PauseInterval]) -> bool:
    """슬롯 10초 구간이 정지 구간과 조금이라도 겹치는지. 이런 슬롯은 점수·LLM에서 뺀다.

    슬롯 구간은 [slot_start, slot_start + SLOT_SECONDS)로 보며, 경계가 맞닿기만 하면 겹치지 않는다.
    """
    begin = slot_start(slot, session_start)
    end = begin + SLOT_SECONDS
    return any(p["start"] < end and p["end"] > begin for p in pause_intervals)


def pause_total(pause_intervals: list[PauseInterval]) -> float:
    """정지한 시간의 합(초)."""
    return sum(p["end"] - p["start"] for p in pause_intervals)


def make_session_id(session_start: float) -> str:
    """로컬 시각 기준 "YYYYMMDD-HHMMSS" (Windows 파일명에 쓸 수 있도록 콜론 없음)."""
    import time
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(session_start))


def closure(ear: float, ear_baseline: float) -> float:
    """개인 기준 대비 눈이 감긴 정도. 0 = 기준만큼 뜸, 1 = 완전히 감음."""
    if ear_baseline <= 0:
        raise ValueError("ear_baseline must be positive")
    return 1.0 - ear / ear_baseline


def normalize_window_label(value: object) -> WindowLabel:
    """LLM 응답의 라벨을 검증한다. 정의되지 않은 값은 "unknown"."""
    return value if value in WINDOW_LABELS else "unknown"  # type: ignore[return-value]


def final_tag(face_present: bool | None, drowsy: bool, window_label: WindowLabel) -> FinalTag:
    """리포트 표에 보여줄 대표 태그 (away > drowsy > distraction > focus > unknown)."""
    if face_present is False:
        return "away"
    if drowsy:
        return "drowsy"
    if window_label == "distraction":
        return "distraction"
    if window_label == "work":
        return "focus"
    return "unknown"