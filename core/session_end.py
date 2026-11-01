"""세션 종료 처리: 슬롯 로그 확정 → 정지 구간 슬롯 제외 → 점수 계산 → 저장할 결과 조립 (개발계획서 2.4, 2.6).

GUI 없이 테스트할 수 있도록 PySide6를 import하지 않는다.
MainWindow는 CV 마지막 flush를 기다린 뒤 큐를 마지막으로 비우고 finish_session()을 부른다.

- 점수 모듈(2.3)에는 정지 구간과 조금이라도 겹친 슬롯을 뺀 슬롯만 넘긴다 (schemas.slot_overlaps_pause).
  slot 번호는 그대로라 중간이 빌 수 있다.
- focus_log의 merged_slots에는 정지 구간 슬롯까지 포함한 원본 전체를 남긴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.slot_log import SlotLog
from schemas import (
    SCHEMA_VERSION,
    Calibration,
    FocusLogFile,
    MergedSlot,
    PauseInterval,
    SessionResult,
    make_session_id,
    slot_overlaps_pause,
)


class ScoreFn(Protocol):
    """점수 모듈(신명철, 2.3) 호출 형태. 지금은 mocks.mock_scoring.MockScorer가 대신한다.

    TODO(2-05): 실제 시그니처가 정해지면 맞춘다 (PROGRESS "모듈 간 약속").
    """

    def __call__(
        self,
        slots: list[MergedSlot],
        calibration: Calibration,
        pause_intervals: list[PauseInterval],
        *,
        session_id: str,
        session_start: float,
        session_end: float,
    ) -> SessionResult: ...


@dataclass(frozen=True)
class SessionOutput:
    result: SessionResult        # sessions.jsonl 한 줄, 리포트 화면에 렌더링
    focus_log: FocusLogFile      # focus_log/{session_id}.json
    excluded_slots: list[int]    # 정지 구간과 겹쳐 점수에서 뺀 slot 번호


def slots_for_scoring(
    merged: list[MergedSlot], session_start: float, pause_intervals: list[PauseInterval]
) -> tuple[list[MergedSlot], list[int]]:
    """정지 구간과 겹친 슬롯을 뺀다. (점수에 넘길 슬롯, 뺀 slot 번호)를 돌려준다."""
    kept: list[MergedSlot] = []
    excluded: list[int] = []
    for slot in merged:
        if slot_overlaps_pause(slot["slot"], session_start, pause_intervals):
            excluded.append(slot["slot"])
        else:
            kept.append(slot)
    return kept, excluded


def finish_session(
    slot_log: SlotLog,
    calibration: Calibration,
    pause_intervals: list[PauseInterval],
    session_end: float,
    score: ScoreFn,
) -> SessionOutput:
    """세션 종료 시 한 번 부른다. slot_log에는 마지막 메시지까지 반영돼 있어야 한다."""
    session_start = slot_log.session_start
    pauses: list[PauseInterval] = sorted((p.copy() for p in pause_intervals), key=lambda p: p["start"])
    merged = slot_log.finalize(session_end)
    kept, excluded = slots_for_scoring(merged, session_start, pauses)
    session_id = make_session_id(session_start)
    result = score(
        [dict(s) for s in kept],  # type: ignore[misc]
        calibration,
        [p.copy() for p in pauses],
        session_id=session_id,
        session_start=session_start,
        session_end=session_end,
    )
    focus_log: FocusLogFile = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "session_start": session_start,
        "calibration": dict(calibration),  # type: ignore[typeddict-item]
        "pause_intervals": pauses,
        "cv_seconds": slot_log.cv_seconds(session_end),
        "merged_slots": merged,
    }
    return SessionOutput(result=result, focus_log=focus_log, excluded_slots=excluded)
