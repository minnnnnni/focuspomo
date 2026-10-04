"""
캘리브레이션 이후 CV 처리 전체를 하나로 묶는다.

프레임(EAR) → 1초 요약 → 연속 감김(closed_run) → 10초 슬롯 메시지

CV 스레드는 프레임마다 add_frame()을 호출하고, 돌려받은 메시지를
그대로 slot_queue.put() 하면 된다. 세션이 끝나면 finish()의 메시지까지 넣는다.
"""

from __future__ import annotations

from typing import Callable

from cv_module.drowsiness import ClosedRunTracker
from cv_module.second_summary import SecondAggregator
from cv_module.slot_compressor import SlotCompressor
from schemas import CvSecond, CvSlotMessage

SecondCallback = Callable[[CvSecond, int], None]


class CvPipeline:
    def __init__(
        self,
        session_start: float,
        ear_baseline: float,
        on_second: SecondCallback | None = None,
    ) -> None:
        """on_second(sec, closed_run): 1초 요약이 나올 때마다 호출 (디버깅·데모용)."""
        self.session_start = session_start
        self.ear_baseline = ear_baseline
        self._seconds = SecondAggregator(session_start, ear_baseline)
        self._tracker = ClosedRunTracker()
        self._compressor = SlotCompressor(session_start)
        self._on_second = on_second

    @property
    def closed_run(self) -> int:
        return self._tracker.run

    def add_frame(self, timestamp: float, ear: float | None) -> list[CvSlotMessage]:
        return self._feed(self._seconds.add_frame(timestamp, ear))

    def finish(self) -> list[CvSlotMessage]:
        # 세션 종료 시 호출. 남은 1초와 마지막 슬롯을 마무리한다.
        return self._feed(self._seconds.flush()) + self._compressor.flush()

    def _feed(self, seconds: list[CvSecond]) -> list[CvSlotMessage]:
        out: list[CvSlotMessage] = []
        for sec in seconds:
            run = self._tracker.update(sec)
            if self._on_second:
                self._on_second(sec, run)
            out += self._compressor.add_second(sec, run)
        return out
