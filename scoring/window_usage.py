"""리포트용 창 사용 집계 (점수 명세 8장): 어떤 창을 얼마나, 언제 썼는지.

SessionResult.slot_scores만으로 계산한다 (저장 형식은 바꾸지 않음).
리포트 화면(gui/report_view.py)의 "많이 쓴 창"·구간별 기록과 리포트 문구(scoring/report_text.py)가 같이 쓴다.

- 같은 창: (app_name, domain, video_category, 백그라운드 딴짓 여부)가 같은 슬롯.
  백그라운드 딴짓 슬롯은 앞 화면 창이 같아도 따로 묶는다 ("VS Code + 백그라운드 재생").
- 연속: slot 번호가 1씩 이어질 때. 일시정지로 빠진 슬롯이 있으면 끊긴다.
- 시간: 슬롯의 end - start 합 (마지막 슬롯은 10초보다 짧을 수 있음).
"""
from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass

from schemas import SlotScore

WindowKey = tuple[str | None, str | None, str | None, bool]  # app_name, domain, video_category, 백그라운드

BACKGROUND_SUFFIX = " + 백그라운드 재생"
NO_WINDOW_TEXT = "창 정보 없음"


def window_key(s: SlotScore) -> WindowKey:
    return (s["app_name"], s["domain"], s["video_category"], s["rule_distraction"] == "background")


def window_name(s: SlotScore) -> str:
    """표시 이름: 사이트가 있으면 도메인, 없으면 앱 이름. 유튜브 카테고리는 [ ]로.

    예: "VS Code", "youtube.com", "youtube.com [Gaming]", "VS Code + 백그라운드 재생", "창 정보 없음".
    """
    name = s["domain"] or s["app_name"] or NO_WINDOW_TEXT
    if s["video_category"]:
        name = f"{name} [{s['video_category']}]"
    if s["rule_distraction"] == "background":
        name += BACKGROUND_SUFFIX
    return name


def slot_seconds(s: SlotScore) -> float:
    return max(s["end"] - s["start"], 0.0)


@dataclass(frozen=True)
class WindowUsage:
    """세션 전체에서 한 창을 쓴 시간."""
    name: str
    window_label: str
    seconds: float
    slots: int


@dataclass(frozen=True)
class Segment:
    """이어지는 슬롯 묶음 하나."""
    start: float          # 첫 슬롯 시작 (epoch 초)
    end: float            # 마지막 슬롯 끝
    name: str             # window_name
    window_label: str
    final_tag: str        # 묶음 안 첫 슬롯의 상태 (by_state=True면 모두 같음)
    seconds: float
    slots: int
    blink_count: int

    def offset(self, session_start: float) -> float:
        """세션 시작부터 이 구간이 시작하기까지 걸린 시간 (정지 시간 포함)."""
        return self.start - session_start


def _ordered(slot_scores: list[SlotScore]) -> list[SlotScore]:
    return sorted(slot_scores, key=lambda s: s["slot"])


def usage_by_window(slot_scores: list[SlotScore]) -> list[WindowUsage]:
    """창별 사용 시간, 오래 쓴 순 (같으면 먼저 나온 순)."""
    totals: dict[WindowKey, list] = {}
    for s in _ordered(slot_scores):
        entry = totals.setdefault(window_key(s), [window_name(s), s["window_label"], 0.0, 0])
        entry[2] += slot_seconds(s)
        entry[3] += 1
    usages = [WindowUsage(name, label, seconds, slots) for name, label, seconds, slots in totals.values()]
    return sorted(usages, key=lambda u: -u.seconds)  # 정렬은 안정적이라 동률이면 먼저 나온 순


def _group(slot_scores: list[SlotScore], key: Callable[[SlotScore], Hashable]) -> list[Segment]:
    segments: list[Segment] = []
    run: list[SlotScore] = []

    def close() -> None:
        if run:
            first, last = run[0], run[-1]
            segments.append(Segment(
                start=first["start"], end=last["end"], name=window_name(first),
                window_label=first["window_label"], final_tag=first["final_tag"],
                seconds=sum(slot_seconds(s) for s in run), slots=len(run),
                blink_count=sum(s["blink_count"] for s in run),
            ))

    for s in _ordered(slot_scores):
        if run and (s["slot"] != run[-1]["slot"] + 1 or key(s) != key(run[-1])):
            close()
            run = []
        run.append(s)
    close()
    return segments


def segments(slot_scores: list[SlotScore]) -> list[Segment]:
    """이어지는 슬롯 중 창·창 분류·상태가 같은 것을 한 구간으로. 리포트 "구간별 기록" 표의 한 행."""
    return _group(slot_scores, lambda s: (window_key(s), s["window_label"], s["final_tag"]))


def window_runs(slot_scores: list[SlotScore]) -> list[Segment]:
    """이어지는 슬롯 중 같은 창을 한 구간으로 (상태가 바뀌어도 이어 봄). "이때 유튜브를 오래 봤다"를 찾는 용도."""
    return _group(slot_scores, window_key)


def long_runs(slot_scores: list[SlotScore], *, min_seconds: float = 60.0, limit: int = 3,
              exclude_labels: tuple[str, ...] = ("work",)) -> list[Segment]:
    """작업이 아닌 창을 min_seconds 이상 이어서 쓴 구간, 긴 순 (같으면 먼저 나온 순). 창 정보가 없는 구간은 뺀다."""
    runs = [r for r in window_runs(slot_scores)
            if r.window_label not in exclude_labels and r.seconds >= min_seconds and r.name != NO_WINDOW_TEXT]
    return sorted(runs, key=lambda r: -r.seconds)[:limit]
