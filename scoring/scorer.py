"""실제 점수 함수 (INT-05): LLM 창 분류 → 점수 계산 → 리포트 문구. core.session_end.ScoreFn 형태.

    scorer = LlmScorer.from_env()          # .env의 키로 클라이언트를 만든다. 키가 없으면 LLM 없이
    result = scorer(slots, calibration, pause_intervals, session_id=..., session_start=..., session_end=...)

순서 (점수 명세 1장):
1. 규칙에 걸리지 않은 고유 창 목록을 LLM으로 분류 (scoring.llm_classify). 실패하면 llm_labels=None.
   분류가 성공하면 FocusPomo 창 자체는 로컬에서 작업으로 더한다 (local_labels). 실패하면(cam_only) 어차피 집중으로 본다
2. scoring.focus_score.score_session — 모드·태그·점수·집계 (LLM 실패면 cam_only/none 모드, llm_call_failed=True)
3. 리포트 문구 — 분류가 성공했으면 LLM 문구, 아니면(또는 문구 호출이 실패하면) 템플릿 문구 (scoring.report_text)

추적 모드 (core.tracking, use_camera / use_window):
- 창 추적을 껐으면(웹캠만·타이머만) 창 분류 호출을 하지 않는다 (보낼 창도 없다). 웹캠만 모드는 리포트 문구 호출은 한다
  (태그별 시간 같은 집계값만 나가고, 창 이름은 없다).
- 타이머만 모드는 LLM을 전혀 부르지 않고 문구도 비운다 (점수가 없어 할 말이 없다. 리포트는 score_note로 안내).

네트워크를 쓰므로 수 초~20초 걸릴 수 있다. MainWindow(score_in_background=True)로 GUI 밖에서 부른다.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from schemas import Calibration, MergedSlot, PauseInterval, SessionResult
from scoring.focus_score import score_session
from scoring.llm_classify import JsonChat, build_items, classify_windows, local_labels
from scoring.llm_client import ChatClient
from scoring.report_text import generate_report_text, template_report_text

logger = logging.getLogger(__name__)


class LlmScorer:
    """client가 None이면 LLM 없이 계산한다 (키 없음, --no-llm)."""

    def __init__(self, client: JsonChat | None, *, llm_report: bool = True,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client = client
        self.llm_report = llm_report
        self._sleep = sleep

    @classmethod
    def from_env(cls, enabled: bool = True) -> LlmScorer:
        client = ChatClient.from_env() if enabled else None
        if client is not None:
            logger.info("LLM 창 분류 사용: %r", client)
        elif enabled:
            logger.warning("LLM API 키가 없어 창 분류 없이 계산합니다 (.env의 GEMINI_API_KEY 등, .env.example 참고)")
        else:
            logger.info("LLM을 끄고 실행합니다 (--no-llm)")
        return cls(client)

    def __call__(
        self,
        slots: list[MergedSlot],
        calibration: Calibration,
        pause_intervals: list[PauseInterval],
        *,
        session_id: str,
        session_start: float,
        session_end: float,
        use_camera: bool = True,
        use_window: bool = True,
    ) -> SessionResult:
        if use_window:
            labels = classify_windows(build_items(slots), self.client, sleep=self._sleep)
            if labels is not None:
                labels = {**labels, **local_labels(slots)}
        else:
            labels = {}  # 창 추적을 껐다: 분류할 창이 없고 호출하지 않는다
        result = score_session(
            slots, calibration, pause_intervals,
            session_id=session_id, session_start=session_start, session_end=session_end,
            llm_labels=labels, report_text="", use_camera=use_camera, use_window=use_window,
        )
        if use_camera or use_window:
            result["report_text"] = self.report_text(result, llm_ok=labels is not None)
        return result

    def report_text(self, result: SessionResult, *, llm_ok: bool) -> str:
        """분류가 성공했고 기록이 있으면 LLM 문구, 아니면 템플릿. 분류가 실패한 세션은 문구 호출도 하지 않는다."""
        if llm_ok and self.llm_report and self.client is not None and result["slot_scores"]:
            text = generate_report_text(result, self.client)
            if text:
                return text
        return template_report_text(result)
