"""뽀모도로 휴식 규칙 (1-15). GUI 없이 테스트할 수 있도록 PySide6를 import하지 않는다.

- 집중 세션이 끝나면 짧은 휴식(5분), 끝까지 마친 세션이 LONG_BREAK_EVERY개 쌓일 때마다 긴 휴식(15분).
- "중간 종료"한 세션은 마친 세션으로 세지 않는다 (짧은 휴식만 준다).
- 긴 휴식 콘텐츠는 팀 미정이라 시간만 정한다.

TODO: 휴식 길이를 config/로 옮길지 팀 합의 (SESSION_SEC과 같이)
"""
from __future__ import annotations

from enum import StrEnum

SHORT_BREAK_SEC = 5 * 60
LONG_BREAK_SEC = 15 * 60
LONG_BREAK_EVERY = 4


class BreakKind(StrEnum):
    SHORT = "short"
    LONG = "long"


BREAK_SECONDS: dict[BreakKind, int] = {BreakKind.SHORT: SHORT_BREAK_SEC, BreakKind.LONG: LONG_BREAK_SEC}


def break_after(session_completed: bool, completed_sessions: int) -> BreakKind:
    """방금 끝난 세션 뒤의 휴식 종류.

    session_completed: 방금 세션을 25분 끝까지 마쳤는지
    completed_sessions: 방금 세션까지 포함해 끝까지 마친 세션 수
    """
    if session_completed and completed_sessions > 0 and completed_sessions % LONG_BREAK_EVERY == 0:
        return BreakKind.LONG
    return BreakKind.SHORT


def cycle_position(completed_sessions: int) -> int:
    """다음 세션이 긴 휴식 주기(LONG_BREAK_EVERY개)의 몇 번째인지 (1부터). 화면의 "세션 n/4" 표시용."""
    return max(completed_sessions, 0) % LONG_BREAK_EVERY + 1
