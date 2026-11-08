"""점수 모듈 mock (신명철 SCORE-01~03 대역, 2-05에서 실제 모듈로 교체).

core.session_end.ScoreFn 형태로 SessionResult(v4)를 만든다. 계산은 집중도 점수 산출 명세
(docs/FocusPomo_집중도_점수_산출_명세.md) 3·4·6·7·8장을 그대로 따른다.
- LLM 창 분류 대신 mocks.mock_window_slots의 앱 목록으로 라벨을 정한다
  (작업 앱 → "work", 딴짓 앱 → "distraction", 그 외 → "unknown"). 규칙상 딴짓(rule_distraction)이 우선
- llm_fail=True면 LLM 실패: 규칙상 딴짓 외에는 라벨이 없음, 폴백 문구, llm_call_failed=True
"""
from __future__ import annotations

import math

from config.scoring import (
    BLINK_RATE_MIN_SEC,
    CAM_USABLE_RATIO,
    GRACE_SLOTS,
    MIN_JUDGED_RATIO,
    MIN_JUDGED_SEC,
    TOP_DISTRACTIONS_N,
)
from mocks.mock_window_slots import DISTRACTION_APPS, WORK_APPS
from schemas import (
    AWAY_TAGS,
    FINAL_TAG_KO,
    FINAL_TAGS,
    JUDGED_TAGS,
    SCHEMA_VERSION,
    SLOT_SECONDS,
    Calibration,
    Distraction,
    FinalTag,
    MergedSlot,
    PauseInterval,
    ScoreMode,
    SessionResult,
    SlotScore,
    WindowLabel,
    raw_tag,
    slot_start,
    window_label_of,
)

FALLBACK_REPORT_TEXT = "이번 세션은 웹캠 신호만으로 계산되었습니다."
INSUFFICIENT_NOTE = "판정 데이터 부족"
SCORE_NOTES: dict[str, str | None] = {
    "full": None,
    "cam_only": "웹캠 신호와 기본 딴짓 규칙만으로 계산된 점수입니다",
    "window_only": "카메라 없이 창 사용만으로 계산된 점수입니다",
    "none": "카메라와 창 분류를 모두 쓸 수 없어 점수를 계산하지 않았습니다",
}


def round_half_up(value: float) -> int:
    """사사오입 (명세 6장). Python round()는 78.5 → 78이라 쓰지 않는다."""
    return math.floor(value + 0.5)


def mock_llm_labels(slots: list[MergedSlot]) -> dict[tuple, WindowLabel]:
    """LLM 분류 대역: 규칙에 걸리지 않은 창만 (app_name, domain, video_category)로 분류한다."""
    labels: dict[tuple, WindowLabel] = {}
    for slot in slots:
        if slot["app_name"] is None or slot["rule_distraction"] is not None:
            continue
        app = (slot["app_name"], slot["domain"])
        label: WindowLabel = "work" if app in WORK_APPS else "distraction" if app in DISTRACTION_APPS else "unknown"
        labels[(slot["app_name"], slot["domain"], slot["video_category"])] = label
    return labels


def choose_mode(slots: list[MergedSlot], durations: list[float], llm_ok: bool) -> ScoreMode:
    """명세 7장: 웹캠 측정 시간이 CAM_USABLE_RATIO 이상이면 웹캠 사용 가능."""
    total = sum(durations)
    cam_sec = sum(d for s, d in zip(slots, durations) if s["face_present"] is not None)
    cam_ok = total > 0 and cam_sec >= CAM_USABLE_RATIO * total
    if cam_ok:
        return "full" if llm_ok else "cam_only"
    return "window_only" if llm_ok else "none"


def apply_grace(slots: list[int], raw: list[FinalTag], grace: int = GRACE_SLOTS) -> list[FinalTag]:
    """명세 4장: 앞뒤가 focus이고 slot 번호가 이어지는 grace 슬롯 이하 이탈은 focus로."""
    final = list(raw)
    n = len(raw)
    i = 0
    while i < n:
        if raw[i] not in AWAY_TAGS:
            i += 1
            continue
        j = i + 1
        while j < n and raw[j] in AWAY_TAGS and slots[j] == slots[j - 1] + 1:
            j += 1
        if (j - i <= grace and i > 0 and j < n
                and raw[i - 1] == "focus" and slots[i] == slots[i - 1] + 1
                and raw[j] == "focus" and slots[j] == slots[j - 1] + 1):
            final[i:j] = ["focus"] * (j - i)
        i = j
    return final


def focus_score(durations: list[float], final_tags: list[FinalTag]) -> int | None:
    """명세 6장: 판정 시간 중 집중 시간 비율. 판정 데이터 부족이면 None."""
    total_sec = sum(durations)
    judged_sec = sum(d for d, t in zip(durations, final_tags) if t in JUDGED_TAGS)
    if judged_sec < MIN_JUDGED_SEC or judged_sec < MIN_JUDGED_RATIO * total_sec:
        return None
    focus_sec = sum(d for d, t in zip(durations, final_tags) if t == "focus")
    return round_half_up(100 * focus_sec / judged_sec)


def top_distractions(scores: list[SlotScore], slots: list[MergedSlot], durations: list[float]) -> list[Distraction]:
    """명세 8장: final_tag == distraction 시간을 창(백그라운드는 카테고리)별로 합해 상위 N개."""
    totals: dict[tuple, float] = {}
    for score, slot, d in zip(scores, slots, durations):
        if score["final_tag"] != "distraction":
            continue
        if score["rule_distraction"] == "background":
            key = (None, None, slot["background_category"], True)
        else:
            key = (score["app_name"], score["domain"], score["video_category"], False)
        totals[key] = totals.get(key, 0.0) + d
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:TOP_DISTRACTIONS_N]
    return [{"app_name": app, "domain": domain, "video_category": category, "background": background,
             "seconds": round_half_up(sec)}
            for (app, domain, category, background), sec in ranked]


def longest_focus(scores: list[SlotScore], durations: list[float]) -> float:
    """slot 번호가 이어지는 final_tag == focus 구간의 최장 시간 (정지로 끊기면 끊김)."""
    best = run = 0.0
    prev: int | None = None
    for score, d in zip(scores, durations):
        if score["final_tag"] != "focus":
            run = 0.0
        elif prev is not None and score["slot"] == prev + 1 and run > 0:
            run += d
        else:
            run = d
        best = max(best, run)
        prev = score["slot"]
    return best


def blinks_per_minute(slots: list[MergedSlot], durations: list[float]) -> float | None:
    """얼굴이 있던 슬롯의 깜빡임 수 ÷ 그 시간(분). 그 시간이 BLINK_RATE_MIN_SEC 미만이면 None."""
    face_sec = sum(d for s, d in zip(slots, durations) if s["face_present"] is True)
    if face_sec < BLINK_RATE_MIN_SEC:
        return None
    blinks = sum(s["blink_count"] for s in slots if s["face_present"] is True)
    return round(blinks / (face_sec / 60), 2)


class MockScorer:
    def __init__(self, llm_fail: bool = False) -> None:
        self.llm_fail = llm_fail
        self.calls: list[list[MergedSlot]] = []  # 테스트에서 넘겨받은 슬롯 확인용

    def __call__(
        self,
        slots: list[MergedSlot],
        calibration: Calibration,
        pause_intervals: list[PauseInterval],
        *,
        session_id: str,
        session_start: float,
        session_end: float,
    ) -> SessionResult:
        self.calls.append(slots)
        starts = [slot_start(s["slot"], session_start) for s in slots]
        ends = [min(start + SLOT_SECONDS, session_end) for start in starts]
        durations = [max(end - start, 0.0) for start, end in zip(starts, ends)]

        llm_labels = {} if self.llm_fail else mock_llm_labels(slots)
        mode = choose_mode(slots, durations, llm_ok=not self.llm_fail)
        labels = [window_label_of(s, llm_labels) for s in slots]
        raw = [raw_tag(s, label, mode) for s, label in zip(slots, labels)]
        final = apply_grace([s["slot"] for s in slots], raw)

        slot_scores: list[SlotScore] = [{
            "slot": s["slot"],
            "start": start,
            "end": end,
            "app_name": s["app_name"],
            "domain": s["domain"],
            "video_category": s["video_category"],
            "rule_distraction": s["rule_distraction"],
            "window_label": label,
            "raw_tag": r,
            "final_tag": f,
            "blink_count": s["blink_count"],
        } for s, start, end, label, r, f in zip(slots, starts, ends, labels, raw, final)]

        score = None if mode == "none" else focus_score(durations, final)
        if mode == "none":
            note = SCORE_NOTES["none"]
        elif score is None:
            note = INSUFFICIENT_NOTE
        else:
            note = SCORE_NOTES[mode]
        tag_sec = {tag: 0.0 for tag in FINAL_TAGS}
        for tag, d in zip(final, durations):
            tag_sec[tag] += d

        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "session_start": session_start,
            "session_end": session_end,
            "ear_baseline": calibration["ear_baseline"],
            "pause_intervals": [p.copy() for p in pause_intervals],
            "slot_scores": slot_scores,
            "score_mode": mode,
            "focus_score": score,
            "score_note": note,
            "tag_seconds": {tag: round_half_up(sec) for tag, sec in tag_sec.items()},
            "judged_seconds": round_half_up(sum(tag_sec[t] for t in JUDGED_TAGS)),
            "top_distractions": top_distractions(slot_scores, slots, durations),
            "longest_focus_seconds": round_half_up(longest_focus(slot_scores, durations)),
            "blinks_per_minute": blinks_per_minute(slots, durations),
            "report_text": FALLBACK_REPORT_TEXT if self.llm_fail else self._report_text(slot_scores),
            "llm_call_failed": self.llm_fail,
        }

    @staticmethod
    def _report_text(slot_scores: list[SlotScore]) -> str:
        if not slot_scores:
            return "(mock) 기록된 슬롯이 없어요."
        focus = sum(1 for s in slot_scores if s["final_tag"] == "focus")
        return f"(mock) {len(slot_scores)}개 슬롯 중 {focus}개가 {FINAL_TAG_KO['focus']} 상태였어요."
