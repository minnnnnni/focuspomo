"""얼굴을 놓친 구간을 세션이 끝난 뒤 '어떻게 놓쳤는지'로 나눈다 (CV-06, 고개 숙임 판정).

필기하려고 고개를 숙이면 평소보다 23~29°에서 얼굴 인식이 끊긴다 (2026-11-26 측정).
얼굴만 보고 판정하면 필기가 자리비움으로 감점되므로, 얼굴이 사라지기 직전·사라진 동안·돌아올 때를 함께 본다.
구간 전체를 보고 정해야 하므로 세션 중에는 지금처럼 보내고, 세션이 끝나면 바뀐 슬롯을 다시 보낸다
(cv_module.pipeline.CvPipeline.finish, 같은 슬롯은 나중 메시지가 남는다).

구간마다 위에서부터 처음 맞는 것으로 정한다. 기준값은 config/aggregation.py (측정에서 정한 임시값).
1. left      얼굴이 평소 위치보다 STAND_UP_FACE_RISE 이상 위에서 사라짐 = 일어나 나감
2. nod       사라지기 NOD_LOOKBACK_SEC초 전~1초 전, 고개를 든 채 눈 비율이 NOD_EAR_RATIO 미만 = 졸다 고개 떨굼.
             그 전에 눈 감고 있던 초(NOD_CLOSING_MAX_SEC까지)도 nod로 바꾼다 (4초가 안 돼 졸음으로 안 잡힌 감김)
3. left      다시 잡히기 직전 SAT_DOWN_LOOKBACK_SEC초 안에 사람 영역이 SAT_DOWN_PERSON_RATIO 이상으로 커짐
             = 돌아와 앉음 (몸이 카메라 가까이 지나간다). 숙인 채 일어난 경우도 여기서 잡힌다
4. head_down 놓친 동안 사람 영역 중앙값이 PERSON_VISIBLE_RATIO 이상 = 머리·어깨가 보임 (필기 등, 자리에 있음)
5. left      그 밖인데 UNSEEN_AWAY_SEC초 이상 = 정수리도 안 보이는 채 오래 있음. 나갔거나 엎드려 조는 것이라 어차피 이탈
6. unseen    그 밖 (짧음) = 깊이 숙였다 든 것인지 잠깐 나간 것인지 구별할 수 없어 판정불가
사람 영역 값이 하나도 없으면(모델 파일 없음 등) 3~6은 건너뛰고 None으로 둔다 = 지금처럼 자리비움.

슬롯에서는 head_down·nod를 얼굴 있음으로, nod를 졸음으로, unseen을 판정불가로 센다 (cv_module.slot_compressor).
"""
from __future__ import annotations

import statistics
from collections.abc import Sequence

from config.aggregation import (
    HEAD_UPRIGHT_PITCH,
    NOD_CLOSING_MAX_SEC,
    NOD_EAR_RATIO,
    NOD_LOOKBACK_SEC,
    PERSON_VISIBLE_RATIO,
    SAT_DOWN_LOOKBACK_SEC,
    SAT_DOWN_PERSON_RATIO,
    STAND_UP_FACE_RISE,
    UNSEEN_AWAY_SEC,
)
from schemas import CvSecond, Posture

_NEXT_SEC_MAX_GAP = 1.5  # 1초 요약 시각이 이보다 벌어지면 사이에 카메라가 끊긴 것 (drowsiness.ClosedRunTracker와 같음)


def _next_to(a: CvSecond, b: CvSecond) -> bool:
    return b["timestamp"] - a["timestamp"] <= _NEXT_SEC_MAX_GAP


def _upright(sec: CvSecond) -> bool:
    # 각도를 모르면(캘리브레이션 각도 없음) 든 것으로 본다
    return sec["pitch_rel"] is None or sec["pitch_rel"] < HEAD_UPRIGHT_PITCH


def usual_face_y(seconds: Sequence[CvSecond]) -> float | None:
    """고개를 든 채 얼굴이 보이던 초의 얼굴 높이 중앙값 = 평소 위치."""
    ys = [s["face_y"] for s in seconds if s["face_present"] and s["face_y"] is not None and _upright(s)]
    return statistics.median(ys) if ys else None


def resolve_postures(seconds: Sequence[CvSecond], ear_baseline: float) -> list[Posture | None]:
    """세션의 1초 요약(시각 순) → 초마다 posture. 얼굴이 보인 초는 None (졸다 떨구기 전 눈 감은 초만 nod).

    얼굴을 놓친 초도 판단할 근거가 없으면 None (슬롯에서는 지금처럼 얼굴 없음).
    """
    n = len(seconds)
    out: list[Posture | None] = [None] * n
    base_y = usual_face_y(seconds)
    i = 0
    while i < n:
        if seconds[i]["face_present"]:
            i += 1
            continue
        j = i + 1
        while j < n and not seconds[j]["face_present"] and _next_to(seconds[j - 1], seconds[j]):
            j += 1
        before = i > 0 and seconds[i - 1]["face_present"] and _next_to(seconds[i - 1], seconds[i])
        after = j < n and seconds[j]["face_present"] and _next_to(seconds[j - 1], seconds[j])
        posture = _gap_posture(seconds, i, j, before, after, base_y, ear_baseline)
        out[i:j] = [posture] * (j - i)
        if posture == "nod":
            _mark_closing(seconds, i, out)
        i = j
    return out


def _gap_posture(seconds: Sequence[CvSecond], i: int, j: int, before: bool, after: bool,
                 base_y: float | None, ear_baseline: float) -> Posture | None:
    gap = seconds[i:j]

    # 1. 일어남: 마지막으로 본 얼굴 위치 (놓친 첫 초에 얼굴 프레임이 조금 있으면 그게 더 늦다)
    last_y = gap[0]["face_y"]
    if last_y is None and before:
        last_y = seconds[i - 1]["face_y"]
    if base_y is not None and last_y is not None and base_y - last_y >= STAND_UP_FACE_RISE:
        return "left"

    # 2. 졸다 떨굼: 직전 1초는 필기여도 눈이 먼저 내려가므로 빼고 그 앞을 본다
    if before:
        t0 = gap[0]["timestamp"]
        look = [s for s in seconds[max(0, i - NOD_LOOKBACK_SEC):i - 1]
                if s["face_present"] and s["ear_mean"] is not None and _upright(s)
                and t0 - s["timestamp"] <= NOD_LOOKBACK_SEC + 0.5]
        if look and statistics.median(s["ear_mean"] / ear_baseline for s in look) < NOD_EAR_RATIO:
            return "nod"

    persons = [s["person_ratio"] for s in gap if s["person_ratio"] is not None]
    if not persons:
        return None  # 사람 영역을 재지 못함: 예전처럼 얼굴 없음으로 둔다

    # 3. 돌아와 앉음: 얼굴이 다시 잡힌 초에도 그 직전 프레임들의 사람 영역이 들어 있다
    if after:
        t_end = gap[-1]["timestamp"]
        tail = [s for s in gap if t_end - s["timestamp"] < SAT_DOWN_LOOKBACK_SEC] + [seconds[j]]
        if any(s["person_max"] is not None and s["person_max"] >= SAT_DOWN_PERSON_RATIO for s in tail):
            return "left"

    # 4. 머리·어깨가 보임
    if statistics.median(persons) >= PERSON_VISIBLE_RATIO:
        return "head_down"

    # 5~6. 아무것도 안 보임: 오래면 자리비움, 짧으면 판정불가
    return "left" if len(gap) >= UNSEEN_AWAY_SEC else "unseen"


def _mark_closing(seconds: Sequence[CvSecond], i: int, out: list[Posture | None]) -> None:
    """졸다 떨군 구간 앞에서 눈 감고 있던 초를 거슬러 nod로 바꾼다. 떨구는 순간(직전 1초)은 눈을 떴어도 건너뛴다."""
    k = i - 1
    while k >= max(0, i - NOD_CLOSING_MAX_SEC) and seconds[k]["face_present"] and _next_to(seconds[k], seconds[k + 1]):
        if seconds[k]["eyes_closed"]:
            out[k] = "nod"
        elif k != i - 1:
            break
        k -= 1
