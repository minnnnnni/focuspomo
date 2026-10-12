"""
1초 요약 → 연속 감김 초(closed_run) → 졸음 판정.

closed_run은 슬롯이 바뀌어도 초기화하지 않는다 (슬롯 경계를 넘는 졸음).
CvSlot 만들 때:
    drowsy         = 슬롯 안 초들 중 is_drowsy(run)이 하나라도 True
    closed_run_max = 슬롯 안 run의 최댓값
"""
from __future__ import annotations

from config.aggregation import DROWSY_MIN_RUN_SEC
from schemas import CvSecond


def is_drowsy(closed_run: int) -> bool:
    return closed_run >= DROWSY_MIN_RUN_SEC


class ClosedRunTracker:
    def __init__(self) -> None:
        self.run = 0
        self._last_ts: float | None = None

    def update(self, sec: CvSecond) -> int:
        # 이 1초까지 이어진 연속 감김 초 수를 반환한다.
        # 프레임이 하나도 없어 건너뛴 초가 있으면 연속이 끊긴 것으로 본다.
        if self._last_ts is not None and sec["timestamp"] - self._last_ts > 1.5:
            self.run = 0
        self.run = self.run + 1 if sec["eyes_closed"] else 0
        self._last_ts = sec["timestamp"]
        return self.run
