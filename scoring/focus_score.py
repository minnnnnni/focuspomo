"""집중도 점수 계산 (SCORE-01, 집중도 점수 산출 명세 3·6·7·8장).

정지 슬롯을 뺀 MergedSlot 목록과 LLM 창 라벨을 받아 SessionResult(v4)를 만든다.
LLM 호출은 하지 않는다. 창 라벨(llm_labels)과 리포트 문구(report_text)는 호출하는 쪽이 넘긴다
(SCORE-02·03, 지금은 mocks.mock_scoring.MockScorer).

처리 순서
1. 슬롯 길이: 슬롯 시작 ~ min(시작 + SLOT_SECONDS, session_end). 시간은 슬롯 개수가 아니라 초로 센다 (6장)
2. 점수 모드: 웹캠 측정 시간 비율과 LLM 성공 여부로 full / cam_only / window_only / none (7장)
3. 창 라벨: 규칙상 딴짓 > LLM 라벨 > unknown (schemas.window_label_of, 3장)
4. 태그: 우선순위 규칙 (schemas.raw_tag, 3장). 유예 보정은 하지 않으므로 final_tag = raw_tag (4장)
5. 점수: 판정 시간 중 집중 시간 비율, 사사오입. 판정 데이터 부족이면 None (6장)
6. 집계: tag_seconds, judged_seconds, top_distractions, longest_focus_seconds, blinks_per_minute (8장)

같은 슬롯·라벨이면 항상 같은 결과가 나온다. 입력은 바꾸지 않는다.
"""
from __future__ import annotations

import math

from config.scoring import (
    BLINK_RATE_MIN_SEC,
    CAM_USABLE_RATIO,
    MIN_JUDGED_RATIO,
    MIN_JUDGED_SEC,
    TOP_DISTRACTIONS_N,
)
from schemas import (
    FINAL_TAGS,
    JUDGED_TAGS,
    SCHEMA_VERSION,
    SLOT_SECONDS,
    Calibration,
    Distraction,
    FinalTag,
    MergedSlot,
    PauseInterval,
    ScoreMode,
    SessionResult,
    SlotScore,
    WindowLabel,
    raw_tag,
    slot_start,
    window_label_of,
)

WindowKey = tuple[str, str | None, str | None]  # (app_name, domain, video_category), LLM 분류 단위 (5장)
LlmLabels = dict[WindowKey, WindowLabel]

INSUFFICIENT_NOTE = "판정 데이터 부족"
SCORE_NOTES: dict[str, str | None] = {
    "full": None,
    "cam_only": "웹캠 신호와 기본 딴짓 규칙만으로 계산된 점수입니다",
    "window_only": "카메라 없이 창 사용만으로 계산된 점수입니다",
    "none": "카메라와 창 분류를 모두 쓸 수 없어 점수를 계산하지 않았습니다",
}


def round_half_up(value: float) -> int:
    """사사오입 (6장). Python round()는 78.5 → 78(은행원 반올림)이라 쓰지 않는다."""
    return math.floor(value + 0.5)


def slot_durations(slots: list[MergedSlot], session_start: float, session_end: float) -> list[tuple[float, float]]:
    """슬롯마다 (start, end). 마지막 슬롯은 session_end에서 끝나 짧을 수 있다."""
    spans = []
    for s in slots:
        start = slot_start(s["slot"], session_start)
        spans.append((start, max(min(start + SLOT_SECONDS, session_end), start)))
    return spans


def _ratio(part: float, total: float) -> float:
    """part / total (total이 0이면 0).

    비율 기준과는 나눗셈 결과로 비교한다. 0.3 * 300 = 90.00000000000001이라
    `part < RATIO * total`로 쓰면 정확히 30%인 세션이 "30% 미만"으로 걸린다.
    """
    return part / total if total > 0 else 0.0


def choose_mode(slots: list[MergedSlot], durations: list[float], llm_ok: bool) -> ScoreMode:
    """7장: face_present가 측정된 시간이 전체의 CAM_USABLE_RATIO 이상이면 웹캠 사용 가능."""
    total = sum(durations)
    cam_sec = sum(d for s, d in zip(slots, durations) if s["face_present"] is not None)
    cam_ok = total > 0 and _ratio(cam_sec, total) >= CAM_USABLE_RATIO
    if cam_ok:
        return "full" if llm_ok else "cam_only"
    return "window_only" if llm_ok else "none"


def focus_score(durations: list[float], final_tags: list[FinalTag]) -> int | None:
    """6장: 판정 시간 중 집중 시간 비율(0~100). 판정 데이터 부족이면 None."""
    total_sec = sum(durations)
    judged_sec = sum(d for d, t in zip(durations, final_tags) if t in JUDGED_TAGS)
    if judged_sec < MIN_JUDGED_SEC or _ratio(judged_sec, total_sec) < MIN_JUDGED_RATIO:
        return None
    focus_sec = sum(d for d, t in zip(durations, final_tags) if t == "focus")
    return round_half_up(100 * focus_sec / judged_sec)


def score_note(mode: ScoreMode, score: int | None) -> str | None:
    """7장: 판정 데이터 부족 안내가 모드 안내보다 우선한다. none 모드는 항상 none 안내."""
    if mode == "none":
        return SCORE_NOTES["none"]
    if score is None:
        return INSUFFICIENT_NOTE
    return SCORE_NOTES[mode]


def tag_seconds(durations: list[float], final_tags: list[FinalTag]) -> dict[str, float]:
    """final_tag별 누적 초 (반올림 전). FINAL_TAGS 키를 모두 둔다."""
    totals = {tag: 0.0 for tag in FINAL_TAGS}
    for tag, d in zip(final_tags, durations):
        totals[tag] += d
    return totals


def top_distractions(scores: list[SlotScore], slots: list[MergedSlot], durations: list[float]) -> list[Distraction]:
    """8장: final_tag == distraction 시간을 창별로 합해 상위 TOP_DISTRACTIONS_N개.

    백그라운드 재생 딴짓은 앞 화면 창(작업)이 아니라 (None, None, background_category, True)로 묶는다.
    시간이 같으면 먼저 나온 항목이 앞에 온다.
    """
    totals: dict[tuple, float] = {}
    for score, slot, d in zip(scores, slots, durations):
        if score["final_tag"] != "distraction":
            continue
        if score["rule_distraction"] == "background":
            key = (None, None, slot["background_category"], True)
        else:
            key = (score["app_name"], score["domain"], score["video_category"], False)
        totals[key] = totals.get(key, 0.0) + d
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:TOP_DISTRACTIONS_N]
    return [{"app_name": app, "domain": domain, "video_category": category, "background": background,
             "seconds": round_half_up(sec)}
            for (app, domain, category, background), sec in ranked]


def longest_focus(scores: list[SlotScore], durations: list[float]) -> float:
    """8장: slot 번호가 이어지는 final_tag == focus 구간의 최장 시간. 정지로 번호가 끊기면 구간도 끊긴다."""
    best = run = 0.0
    prev: int | None = None
    for score, d in zip(scores, durations):
        if score["final_tag"] != "focus":
            run = 0.0
        elif run > 0 and prev is not None and score["slot"] == prev + 1:
            run += d
        else:
            run = d
        best = max(best, run)
        prev = score["slot"]
    return best


def blinks_per_minute(slots: list[MergedSlot], durations: list[float]) -> float | None:
    """8장: 얼굴이 있던 슬롯의 깜빡임 수 ÷ 그 시간(분). 그 시간이 BLINK_RATE_MIN_SEC 미만이면 None."""
    face_sec = sum(d for s, d in zip(slots, durations) if s["face_present"] is True)
    if face_sec < BLINK_RATE_MIN_SEC:
        return None
    blinks = sum(s["blink_count"] for s in slots if s["face_present"] is True)
    return round(blinks / (face_sec / 60), 2)


def score_session(
    slots: list[MergedSlot],
    calibration: Calibration,
    pause_intervals: list[PauseInterval],
    *,
    session_id: str,
    session_start: float,
    session_end: float,
    llm_labels: LlmLabels | None,
    report_text: str,
) -> SessionResult:
    """세션 점수 결과를 만든다.

    slots: 정지 구간과 겹친 슬롯을 뺀 슬롯 (core.session_end.slots_for_scoring). slot 번호는 중간이 빌 수 있다.
    llm_labels: LLM 창 분류 결과. None이면 LLM 실패(llm_call_failed=True)로 보고 cam_only/none 모드가 된다.
        보낼 창이 없어 호출하지 않았으면 {}를 넘긴다 (성공으로 본다, 7장).
    report_text: 리포트 문구 (LLM 문구 또는 폴백 문구). 점수 계산에는 쓰지 않는다.
    """
    ordered = sorted(slots, key=lambda s: s["slot"])
    spans = slot_durations(ordered, session_start, session_end)
    durations = [end - start for start, end in spans]

    llm_ok = llm_labels is not None
    labels_by_window = llm_labels or {}
    mode = choose_mode(ordered, durations, llm_ok)
    labels = [window_label_of(s, labels_by_window) for s in ordered]
    raw = [raw_tag(s, label, mode) for s, label in zip(ordered, labels)]
    final = list(raw)  # 유예 보정 없음 (4장)

    slot_scores: list[SlotScore] = [{
        "slot": s["slot"],
        "start": start,
        "end": end,
        "app_name": s["app_name"],
        "domain": s["domain"],
        "video_category": s["video_category"],
        "rule_distraction": s["rule_distraction"],
        "window_label": label,
        "raw_tag": r,
        "final_tag": f,
        "blink_count": s["blink_count"],
    } for s, (start, end), label, r, f in zip(ordered, spans, labels, raw, final)]

    score = None if mode == "none" else focus_score(durations, final)
    seconds = tag_seconds(durations, final)

    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "session_start": session_start,
        "session_end": session_end,
        "ear_baseline": calibration["ear_baseline"],
        "pause_intervals": [p.copy() for p in pause_intervals],
        "slot_scores": slot_scores,
        "score_mode": mode,
        "focus_score": score,
        "score_note": score_note(mode, score),
        "tag_seconds": {tag: round_half_up(sec) for tag, sec in seconds.items()},
        "judged_seconds": round_half_up(sum(seconds[t] for t in JUDGED_TAGS)),
        "top_distractions": top_distractions(slot_scores, ordered, durations),
        "longest_focus_seconds": round_half_up(longest_focus(slot_scores, durations)),
        "blinks_per_minute": blinks_per_minute(ordered, durations),
        "report_text": report_text,
        "llm_call_failed": not llm_ok,
    }
