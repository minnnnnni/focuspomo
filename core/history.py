"""통계/기록 화면용 세션 요약 (3-01~3-03).

storage.session_store.load_sessions()가 읽은 sessions.jsonl 기록(dict)을 화면에 쓸 값으로 줄인다.
GUI 없이 테스트할 수 있도록 PySide6를 쓰지 않는다.

schema_version별 읽기 (3-03, 스키마 명세 v2·v4 변경점, 계획서 3장)
- v1: pause_intervals가 없다 → [] (일시정지 기능이 없었음)
- v3 이하: v4 점수 필드(focus_score, score_mode, judged_seconds, tag_seconds)가 없다.
  점수 방식이 달라(슬롯 가감점 합) v4 점수와 섞어 보이지 않게 focus_score는 None으로 두고 legacy로 표시한다.
  집중 시간은 slot_scores의 final_tag로 다시 센다.
- v4: 저장된 focus_score·judged_seconds·tag_seconds를 그대로 쓴다.
- 처음 보는 더 높은 버전은 v4처럼 읽는다 (필드가 없으면 위와 같이 None·슬롯 기준으로).

졸음·딴짓·자리비움 "횟수"는 slot 번호가 이어지는 같은 final_tag 슬롯 묶음 수다 (모든 버전 공통,
정지로 번호가 끊기면 다른 묶음). 30초 졸면 슬롯 3개지만 1회로 센다.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass

from schemas import pause_total

logger = logging.getLogger(__name__)

SCORE_VERSION = 4  # 이 버전부터 focus_score(판정 시간 중 집중 비율)가 있다


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    schema_version: int
    session_start: float
    session_end: float
    pause_count: int
    pause_seconds: float
    active_seconds: float  # 실제 세션 시간 = session_end - session_start - 정지 시간 (계획서 3장)
    focus_score: int | None  # v4만. v3 이하이거나 계산 불가(none 모드·판정 부족)면 None
    score_mode: str | None  # v3 이하는 None
    judged_seconds: int | None  # v3 이하는 None
    focus_seconds: int  # final_tag == "focus" 시간
    drowsy_count: int
    distraction_count: int
    away_count: int

    @property
    def legacy(self) -> bool:
        """v4 점수 방식 이전 기록인지 (점수를 표시하지 않는다)."""
        return self.schema_version < SCORE_VERSION


def summarize(record: dict) -> SessionSummary:
    """sessions.jsonl 한 줄(dict)을 SessionSummary로. 필수 값이 없거나 타입이 틀리면 ValueError."""
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    version = _int(record.get("schema_version"), "schema_version")
    session_id = record.get("session_id")
    if not isinstance(session_id, str):
        raise ValueError("session_id must be a str")
    start = _number(record.get("session_start"), "session_start")
    end = _number(record.get("session_end"), "session_end")  # 시계가 바뀌어 start보다 앞서도 기록은 보여 준다

    pauses = record.get("pause_intervals", [])  # v1에는 없음
    if not isinstance(pauses, list):
        raise ValueError("pause_intervals must be a list")
    for p in pauses:
        if not isinstance(p, dict):
            raise ValueError("pause interval must be a dict")
        _number(p.get("start"), "pause.start")
        _number(p.get("end"), "pause.end")
    paused = pause_total(pauses)

    slots = record.get("slot_scores", [])
    if not isinstance(slots, list) or not all(isinstance(s, dict) for s in slots):
        raise ValueError("slot_scores must be a list of dicts")

    scored = version >= SCORE_VERSION
    tag_seconds = record.get("tag_seconds") if scored else None
    if isinstance(tag_seconds, dict) and _is_number(tag_seconds.get("focus")):
        focus_seconds = int(tag_seconds["focus"])
    else:
        focus_seconds = _tag_seconds(slots, "focus")

    return SessionSummary(
        session_id=session_id,
        schema_version=version,
        session_start=start,
        session_end=end,
        pause_count=len(pauses),
        pause_seconds=paused,
        active_seconds=max(end - start - paused, 0.0),
        focus_score=_optional_int(record.get("focus_score")) if scored else None,
        score_mode=record.get("score_mode") if scored and isinstance(record.get("score_mode"), str) else None,
        judged_seconds=_optional_int(record.get("judged_seconds")) if scored else None,
        focus_seconds=focus_seconds,
        drowsy_count=tag_episodes(slots, "drowsy"),
        distraction_count=tag_episodes(slots, "distraction"),
        away_count=tag_episodes(slots, "away"),
    )


def summarize_all(records: Iterable[dict]) -> list[SessionSummary]:
    """기록 목록을 요약해 session_start 오름차순(오래된 것 먼저)으로 돌려준다. 읽을 수 없는 기록은 건너뛴다."""
    summaries = []
    for record in records:
        try:
            summaries.append(summarize(record))
        except ValueError as e:
            sid = record.get("session_id") if isinstance(record, dict) else None
            logger.warning("세션 기록을 읽지 못해 건너뜁니다 (%s): %s", sid, e)
    summaries.sort(key=lambda s: s.session_start)
    return summaries


def tag_episodes(slot_scores: list[dict], tag: str) -> int:
    """slot 번호가 이어지는 final_tag == tag 슬롯 묶음 수."""
    count = 0
    prev: int | None = None  # 직전 tag 슬롯 번호
    for slot in sorted(slot_scores, key=lambda s: s.get("slot", 0)):
        number = slot.get("slot")
        if slot.get("final_tag") != tag or not isinstance(number, int):
            prev = None
            continue
        if prev is None or number != prev + 1:
            count += 1
        prev = number
    return count


def average_score(summaries: Iterable[SessionSummary]) -> float | None:
    """점수가 있는 세션들의 평균 focus_score. 하나도 없으면 None."""
    scores = [s.focus_score for s in summaries if s.focus_score is not None]
    return sum(scores) / len(scores) if scores else None


def total_focus_seconds(summaries: Iterable[SessionSummary]) -> int:
    return sum(s.focus_seconds for s in summaries)


def _tag_seconds(slot_scores: list[dict], tag: str) -> int:
    total = 0.0
    for slot in slot_scores:
        if slot.get("final_tag") != tag:
            continue
        start, end = slot.get("start"), slot.get("end")
        if _is_number(start) and _is_number(end) and end > start:
            total += end - start
    return int(total + 0.5)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _number(value: object, name: str) -> float:
    if not _is_number(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an int")
    return value


def _optional_int(value: object) -> int | None:
    return int(value) if _is_number(value) else None
