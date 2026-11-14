"""점수 모듈 mock: LLM 창 분류·리포트 문구 대역 (SCORE-02·03 전까지).

점수 계산은 실제 모듈 scoring.focus_score.score_session(SCORE-01)을 그대로 쓰고,
LLM이 할 일만 흉내 낸다. core.session_end.ScoreFn 형태로 SessionResult(v4)를 돌려준다.
- 창 분류: mocks.mock_window_slots의 앱 목록으로 라벨을 정한다
  (작업 앱 → "work", 딴짓 앱 → "distraction", 그 외 → "unknown"). 규칙상 딴짓 슬롯은 보내지 않는다
- llm_fail=True면 LLM 실패: llm_labels=None(→ cam_only/none 모드), 폴백 문구, llm_call_failed=True
"""
from __future__ import annotations

from mocks.mock_window_slots import DISTRACTION_APPS, WORK_APPS
from schemas import FINAL_TAG_KO, Calibration, MergedSlot, PauseInterval, SessionResult, SlotScore
from scoring.focus_score import LlmLabels, score_session

FALLBACK_REPORT_TEXT = "이번 세션은 웹캠 신호만으로 계산되었습니다."


def mock_llm_labels(slots: list[MergedSlot]) -> LlmLabels:
    """LLM 분류 대역: 규칙에 걸리지 않은 창만 (app_name, domain, video_category)로 분류한다."""
    labels: LlmLabels = {}
    for slot in slots:
        if slot["app_name"] is None or slot["rule_distraction"] is not None:
            continue
        app = (slot["app_name"], slot["domain"])
        labels[(slot["app_name"], slot["domain"], slot["video_category"])] = (
            "work" if app in WORK_APPS else "distraction" if app in DISTRACTION_APPS else "unknown")
    return labels


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
        result = score_session(
            slots, calibration, pause_intervals,
            session_id=session_id, session_start=session_start, session_end=session_end,
            llm_labels=None if self.llm_fail else mock_llm_labels(slots),
            report_text=FALLBACK_REPORT_TEXT,
        )
        if not self.llm_fail:
            result["report_text"] = self._report_text(result["slot_scores"])  # 문구는 점수 계산 뒤 (5장)
        return result

    @staticmethod
    def _report_text(slot_scores: list[SlotScore]) -> str:
        if not slot_scores:
            return "(mock) 기록된 슬롯이 없어요."
        focus = sum(1 for s in slot_scores if s["final_tag"] == "focus")
        return f"(mock) {len(slot_scores)}개 슬롯 중 {focus}개가 {FINAL_TAG_KO['focus']} 상태였어요."
