"""리포트 문구 (SCORE-02·03, 점수 명세 5장 끝·8장).

점수 계산이 끝난 SessionResult의 집계값만으로 한두 문장을 만든다.
- LLM 문구: 집계값(태그별 분, 최장 집중, 딴짓 상위 항목)만 보낸다. 슬롯별 기록·웹캠 수치는 보내지 않는다.
- 템플릿 문구: LLM이 없거나 실패하면 로컬에서 만든다. 같은 결과면 항상 같은 문구.
문구 호출이 실패해도 llm_call_failed는 바꾸지 않는다 (점수 모드는 창 분류 성공 여부로만 정한다).
"""
from __future__ import annotations

import json
import logging
import re

from config.llm import REPORT_TEXT_MAX_CHARS, REPORT_TIMEOUT_SEC
from schemas import SessionResult
from scoring.llm_classify import JsonChat
from scoring.llm_client import LlmError

logger = logging.getLogger(__name__)

LOSS_TAGS = ("distraction", "away", "drowsy")  # 이탈 종류 (점수 명세 3장)
LOSS_KO = {"distraction": "딴짓", "away": "자리비움", "drowsy": "졸음"}

REPORT_SYSTEM_PROMPT = """너는 뽀모도로 집중 타이머 FocusPomo의 세션 리포트 문구를 쓴다.
입력은 방금 끝난 집중 세션의 집계값이다 (시간은 분 단위).
- 한국어 해요체로 1~2문장, 120자 안쪽으로 쓴다.
- 점수 숫자는 화면에 크게 나오므로 다시 말하지 않는다.
- 가장 큰 이탈 원인 하나를 짚고, 다음 세션에서 해 볼 구체적인 행동 하나를 제안한다. 이탈이 거의 없으면 짧게 칭찬한다.
- 입력에 없는 사실을 지어내지 않는다. 비난·과장·건강에 대한 판단은 하지 않는다.
- JSON 객체 하나만 출력한다: {"report_text": "..."}"""


# ── 공통 ─────────────────────────────────────────────

def format_duration(seconds: float) -> str:
    """초 → "17분 30초" / "45초" / "2분"."""
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    if minutes and sec:
        return f"{minutes}분 {sec}초"
    if minutes:
        return f"{minutes}분"
    return f"{sec}초"


def distraction_name(item: dict) -> str:
    """리포트 딴짓 항목 이름. 예: "youtube.com [Gaming]", "백그라운드 재생 [Entertainment]"."""
    category = f" [{item['video_category']}]" if item.get("video_category") else ""
    if item.get("background"):
        return f"백그라운드 재생{category}"
    return f"{item.get('domain') or item.get('app_name') or '알 수 없는 창'}{category}"


def tidy_text(text: str, max_chars: int = REPORT_TEXT_MAX_CHARS) -> str:
    """공백을 정리하고, 길면 max_chars 안의 마지막 문장 끝에서 자른다."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    ends = [m.end() for m in re.finditer(r"[.!?。](?=\s|$)|요(?=\s)", cut)]
    return cut[:ends[-1]].rstrip() if ends else cut.rstrip() + "…"


def _main_loss(result: SessionResult) -> tuple[str, int] | None:
    seconds = result["tag_seconds"]
    tag = max(LOSS_TAGS, key=lambda t: seconds.get(t, 0))  # 동률이면 LOSS_TAGS 순서 (딴짓 먼저)
    return (tag, seconds.get(tag, 0)) if seconds.get(tag, 0) > 0 else None


# ── 템플릿 (로컬) ────────────────────────────────────

def template_report_text(result: SessionResult) -> str:
    """LLM 없이 만드는 문구. 가장 큰 이탈 원인과 다음 세션 제안 하나."""
    if not result["slot_scores"]:
        return "기록된 시간이 없어 이번 세션은 분석하지 못했어요."
    focus = result["tag_seconds"].get("focus", 0)
    if result["focus_score"] is None:
        return "판정할 수 있는 시간이 부족했어요. 카메라와 창 인식 상태를 확인하고 다음 세션을 시작해 보세요."
    loss = _main_loss(result)
    if loss is None:
        return f"이탈 없이 {format_duration(focus)} 동안 집중했어요. 이 흐름을 다음 세션에도 이어 가 보세요."
    tag, seconds = loss
    first = f"{format_duration(focus)} 집중했고, 가장 큰 이탈은 {LOSS_KO[tag]}({format_duration(seconds)})이었어요."
    if tag == "distraction":
        top = result["top_distractions"][0] if result["top_distractions"] else None
        if top is not None and top.get("background"):
            advice = "다음 세션에서는 틀어 둔 영상을 끄고 시작해 보세요."
        elif top is not None:
            advice = f"다음 세션 전에 {distraction_name(top)} 창을 닫아 두면 도움이 될 거예요."
        else:
            advice = "다음 세션 전에 공부와 상관없는 창을 닫아 두면 도움이 될 거예요."
    elif tag == "drowsy":
        advice = "휴식 시간에 잠깐 일어나 몸을 움직이고 오면 다음 세션이 한결 수월할 거예요."
    else:
        advice = "필요한 물건을 세션 전에 미리 챙겨 두면 자리를 덜 비우게 돼요."
    return f"{first} {advice}"


# ── LLM ──────────────────────────────────────────────

def _minutes(seconds: float) -> float:
    return round(seconds / 60, 1)


def report_input(result: SessionResult) -> dict:
    """LLM에 보내는 집계값. 슬롯별 기록·웹캠 수치·창 제목은 넣지 않는다."""
    seconds = result["tag_seconds"]
    return {
        "focus_score": result["focus_score"],
        "score_note": result["score_note"],
        "minutes": {
            "집중": _minutes(seconds.get("focus", 0)),
            "딴짓": _minutes(seconds.get("distraction", 0)),
            "자리비움": _minutes(seconds.get("away", 0)),
            "졸음": _minutes(seconds.get("drowsy", 0)),
            "판정 제외": _minutes(seconds.get("unknown", 0) + seconds.get("no_cam", 0)),
        },
        "longest_focus_minutes": _minutes(result["longest_focus_seconds"]),
        "top_distractions": [{"name": distraction_name(d), "minutes": _minutes(d["seconds"])}
                             for d in result["top_distractions"]],
    }


def generate_report_text(result: SessionResult, client: JsonChat, *,
                         timeout: float = REPORT_TIMEOUT_SEC) -> str | None:
    """LLM 문구 1회 (다시 시도하지 않음). 실패하면 None → 호출하는 쪽이 템플릿 문구를 쓴다."""
    try:
        data = client.complete_json(REPORT_SYSTEM_PROMPT, json.dumps(report_input(result), ensure_ascii=False),
                                    timeout=timeout)
    except LlmError as e:
        logger.warning("LLM 리포트 문구 실패 — 기본 문구를 씁니다: %s", e)
        return None
    text = data.get("report_text")
    if not isinstance(text, str) or not text.strip():
        logger.warning("LLM 리포트 문구가 비어 있어 기본 문구를 씁니다")
        return None
    return tidy_text(text)
