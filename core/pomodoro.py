"""뽀모도로 세션·휴식 규칙 (1-15). GUI 없이 테스트할 수 있도록 PySide6를 import하지 않는다.

- 집중 세션이 끝나면 짧은 휴식, 끝까지 마친 세션이 long_break_every개 쌓일 때마다 긴 휴식.
- "중간 종료"한 세션은 마친 세션으로 세지 않는다 (짧은 휴식만 준다).
- 집중·휴식 길이와 긴 휴식 주기는 설정 화면에서 바꾼다 (PomodoroSettings, storage.settings_store가 저장).
  기본값은 25분 / 5분 / 15분 / 4세션. 긴 휴식 콘텐츠는 팀 미정이라 시간만 정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from enum import StrEnum

# 기본값 (설정을 저장한 적 없을 때)
SESSION_SEC = 25 * 60
SHORT_BREAK_SEC = 5 * 60
LONG_BREAK_SEC = 15 * 60
LONG_BREAK_EVERY = 4


class BreakKind(StrEnum):
    SHORT = "short"
    LONG = "long"


BREAK_SECONDS: dict[BreakKind, int] = {BreakKind.SHORT: SHORT_BREAK_SEC, BreakKind.LONG: LONG_BREAK_SEC}

# 설정 화면에서 고를 수 있는 범위 (양 끝 포함)
SETTING_LIMITS: dict[str, tuple[int, int]] = {
    "focus_min": (1, 120),
    "short_break_min": (1, 60),
    "long_break_min": (1, 60),
    "long_break_every": (1, 12),
}


@dataclass(frozen=True)
class PomodoroSettings:
    """집중·휴식 길이(분)와 긴 휴식 주기(마친 세션 수)."""

    focus_min: int = SESSION_SEC // 60
    short_break_min: int = SHORT_BREAK_SEC // 60
    long_break_min: int = LONG_BREAK_SEC // 60
    long_break_every: int = LONG_BREAK_EVERY

    @property
    def focus_sec(self) -> int:
        return self.focus_min * 60

    def break_seconds(self, kind: BreakKind) -> int:
        minutes = self.long_break_min if kind is BreakKind.LONG else self.short_break_min
        return minutes * 60

    def clamped(self) -> PomodoroSettings:
        """각 값을 SETTING_LIMITS 범위 안으로 맞춘 사본 (저장된 파일을 손으로 고쳤을 때 등)."""
        values = {}
        for f in fields(self):
            low, high = SETTING_LIMITS[f.name]
            values[f.name] = min(max(int(getattr(self, f.name)), low), high)
        return replace(self, **values)

    def to_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> PomodoroSettings:
        """저장된 dict에서 만든다. 없거나 숫자가 아닌 값은 기본값, 범위를 벗어난 값은 범위 안으로."""
        default = cls()
        if not isinstance(data, dict):
            return default
        values = {}
        for f in fields(cls):
            value = data.get(f.name)
            ok = isinstance(value, int) and not isinstance(value, bool)
            values[f.name] = value if ok else getattr(default, f.name)
        return cls(**values).clamped()


DEFAULT_SETTINGS = PomodoroSettings()


def break_after(session_completed: bool, completed_sessions: int, every: int = LONG_BREAK_EVERY) -> BreakKind:
    """방금 끝난 세션 뒤의 휴식 종류.

    session_completed: 방금 세션을 설정한 집중 시간 끝까지 마쳤는지
    completed_sessions: 방금 세션까지 포함해 끝까지 마친 세션 수
    every: 긴 휴식 주기 (마친 세션 수)
    """
    every = max(every, 1)
    if session_completed and completed_sessions > 0 and completed_sessions % every == 0:
        return BreakKind.LONG
    return BreakKind.SHORT


def cycle_position(completed_sessions: int, every: int = LONG_BREAK_EVERY) -> int:
    """다음 세션이 긴 휴식 주기(every개)의 몇 번째인지 (1부터). 화면의 "세션 n/every" 표시용."""
    return max(completed_sessions, 0) % max(every, 1) + 1
