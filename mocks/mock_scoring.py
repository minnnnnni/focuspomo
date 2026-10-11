"""점수 모듈 mock (신명철 SCORE-01~03 대역, 2-05에서 실제 모듈로 교체).

core.session_end.ScoreFn 형태로 SessionResult를 만든다.
- LLM 창 분류 대신 mocks.mock_window_slots의 앱 목록으로 window_label을 정한다
  (작업 앱 → "work", 딴짓 앱 → "distraction", 그 외·조회 실패 → "unknown").
- 점수는 개발계획서 2.5 공식, final_tag는 schemas.final_tag (2.7 우선순위).
- llm_fail=True면 LLM 실패 폴백: 모든 window_label "unknown", 폴백 문구, llm_call_failed=True.
"""
from __future__ import annotations

from config.scoring_weights import WEIGHTS
from mocks.mock_window_slots import DISTRACTION_APPS, WORK_APPS
from schemas import (
    SCHEMA_VERSION,
    SLOT_SECONDS,
    Calibration,
    FINAL_TAG_KO,
    MergedSlot,
    PauseInterval,
    SessionResult,
    SlotScore,
    WindowLabel,
    final_tag,
    slot_start,
)

FALLBACK_REPORT_TEXT = "이번 세션은 웹캠 신호만으로 계산되었습니다."


def mock_window_label(slot: MergedSlot) -> WindowLabel:
    app = (slot["app_name"], slot["domain"])
    if app in WORK_APPS:
        return "work"
    if app in DISTRACTION_APPS:
        return "distraction"
    return "unknown"


def slot_score(slot: MergedSlot, window_label: WindowLabel, weights: dict = WEIGHTS) -> int:
    """개발계획서 2.5 slot_score 그대로."""
    score = 0
    score += min(slot["blink_count"], weights["blink_bonus_cap"]) * weights["blink_bonus_per_blink"]
    if slot["drowsy"]:
        score -= weights["drowsy_penalty"]
    if slot["face_present"] is False:  # None(측정 불가)은 감점하지 않음
        score -= weights["face_away_penalty"]
    if window_label == "distraction":
        score -= weights["distraction_penalty"]
    return score


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
        slot_scores: list[SlotScore] = []
        for slot in slots:
            label: WindowLabel = "unknown" if self.llm_fail else mock_window_label(slot)
            start = slot_start(slot["slot"], session_start)
            slot_scores.append({
                "slot": slot["slot"],
                "start": start,
                "end": min(start + SLOT_SECONDS, session_end),
                "window_label": label,
                "final_tag": final_tag(slot["face_present"], slot["drowsy"], label),
                "blink_count": slot["blink_count"],
                "score": slot_score(slot, label),
            })
        by_slot = {s["slot"]: s for s in slots}
        drowsy = sum(1 for s in slots if s["drowsy"])
        away = sum(1 for s in slots if s["face_present"] is False)
        distraction = sum(1 for s in slot_scores if s["window_label"] == "distraction")
        blink_bonus = sum(
            min(by_slot[s["slot"]]["blink_count"], WEIGHTS["blink_bonus_cap"]) * WEIGHTS["blink_bonus_per_blink"]
            for s in slot_scores
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "session_start": session_start,
            "session_end": session_end,
            "ear_baseline": calibration["ear_baseline"],
            "pause_intervals": [p.copy() for p in pause_intervals],
            "slot_scores": slot_scores,
            "score_total": sum(s["score"] for s in slot_scores),
            "blink_bonus_total": blink_bonus,
            "drowsy_count": drowsy,
            "away_count": away,
            "distraction_count": distraction,
            "report_text": FALLBACK_REPORT_TEXT if self.llm_fail else self._report_text(slot_scores),
            "llm_call_failed": self.llm_fail,
        }

    @staticmethod
    def _report_text(slot_scores: list[SlotScore]) -> str:
        if not slot_scores:
            return "(mock) 기록된 슬롯이 없어요."
        focus = sum(1 for s in slot_scores if s["final_tag"] == "focus")
        return f"(mock) {len(slot_scores)}개 슬롯 중 {focus}개가 {FINAL_TAG_KO['focus']} 상태였어요."
