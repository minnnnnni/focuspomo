"""CvSlotMessage mock 생성기 (CV 스레드 대역, 2주차에 실제 cv_module로 교체).

슬롯 하나를 1초 단위 패턴 문자열로 기술한다. 문자 하나 = 1초.

    "P"  얼굴 있음, 눈 뜸, 깜빡임 0
    "B"  얼굴 있음, 눈 뜸, 깜빡임 1
    "C"  얼굴 있음, 눈 감김 (eyes_closed=True)
    "A"  얼굴 없음 (자리 비움)
    "-"  1초 요약 누락 (CvSecond를 만들지 않음, closed_run을 끊음)

패턴 길이가 10보다 짧으면 "마지막 슬롯 10초 미만"(중간 종료 flush)이다.
압축은 개발계획서 2.6 규칙을 그대로 따른다 (closed_run은 슬롯을 넘어 이어 센다).
"""
from __future__ import annotations

import random

from config.aggregation import DROWSY_MIN_RUN_SEC, FACE_PRESENT_MIN_RATIO
from schemas import SLOT_SECONDS, CvSecond, CvSlot, CvSlotMessage, slot_start

DEFAULT_EAR_BASELINE = 0.30
CLOSED_EAR_RATIO = 0.3  # 감김 초의 ear_mean = baseline * 이 값

FULL_FOCUS = "P" * SLOT_SECONDS
FULL_AWAY = "A" * SLOT_SECONDS
FULL_CLOSED = "C" * SLOT_SECONDS

_VALID_CHARS = set("PBCA-")


def make_cv_seconds(
    slot: int,
    session_start: float,
    pattern: str,
    ear_baseline: float = DEFAULT_EAR_BASELINE,
) -> list[CvSecond | None]:
    """패턴을 1초 요약 리스트로 바꾼다. "-" 자리는 None (누락)."""
    if len(pattern) > SLOT_SECONDS or not set(pattern) <= _VALID_CHARS:
        raise ValueError(f"invalid cv pattern: {pattern!r}")
    base = slot_start(slot, session_start)
    seconds: list[CvSecond | None] = []
    for i, ch in enumerate(pattern):
        if ch == "-":
            seconds.append(None)
            continue
        present = ch != "A"
        closed = ch == "C"
        if not present:
            ear = None
        elif closed:
            ear = round(ear_baseline * CLOSED_EAR_RATIO, 4)
        else:
            ear = ear_baseline
        seconds.append({
            "timestamp": base + i,
            "face_present": present,
            "eyes_closed": closed,
            "blink_count": 1 if ch == "B" else 0,
            "ear_mean": ear,
            "gaze_off_screen": None,
        })
    return seconds


def compress_slot(
    slot: int,
    session_start: float,
    seconds: list[CvSecond | None],
    closed_run: int = 0,
) -> tuple[CvSlot, int]:
    """1초 요약들을 CvSlot으로 압축한다. (CvSlot, 다음 슬롯으로 넘길 closed_run)을 반환.

    실제 압축기(slot_compressor)의 대역이며, 개발계획서 2.6 규칙만 구현한다.
    """
    present = [s for s in seconds if s is not None]
    run_max = 0
    for s in seconds:
        if s is not None and s["face_present"] and s["eyes_closed"]:
            closed_run += 1
        else:
            closed_run = 0
        run_max = max(run_max, closed_run)

    if present:
        ratio = sum(s["face_present"] for s in present) / len(present)
        face_present: bool | None = ratio >= FACE_PRESENT_MIN_RATIO
    else:
        face_present = None
    ears = [s["ear_mean"] for s in present if s["ear_mean"] is not None]

    cv_slot: CvSlot = {
        "slot": slot,
        "timestamp": slot_start(slot, session_start),
        "face_present": face_present,
        "drowsy": face_present is True and run_max >= DROWSY_MIN_RUN_SEC,
        "closed_run_max": run_max,
        "blink_count": sum(s["blink_count"] for s in present),
        "ear_mean": sum(ears) / len(ears) if ears else None,
        "gaze_off_screen": None,
    }
    return cv_slot, closed_run


def make_cv_messages(
    session_start: float,
    patterns: list[str],
    ear_baseline: float = DEFAULT_EAR_BASELINE,
) -> list[CvSlotMessage]:
    """슬롯 0부터 순서대로 CvSlotMessage를 만든다 (closed_run을 슬롯 간에 이어 셈)."""
    messages: list[CvSlotMessage] = []
    closed_run = 0
    for slot, pattern in enumerate(patterns):
        raw = make_cv_seconds(slot, session_start, pattern, ear_baseline)
        cv_slot, closed_run = compress_slot(slot, session_start, raw, closed_run)
        messages.append({
            "source": "cv",
            "slot": cv_slot,
            "seconds": [s for s in raw if s is not None],
        })
    return messages


def random_cv_patterns(n_slots: int, rng: random.Random) -> list[str]:
    """긴 세션용 무작위 패턴. 대부분 집중, 가끔 자리 비움·졸음·요약 누락."""
    patterns: list[str] = []
    for _ in range(n_slots):
        roll = rng.random()
        if roll < 0.75:
            chars = ["B" if rng.random() < 0.3 else "P" for _ in range(SLOT_SECONDS)]
        elif roll < 0.85:
            chars = list(FULL_AWAY)
        elif roll < 0.95:
            start = rng.randrange(SLOT_SECONDS)
            chars = ["C" if i >= start else "P" for i in range(SLOT_SECONDS)]
        else:
            chars = ["-" if rng.random() < 0.5 else "P" for _ in range(SLOT_SECONDS)]
        patterns.append("".join(chars))
    return patterns
