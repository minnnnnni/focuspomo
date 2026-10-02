"""세션 슬롯 로그: slot_queue 메시지를 slot 번호로 병합한다 (개발계획서 2.6 "GUI 병합 규칙").

GUI 없이 테스트할 수 있도록 PySide6를 import하지 않는다.
GUI는 QTimer에서 큐를 비우며 add()를 부르고, 세션 종료 시 finalize()로 슬롯 로그를 받는다.

- CV·창 메시지를 따로 보관했다가 finalize()에서 합치므로 도착 순서와 무관하게 결과가 같다.
- 같은 슬롯 창 메시지가 두 번 오면 timestamp가 늦은 것(= 나중 조회)이 남는다. 같으면 나중 도착.
- 같은 슬롯 CV 메시지가 두 번 오면 나중 도착이 남는다 (1초 원본도 함께 교체).
"""
from __future__ import annotations

from schemas import (
    CvSecond,
    CvSlot,
    MergedSlot,
    SlotMessage,
    WindowSlot,
    empty_cv_part,
    empty_window_part,
    slot_start,
)


class SlotLog:
    def __init__(self, session_start: float) -> None:
        self.session_start = session_start
        self._cv: dict[int, CvSlot] = {}
        self._window: dict[int, WindowSlot] = {}
        self._seconds: dict[int, list[CvSecond]] = {}

    def add(self, msg: SlotMessage) -> None:
        """큐 메시지 하나를 반영한다. source가 "cv"/"window"가 아니면 ValueError."""
        source = msg.get("source")
        if source == "cv":
            cv_slot: CvSlot = dict(msg["slot"])  # type: ignore[assignment]
            slot = cv_slot["slot"]
            self._cv[slot] = cv_slot
            self._seconds[slot] = [dict(s) for s in msg["seconds"]]  # type: ignore[misc]
        elif source == "window":
            win: WindowSlot = dict(msg["slot"])  # type: ignore[assignment]
            prev = self._window.get(win["slot"])
            if prev is None or win["timestamp"] >= prev["timestamp"]:
                self._window[win["slot"]] = win
        else:
            raise ValueError(f"unknown slot message source: {source!r}")

    def __len__(self) -> int:
        """메시지가 하나라도 온 슬롯 수."""
        return len(self._cv.keys() | self._window.keys())

    def finalize(self) -> list[MergedSlot]:
        """한쪽 메시지가 없는 슬롯을 기본값으로 채워 slot 순서대로 돌려준다.

        로그 상태는 바꾸지 않으므로 여러 번 불러도 된다 (늦게 온 메시지 반영 후 재호출 가능).
        """
        merged: list[MergedSlot] = []
        for slot in sorted(self._cv.keys() | self._window.keys()):
            cv = self._cv.get(slot)
            win = self._window.get(slot)
            row: dict = {"slot": slot, "timestamp": slot_start(slot, self.session_start)}
            if cv is None:
                row.update(empty_cv_part())
            else:
                row.update({k: v for k, v in cv.items() if k != "slot"})
            if win is None:
                row.update(empty_window_part())
            else:
                row.update({"app_name": win["app_name"], "domain": win["domain"]})
            merged.append(row)  # type: ignore[arg-type]
        return merged

    def cv_seconds(self) -> list[CvSecond]:
        """focus_log 저장용 1초 원본을 slot·시각 순으로 이어 붙인다."""
        out: list[CvSecond] = []
        for slot in sorted(self._seconds):
            seconds = sorted(self._seconds[slot], key=lambda s: s["timestamp"])
            out.extend(dict(s) for s in seconds)  # type: ignore[misc]
        return out
