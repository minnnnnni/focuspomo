"""FocusPomo 공용 데이터 스키마 (schema_version 4).

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

SCHEMA_VERSION = 4  # v2: 일시정지 구간(pause_intervals) / v3: 창 슬롯에 유튜브 카테고리·백그라운드 재생
#                    v4: 점수 = 판정 시간 중 집중 비율, 창 슬롯 규칙상 딴짓 (집중도 점수 산출 명세)
SLOT_SECONDS = 10

# ── enum ─────────────────────────────────────────────
WindowLabel = Literal["work", "distraction", "unknown"]
FinalTag = Literal["away", "drowsy", "distraction", "focus", "unknown", "no_cam"]
ScoreMode = Literal["full", "cam_only", "window_only", "none"]
RuleDistraction = Literal["foreground", "background"]

WINDOW_LABELS: tuple[str, ...] = get_args(WindowLabel)
FINAL_TAGS: tuple[str, ...] = get_args(FinalTag)
SCORE_MODES: tuple[str, ...] = get_args(ScoreMode)
JUDGED_TAGS: tuple[str, ...] = ("focus", "away", "drowsy", "distraction")  # 점수 분모에 들어가는 태그
AWAY_TAGS: tuple[str, ...] = ("away", "drowsy", "distraction")  # 이탈 (유예 보정 대상)

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
    "no_cam": "판정불가",
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
    timestamp: float                 # 슬롯의 첫 조회 시각 (session_start + 10*k에 맞춰 조회)
    app_name: str | None             # 슬롯의 대표 창 (v4: 우선순위 압축). 조회 실패 시 None
    domain: str | None               # 브라우저일 때 도메인만, 그 외 None
    video_category: str | None       # v3: 그 창이 유튜브 영상이면 카테고리 (예: "Gaming"), 그 외 None
    background_media_sec: int | None  # v3: 재생 중인 미디어가 앞 화면에 없던 초. 측정 불가면 None
    background_category: str | None  # v3: 백그라운드로 가장 오래 재생된 유튜브 영상의 카테고리
    rule_distraction: RuleDistraction | None  # v4: 로컬 규칙상 명확한 딴짓 (앞 화면/백그라운드), 아니면 None


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
    video_category: str | None
    background_media_sec: int | None
    background_category: str | None
    rule_distraction: RuleDistraction | None


# ── LLM (창 분류: 세션의 고유 창 목록을 한 번에) ─────
class LlmWindowItem(TypedDict):
    app_name: str
    domain: str | None
    video_category: str | None
    slots: int                       # 이 창이 대표인 슬롯 수


class ItemLabel(TypedDict):
    app_name: str
    domain: str | None
    video_category: str | None
    window_label: WindowLabel


class LlmResponse(TypedDict):
    item_labels: list[ItemLabel]     # 빠진 항목은 "unknown"


# ── 점수 결과 ─────────────────────────────────────────
class SlotScore(TypedDict):
    slot: int
    start: float
    end: float                       # 마지막 슬롯은 세션 종료 시각
    app_name: str | None
    domain: str | None
    video_category: str | None
    rule_distraction: RuleDistraction | None
    window_label: WindowLabel        # 규칙상 딴짓이면 "distraction", 아니면 LLM 라벨
    raw_tag: FinalTag                # 유예 보정 전
    final_tag: FinalTag              # 유예 보정 후 (점수·리포트 기준)
    blink_count: int                 # 표시용


class Distraction(TypedDict):
    app_name: str | None             # 백그라운드 항목은 None
    domain: str | None               # 백그라운드 항목은 None
    video_category: str | None       # 백그라운드 항목은 background_category
    background: bool                 # rule_distraction == "background"에서 온 항목
    seconds: int


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
    score_mode: ScoreMode
    focus_score: int | None          # 0~100, 계산 불가(none 모드·판정 데이터 부족)면 None
    score_note: str | None           # 폴백·데이터 부족 안내, 없으면 None
    tag_seconds: dict[str, int]      # final_tag별 누적 초 (FINAL_TAGS 키 모두, 없으면 0)
    judged_seconds: int              # 점수 분모에 들어간 시간 (JUDGED_TAGS)
    top_distractions: list[Distraction]  # 딴짓 시간 상위 TOP_DISTRACTIONS_N개
    longest_focus_seconds: int       # slot 번호가 이어지는 focus 구간의 최장 시간
    blinks_per_minute: float | None  # 참고 지표 (점수 미반영). 얼굴 있던 시간이 짧으면 None
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
    return {
        "app_name": None,
        "domain": None,
        "video_category": None,
        "background_media_sec": None,
        "background_category": None,
        "rule_distraction": None,
    }


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


def window_label_of(slot: MergedSlot, llm_labels: dict) -> WindowLabel:
    """슬롯의 창 라벨 (점수 명세 3장). 규칙상 딴짓이 LLM보다 우선한다.

    llm_labels: {(app_name, domain, video_category): WindowLabel}. 빠진 창은 "unknown".
    """
    if slot["rule_distraction"] is not None:
        return "distraction"
    if slot["app_name"] is None:
        return "unknown"
    key = (slot["app_name"], slot["domain"], slot["video_category"])
    return normalize_window_label(llm_labels.get(key, "unknown"))


def raw_tag(slot: MergedSlot, window_label: WindowLabel, mode: ScoreMode) -> FinalTag:
    """유예 보정 전 슬롯 태그 (점수 명세 3장, 7장 모드별 규칙).

    no_cam > away > drowsy > distraction > focus > unknown. window_only는 웹캠 조건을 건너뛰고,
    cam_only는 LLM 라벨이 없으므로 얼굴 있고 졸지 않으면 규칙상 딴짓이 아닌 한 focus.
    """
    if mode != "window_only":
        if slot["face_present"] is None:
            return "no_cam"
        if slot["face_present"] is False:
            return "away"
        if slot["drowsy"]:
            return "drowsy"
        if mode == "cam_only":
            return "distraction" if window_label == "distraction" else "focus"
    if window_label == "distraction":
        return "distraction"
    if window_label == "work":
        return "focus"
    return "unknown"