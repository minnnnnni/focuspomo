"""
1초 요약(CvSecond) 10개 → 10초 슬롯(CvSlot) + GUI 큐 메시지(CvSlotMessage).

슬롯 필드 계산:
- face_present  : 받은 1초 요약 중 얼굴 있던 초의 비율 >= FACE_PRESENT_MIN_RATIO
                  posture(v6, 세션 종료 후 cv_module.head_down이 채움)가 있으면
                  head_down·nod 초는 얼굴 있음, unseen 초는 판정에서 뺀다.
                  unseen이 절반을 넘으면 None (판정불가 → 점수 no_cam)
- drowsy        : 슬롯 안에 closed_run >= DROWSY_MIN_RUN_SEC 인 초가 있었거나, posture가 nod인 초가 있었음
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

PRESENT_POSTURES = ("head_down", "nod")  # 얼굴은 놓쳤지만 자리에 있는 것으로 보는 판정


def build_slot(slot: int, session_start: float, secs: list[CvSecond], runs: list[int]) -> CvSlotMessage:
    """1초 요약들(과 그때의 closed_run) → 슬롯 메시지. 세션 종료 후 소급해 다시 만들 때도 쓴다."""
    unseen = sum(1 for s in secs if s.get("posture") == "unseen")
    present = sum(1 for s in secs if s["face_present"] or s.get("posture") in PRESENT_POSTURES)
    judged = len(secs) - unseen
    if not secs or unseen * 2 > len(secs):
        face_present = None
    else:
        face_present = present / judged >= FACE_PRESENT_MIN_RATIO
    ears = [s["ear_mean"] for s in secs if s["ear_mean"] is not None]

    cv_slot = CvSlot(
        slot=slot,
        timestamp=slot_start(slot, session_start),
        face_present=face_present,
        drowsy=any(is_drowsy(r) for r in runs) or any(s.get("posture") == "nod" for s in secs),
        closed_run_max=max(runs, default=0),
        blink_count=sum(s["blink_count"] for s in secs),
        ear_mean=(sum(ears) / len(ears)) if ears else None,
        gaze_off_screen=None,
    )
    return CvSlotMessage(source="cv", slot=cv_slot, seconds=list(secs))


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
        return build_slot(self._slot, self.session_start, self._seconds, self._runs)
