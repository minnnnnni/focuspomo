"""LLM 창 분류 (SCORE-02, 점수 명세 5장).

세션에 나온 고유 창 목록(LlmWindowItem)을 한 번에 보내 항목마다 work / distraction / unknown을 받고,
(app_name, domain, None) → 라벨 dict(LlmLabels)로 돌려준다. 슬롯 매핑은 schemas.window_label_of.

- 보내지 않는 슬롯: app_name이 None(조회 실패), 규칙상 딴짓(rule_distraction, 이미 딴짓으로 정해짐),
  LLM에 묻지 않고 코드에서 정하는 창:
  · FocusPomo 창 자체 → 작업 (타이머를 보던 시간, 2026-11-15 팀 결정, local_labels)
  · 유튜브 영상(video_category가 있음) → 점수 모듈이 유튜브 영상 분류표로 정한다 (scoring.focus_score.window_label,
    묶음 전체 길이로 소급, 2026-11-25). 유튜브 홈·검색 화면처럼 영상 분류가 없는 youtube.com은 지금처럼 LLM에 보낸다
- 보내는 값: 항목 번호(id), 앱 이름, 도메인, 등장 슬롯 수. 웹캠 신호·창 제목·URL 전체·유튜브 카테고리는 보내지 않는다.
- 응답은 id로 항목에 맞춘다 (모델이 앱 이름의 대소문자·null 표기를 바꿔도 깨지지 않게). id가 없으면 이름으로 맞춘다.
- 정의되지 않은 라벨은 unknown (schemas.normalize_window_label), 응답에서 빠진 항목은 dict에 없어 unknown이 된다.
- 실패(키 없음, 네트워크, 형식 오류가 재시도 후에도 계속)하면 None → 점수 모듈이 cam_only/none 모드로 계산한다 (7장).
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Protocol

from config.llm import CLASSIFY_RETRIES, CLASSIFY_TIMEOUT_SEC, MAX_ITEMS, RETRY_BACKOFF_SEC
from schemas import LlmWindowItem, MergedSlot, WindowLabel, normalize_window_label
from scoring.focus_score import LlmLabels, WindowKey
from scoring.llm_client import LlmError, call_with_retries
from window_tracker.foreground import SELF_APP_NAME

logger = logging.getLogger(__name__)

SELF_WINDOW_LABEL: WindowLabel = "work"  # FocusPomo 창 자체 (타이머·리포트를 보는 시간)은 작업

SYSTEM_PROMPT = """너는 뽀모도로 집중 타이머 FocusPomo의 창 분류기다.
사용자는 25분 집중 세션 동안 공부·과제·코딩을 하는 대학생이다. 세션 동안 앞 화면에 있던 창 목록을 받는다.
각 항목은 앱 이름(app_name), 브라우저면 사이트 도메인(domain), 등장 횟수(slots, 10초 단위)다.

각 항목을 다음 중 하나로 분류한다.
- "work": 공부·과제·개발에 쓰는 도구나 자료. 예: 코드 편집기, 터미널, 문서·스프레드시트·발표 도구, PDF 뷰어, 노트 앱, 검색 엔진, 개발 문서, 깃허브, 스택오버플로, 강의·학습 사이트, 학교 LMS, 사전·번역기, AI 도우미
- "distraction": 공부와 무관한 오락·쇼핑·잡담. 예: 게임, 쇼핑몰, 연예·스포츠, 웹툰·웹소설, OTT, SNS, 유머 커뮤니티
- "unknown": 이름만으로 판단할 수 없거나 공부와 오락에 모두 쓰는 것. 예: 메신저(카카오톡·디스코드·슬랙), 파일 탐색기, 설정, 음악 앱, 포털 첫 화면, 뉴스, 도메인이 없는 브라우저(어떤 사이트인지 모름), youtube.com(유튜브 홈·검색 화면 — 영상은 따로 분류하므로 목록에 오지 않는다)

규칙:
- 확신이 없으면 "unknown"을 고른다. 공부 중인 창을 딴짓으로 잘못 고르는 것이 가장 나쁘다.
- 입력의 모든 항목에 대해 id를 그대로 돌려준다. 항목을 빼거나 새로 만들지 않는다.
- JSON 객체 하나만 출력한다. 설명이나 코드 블록 없이 다음 형식으로:
{"item_labels": [{"id": 0, "window_label": "work"}, {"id": 1, "window_label": "unknown"}]}"""


class JsonChat(Protocol):
    def complete_json(self, system: str, user: str, *, timeout: float) -> dict: ...


def item_key(item: LlmWindowItem) -> WindowKey:
    """LLM 항목 → 라벨 dict 키. 유튜브 영상은 보내지 않으므로 video_category 자리는 항상 None이다."""
    return (item["app_name"], item["domain"], None)


def local_label(app_name: str | None, domain: str | None, video_category: str | None) -> WindowLabel | None:
    """LLM에 묻지 않고 라벨 dict에 넣는 창 라벨. 지금은 FocusPomo 창 자체 → 작업 (11-15). 그 외 None."""
    return SELF_WINDOW_LABEL if app_name == SELF_APP_NAME else None


def is_youtube_video(slot: MergedSlot) -> bool:
    """유튜브 영상 슬롯 — 슬롯마다 묶음 길이로 라벨이 달라 dict 대신 점수 모듈이 분류표로 정한다."""
    return slot["video_category"] is not None


def _needs_llm(slot: MergedSlot) -> bool:
    return (slot["app_name"] is not None and slot["rule_distraction"] is None and not is_youtube_video(slot)
            and local_label(slot["app_name"], slot["domain"], slot["video_category"]) is None)


def build_items(slots: list[MergedSlot], *, max_items: int = MAX_ITEMS) -> list[LlmWindowItem]:
    """정지 슬롯을 뺀 슬롯 → LLM에 보낼 고유 창 목록. 등장 슬롯이 많은 순, 같으면 먼저 나온 순.

    조회 실패·규칙상 딴짓·코드에서 정하는 창(FocusPomo, 유튜브 영상)은 보내지 않는다.
    max_items를 넘는 항목(드물게 잠깐 지나간 창)은 보내지 않아 unknown이 된다.
    """
    counts: dict[tuple[str, str | None], int] = {}
    for slot in sorted(slots, key=lambda s: s["slot"]):
        if not _needs_llm(slot):
            continue
        key = (slot["app_name"], slot["domain"])
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])[:max_items]  # 정렬은 안정적이라 동률이면 먼저 나온 순
    return [{"id": i, "app_name": app, "domain": domain, "slots": n}
            for i, ((app, domain), n) in enumerate(ordered)]


def local_labels(slots: list[MergedSlot]) -> LlmLabels:
    """LLM에 묻지 않고 로컬에서 정하는 창 라벨 (local_label). 규칙상 딴짓 슬롯은 어차피 딴짓이라 뺀다."""
    labels: LlmLabels = {}
    for s in slots:
        if s["app_name"] is None or s["rule_distraction"] is not None:
            continue
        label = local_label(s["app_name"], s["domain"], s["video_category"])
        if label is not None:
            labels[(s["app_name"], s["domain"], s["video_category"])] = label
    return labels


def build_user_message(items: list[LlmWindowItem]) -> str:
    return json.dumps({"items": items}, ensure_ascii=False)


def _entry_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def parse_labels(data: dict, items: list[LlmWindowItem]) -> LlmLabels:
    """LLM 응답(JSON) → {(app_name, domain, None): 라벨}.

    항목을 하나도 맞추지 못하면(형식이 완전히 다름) LlmError — 다시 시도한다.
    """
    raw = data.get("item_labels")
    if not isinstance(raw, list):
        raise LlmError("응답에 item_labels 목록이 없습니다")
    by_id = {item["id"]: item for item in items}
    by_key = {item_key(item): item for item in items}
    labels: LlmLabels = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        entry_id = _entry_id(entry.get("id"))
        item = by_id.get(entry_id) if entry_id is not None else \
            by_key.get((entry.get("app_name"), entry.get("domain"), None))
        if item is not None:
            labels[item_key(item)] = normalize_window_label(entry.get("window_label"))
    if items and not labels:
        raise LlmError("응답에서 입력 항목을 하나도 찾지 못했습니다")
    missing = len(items) - len(labels)
    if missing:
        logger.info("LLM 응답에서 빠진 창 %d개는 미분류로 둡니다", missing)
    return labels


def classify_windows(items: list[LlmWindowItem], client: JsonChat | None, *,
                     timeout: float = CLASSIFY_TIMEOUT_SEC, retries: int = CLASSIFY_RETRIES,
                     backoff: float = RETRY_BACKOFF_SEC,
                     sleep: Callable[[float], None] = time.sleep) -> LlmLabels | None:
    """창 목록 분류. 보낼 항목이 없으면 호출하지 않고 {} (성공), 실패하면 None (llm_call_failed)."""
    if not items:
        return {}
    if client is None:
        return None
    user = build_user_message(items)
    try:
        return call_with_retries(lambda: parse_labels(client.complete_json(SYSTEM_PROMPT, user, timeout=timeout), items),
                                 retries=retries, backoff=backoff, sleep=sleep)
    except LlmError as e:
        logger.warning("LLM 창 분류 실패 — 웹캠 신호와 기본 딴짓 규칙만으로 계산합니다: %s", e)
        return None
