"""
캘리브레이션 이후 CV 처리 전체를 하나로 묶는다.

프레임(EAR) → 1초 요약 → 연속 감김(closed_run) → 10초 슬롯 메시지

CV 스레드는 프레임마다 add_frame()을 호출하고, 돌려받은 메시지를
그대로 slot_queue.put() 하면 된다. 세션이 끝나면 finish()의 메시지까지 넣는다.

finish()는 마지막 슬롯 뒤에, 얼굴을 놓친 구간을 고개 숙임·졸다 떨굼·자리 뜸으로 나눠(cv_module.head_down)
판정이 바뀐 슬롯을 다시 보낸다 (CV-06). GUI는 같은 슬롯의 나중 메시지를 남기므로 이것으로 바뀐다.
"""

from __future__ import annotations

from typing import Callable

from cv_module.drowsiness import ClosedRunTracker
from cv_module.head_down import resolve_postures
from cv_module.second_summary import SecondAggregator
from cv_module.slot_compressor import SlotCompressor, build_slot
from schemas import CvSecond, CvSlotMessage, slot_index

SecondCallback = Callable[[CvSecond, int], None]


class CvPipeline:
    def __init__(
        self,
        session_start: float,
        ear_baseline: float,
        on_second: SecondCallback | None = None,
        *,
        pitch_baseline: float | None = None,
        head_down: bool = True,
    ) -> None:
        """on_second(sec, closed_run): 1초 요약이 나올 때마다 호출 (디버깅·데모용).

        pitch_baseline: 캘리브레이션 때 잰 평소 고개 각도 (없으면 각도 조건은 건너뜀).
        head_down=False면 finish()에서 소급하지 않는다 (예전 규칙과 비교할 때).
        """
        self.session_start = session_start
        self.ear_baseline = ear_baseline
        self._seconds = SecondAggregator(session_start, ear_baseline, pitch_baseline)
        self._tracker = ClosedRunTracker()
        self._compressor = SlotCompressor(session_start)
        self._on_second = on_second
        self._head_down = head_down
        self._history: list[tuple[CvSecond, int]] = []  # 세션 전체 (1초 요약, closed_run). 소급 판정용

    @property
    def closed_run(self) -> int:
        return self._tracker.run

    def add_frame(self, timestamp: float, ear: float | None, *, pitch: float | None = None,
                  face_y: float | None = None, person: float | None = None) -> list[CvSlotMessage]:
        return self._feed(self._seconds.add_frame(timestamp, ear, pitch=pitch, face_y=face_y, person=person))

    def finish(self) -> list[CvSlotMessage]:
        # 세션 종료 시 호출. 남은 1초와 마지막 슬롯을 마무리한 뒤, 소급 판정으로 바뀐 슬롯을 다시 보낸다.
        out = self._feed(self._seconds.flush()) + self._compressor.flush()
        return out + (self.resend_changed() if self._head_down else [])

    def resend_changed(self) -> list[CvSlotMessage]:
        """얼굴을 놓친 구간에 posture를 붙여, 그런 초가 있는 슬롯을 다시 만든다."""
        seconds = [sec for sec, _ in self._history]
        postures = resolve_postures(seconds, self.ear_baseline)
        by_slot: dict[int, list[tuple[CvSecond, int]]] = {}
        changed: set[int] = set()
        for (sec, run), posture in zip(self._history, postures):
            slot = slot_index(sec["timestamp"], self.session_start)
            by_slot.setdefault(slot, []).append((dict(sec, posture=posture), run))  # type: ignore[arg-type]
            if posture is not None:
                changed.add(slot)
        return [
            build_slot(slot, self.session_start, [s for s, _ in by_slot[slot]], [r for _, r in by_slot[slot]])
            for slot in sorted(changed)
        ]

    def _feed(self, seconds: list[CvSecond]) -> list[CvSlotMessage]:
        out: list[CvSlotMessage] = []
        for sec in seconds:
            run = self._tracker.update(sec)
            self._history.append((sec, run))
            if self._on_second:
                self._on_second(sec, run)
            out += self._compressor.add_second(sec, run)
        return out
