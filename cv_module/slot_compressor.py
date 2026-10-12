"""
1초 요약(CvSecond) 10개 → 10초 슬롯(CvSlot) + GUI 큐 메시지(CvSlotMessage).

슬롯 필드 계산:
- face_present  : 받은 1초 요약 중 얼굴 있던 초의 비율 >= FACE_PRESENT_MIN_RATIO
- drowsy        : 슬롯 안에 closed_run >= DROWSY_MIN_RUN_SEC 인 초가 있었음
- closed_run_max: 슬롯 안 closed_run 최댓값 (앞 슬롯에서 이어진 값 포함)
- blink_count   : 1초 요약 합 (슬롯당 캡은 점수 단계에서 적용)
- ear_mean      : 1초 요약 ear_mean 평균 (값이 하나도 없으면 None)

1초 요약을 하나도 받지 못한 슬롯(카메라 정지 등)은 메시지를 보내지 않는다.
그 슬롯은 GUI가 세션 종료 시 schemas.empty_cv_part()로 채운다.
"""

from __future__ import annotations

from config.aggregation import FACE_PRESENT_MIN_RATIO
from cv_module.drowsiness import is_drowsy
from schemas import CvSecond, CvSlot, CvSlotMessage, slot_index, slot_start


class SlotCompressor:
    def __init__(self, session_start: float) -> None:
        self.session_start = session_start
        self._slot: int | None = None
        self._seconds: list[CvSecond] = []
        self._runs: list[int] = []

    def add_second(self, sec: CvSecond, closed_run: int) -> list[CvSlotMessage]:
        # 1초 요약과 그 시점의 closed_run을 넣는다. 슬롯이 끝나면 메시지를 반환.
        out: list[CvSlotMessage] = []
        slot = slot_index(sec["timestamp"], self.session_start)
        if self._slot is not None and slot != self._slot:
            out.append(self._build())
        if self._slot is None or slot != self._slot:
            self._slot, self._seconds, self._runs = slot, [], []
        self._seconds.append(sec)
        self._runs.append(closed_run)
        return out

    def flush(self) -> list[CvSlotMessage]:
        # 세션 종료 시 마지막(덜 찬) 슬롯을 내보낸다.
        if self._slot is None or not self._seconds:
            return []
        msg = self._build()
        self._slot, self._seconds, self._runs = None, [], []
        return [msg]

    def _build(self) -> CvSlotMessage:
        secs, runs = self._seconds, self._runs
        face_secs = sum(1 for s in secs if s["face_present"])
        ears = [s["ear_mean"] for s in secs if s["ear_mean"] is not None]

        slot = CvSlot(
            slot=self._slot,
            timestamp=slot_start(self._slot, self.session_start),
            face_present=(face_secs / len(secs) >= FACE_PRESENT_MIN_RATIO) if secs else None,
            drowsy=any(is_drowsy(r) for r in runs),
            closed_run_max=max(runs, default=0),
            blink_count=sum(s["blink_count"] for s in secs),
            ear_mean=(sum(ears) / len(ears)) if ears else None,
            gaze_off_screen=None,
        )
        return CvSlotMessage(source="cv", slot=slot, seconds=list(secs))
