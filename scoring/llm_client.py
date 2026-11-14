"""OpenAI 호환 Chat Completions 클라이언트 (SCORE-02·03). 표준 라이브러리(urllib)만 쓴다.

Gemini·Groq 모두 같은 형식이라 config/llm.py의 PROVIDERS에서 base_url·모델만 바꾼다.
HTTP 함수(post_json)를 주입받으므로 테스트는 실제 API를 부르지 않는다.

오류 구분
- LlmError: 다시 시도할 만한 실패 (연결·타임아웃, 429, 5xx, 응답 형식 오류)
- LlmConfigError(LlmError): 다시 시도해도 같은 실패 (키 오류 401/403, 모델 없음 404, 잘못된 요청 400)
API 키는 헤더로만 보내고 로그·예외 메시지에 넣지 않는다.

연결 확인 (레포 루트, venv 활성화, .env에 키를 넣은 뒤):
    python -m scoring.llm_client --check                 # 샘플 창 목록 분류 + 리포트 문구 1회
    python -m scoring.llm_client --check --provider groq
    python -m scoring.llm_client --list-models           # 이 키로 쓸 수 있는 모델 ID
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from config.env import load_env_value
from config.llm import DEFAULT_PROVIDER, PROVIDERS, RETRY_BACKOFF_SEC, TEMPERATURE, ProviderConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")
PostJson = Callable[[str, dict[str, str], dict, float], dict]  # (url, headers, body, timeout) → 응답 JSON


class LlmError(Exception):
    """LLM 호출 실패 (다시 시도할 만함)."""


class LlmConfigError(LlmError):
    """설정 문제로 인한 실패 (키·모델·요청 형식). 다시 시도하지 않는다."""


_RETRYABLE_HTTP = {408, 409, 429}


def _http_json(request: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        if e.code in _RETRYABLE_HTTP or e.code >= 500:
            raise LlmError(f"HTTP {e.code}: {body}") from None
        raise LlmConfigError(f"HTTP {e.code}: {body}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LlmError(f"연결 실패: {e}") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise LlmError("응답 본문이 JSON이 아닙니다") from None


def http_post_json(url: str, headers: dict[str, str], body: dict, timeout: float) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={**headers, "Content-Type": "application/json"})
    return _http_json(request, timeout)


def http_get_json(url: str, headers: dict[str, str], timeout: float) -> dict:
    return _http_json(urllib.request.Request(url, headers=headers), timeout)


def parse_json_object(text: str | None) -> dict:
    """모델 출력 → JSON 객체. 코드 블록(```json)이나 앞뒤 설명이 붙어도 첫 { ~ 마지막 }을 읽는다."""
    if not text or not text.strip():
        raise LlmError("응답 내용이 비어 있습니다")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LlmError("응답에 JSON 객체가 없습니다")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        raise LlmError("응답 JSON을 읽지 못했습니다 (잘렸거나 형식 오류)") from None
    if not isinstance(value, dict):
        raise LlmError("응답이 JSON 객체가 아닙니다")
    return value


def _message_content(response: dict) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LlmError("응답에 choices[0].message.content가 없습니다") from None
    if isinstance(content, list):  # 일부 제공사는 [{type: text, text: ...}] 형태
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        raise LlmError("응답 내용이 문자열이 아닙니다")
    return content


def call_with_retries(fn: Callable[[], T], *, retries: int, backoff: float = RETRY_BACKOFF_SEC,
                      sleep: Callable[[float], None] = time.sleep) -> T:
    """fn을 부르고 LlmError면 retries번까지 다시 부른다. LlmConfigError는 바로 올린다."""
    for attempt in range(retries + 1):
        try:
            return fn()
        except LlmConfigError:
            raise
        except LlmError as e:
            if attempt >= retries:
                raise
            logger.info("LLM 호출 실패, %.0f초 뒤 다시 시도 (%d/%d): %s", backoff, attempt + 1, retries, e)
            sleep(backoff)
    raise AssertionError("unreachable")


class ChatClient:
    """한 제공사·모델에 대한 Chat Completions 호출."""

    def __init__(self, provider: ProviderConfig, api_key: str, *, model: str | None = None,
                 post_json: PostJson = http_post_json) -> None:
        if not api_key:
            raise LlmConfigError(f"{provider.key_env}가 비어 있습니다")
        self.provider = provider
        self.model = model or provider.model
        self._api_key = api_key
        self._post = post_json
        self.request_count = 0

    def __repr__(self) -> str:  # 키가 로그에 찍히지 않게
        return f"ChatClient(provider={self.provider.name!r}, model={self.model!r})"

    @classmethod
    def from_env(cls, provider_name: str | None = None, *, env_path: Path | None = None,
                 post_json: PostJson = http_post_json) -> ChatClient | None:
        """.env(또는 환경변수)의 LLM_PROVIDER·키·LLM_MODEL로 만든다. 키가 없으면 None (LLM 없이 동작)."""
        name = (provider_name or load_env_value("LLM_PROVIDER", env_path) or DEFAULT_PROVIDER).lower()
        provider = PROVIDERS.get(name)
        if provider is None:
            logger.warning("알 수 없는 LLM_PROVIDER=%r — %s로 대신합니다 (가능: %s)",
                           name, DEFAULT_PROVIDER, ", ".join(PROVIDERS))
            provider = PROVIDERS[DEFAULT_PROVIDER]
        key = load_env_value(provider.key_env, env_path)
        if not key:
            return None
        return cls(provider, key, model=load_env_value("LLM_MODEL", env_path), post_json=post_json)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def complete(self, system: str, user: str, *, timeout: float) -> str:
        """system·user 메시지 하나씩 보내고 응답 텍스트를 돌려준다."""
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": TEMPERATURE,
            **self.provider.extra,
        }
        if self.provider.json_mode:
            body["response_format"] = {"type": "json_object"}
        self.request_count += 1
        return _message_content(self._post(self.provider.base_url + "chat/completions", self._headers(),
                                           body, timeout))

    def complete_json(self, system: str, user: str, *, timeout: float) -> dict:
        return parse_json_object(self.complete(system, user, timeout=timeout))

    def list_models(self, *, timeout: float = 10.0,
                    get_json: Callable[[str, dict[str, str], float], dict] = http_get_json) -> list[str]:
        data = get_json(self.provider.base_url + "models", self._headers(), timeout)
        return sorted(str(m.get("id", "")).removeprefix("models/") for m in data.get("data", []) if isinstance(m, dict))


# ── 확인용 실행 ──────────────────────────────────────

SAMPLE_SLOTS = [  # (app_name, domain, video_category, 슬롯 수) — 실제로 보낼 형식 확인용
    ("VS Code", None, None, 60),
    ("Chrome", "stackoverflow.com", None, 12),
    ("Chrome", "docs.python.org", None, 10),
    ("Chrome", "youtube.com", "Education", 8),
    ("Chrome", "youtube.com", "Music", 6),
    ("KakaoTalk", None, None, 5),
    ("Chrome", "reddit.com", None, 4),
    ("Chrome", "namu.wiki", None, 3),
]


def _check(provider_name: str | None) -> int:
    from scoring.llm_classify import build_user_message, classify_windows
    from scoring.report_text import generate_report_text

    client = ChatClient.from_env(provider_name)
    if client is None:
        name = provider_name or load_env_value("LLM_PROVIDER") or DEFAULT_PROVIDER
        key_env = PROVIDERS.get(name, PROVIDERS[DEFAULT_PROVIDER]).key_env
        print(f"API 키가 없습니다. 레포 루트 .env에 {key_env}=... 를 넣어 주세요 (.env.example 참고).")
        return 1
    print(f"{client!r}")
    items = [{"id": i, "app_name": a, "domain": d, "video_category": c, "slots": n}
             for i, (a, d, c, n) in enumerate(SAMPLE_SLOTS)]
    print("보내는 창 목록:", build_user_message(items))
    started = time.monotonic()
    labels = classify_windows(items, client)  # type: ignore[arg-type]
    elapsed = time.monotonic() - started
    if labels is None:
        print(f"창 분류 실패 ({elapsed:.1f}초) — 위 경고 메시지를 확인하세요. 모델 ID는 --list-models로 확인합니다.")
        return 1
    print(f"창 분류 {elapsed:.1f}초:")
    for item in items:
        key = (item["app_name"], item["domain"], item["video_category"])
        print(f"  {labels.get(key, 'unknown (응답에 없음)'):<12} {item['app_name']} {item['domain'] or ''} "
              f"{item['video_category'] or ''}")
    sample_result = {
        "focus_score": 78, "score_note": None, "score_mode": "full",
        "tag_seconds": {"focus": 1050, "distraction": 150, "away": 90, "drowsy": 60, "unknown": 150, "no_cam": 0},
        "judged_seconds": 1350, "longest_focus_seconds": 550,
        "top_distractions": [{"app_name": "Chrome", "domain": "youtube.com", "video_category": "Gaming",
                              "background": False, "seconds": 150}],
        "slot_scores": [{}] * 150,
    }
    started = time.monotonic()
    text = generate_report_text(sample_result, client)  # type: ignore[arg-type]
    print(f"리포트 문구 {time.monotonic() - started:.1f}초: {text!r}" if text else "리포트 문구 실패 (템플릿 문구로 대체됨)")
    print(f"요청 {client.request_count}회")
    return 0


def _list_models(provider_name: str | None) -> int:
    client = ChatClient.from_env(provider_name)
    if client is None:
        print("API 키가 없습니다. .env를 확인하세요.")
        return 1
    try:
        models = client.list_models()
    except LlmError as e:
        print(f"모델 목록을 가져오지 못했습니다: {e}")
        return 1
    print(f"{client.provider.name} 모델 {len(models)}개 (지금 설정: {client.model}):")
    for model in models:
        print(f"  {model}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="LLM API 연결 확인")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="샘플 창 목록 분류 + 리포트 문구")
    group.add_argument("--list-models", action="store_true", help="쓸 수 있는 모델 ID 목록")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default=None, help="기본: .env의 LLM_PROVIDER")
    args = parser.parse_args()
    raise SystemExit(_check(args.provider) if args.check else _list_models(args.provider))
