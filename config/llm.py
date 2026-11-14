"""LLM API 설정 (SCORE-02·03, 점수 명세 5·7장).

두 제공사 모두 OpenAI 호환 /chat/completions로 부르므로 코드 하나로 바꿔 쓸 수 있다.
비밀이 아닌 값만 여기에 둔다. API 키는 .env에 (config.env):

    LLM_PROVIDER=gemini          # 생략하면 DEFAULT_PROVIDER
    GEMINI_API_KEY=...           # https://aistudio.google.com/apikey (무료 티어, 카드 등록 불필요)
    GROQ_API_KEY=...             # https://console.groq.com/keys (비교용, 무료 플랜)
    LLM_MODEL=...                # 생략하면 아래 기본 모델. 모델이 내려가면 여기만 바꾼다

키가 없으면 LLM 없이 동작한다 (llm_call_failed=True → cam_only 모드, 리포트는 템플릿 문구).
연결 확인: python -m scoring.llm_client --check   /   모델 목록: python -m scoring.llm_client --list-models

모두 임시값이며 점수 명세 9장 검증(VAL-04) 때 두 제공사를 같은 샘플로 비교해 정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str          # 끝에 / 포함. 여기에 "chat/completions"를 붙인다
    model: str             # 기본 모델 ID (.env의 LLM_MODEL이 있으면 그것)
    key_env: str           # API 키를 읽을 환경변수/.env 이름
    extra: dict = field(default_factory=dict)  # 요청 본문에 더할 제공사별 옵션
    json_mode: bool = True  # response_format={"type": "json_object"}를 보낼지


PROVIDERS: dict[str, ProviderConfig] = {
    # Gemini API 무료 티어. 모델은 바뀌는 속도가 빠르다 (2.0 Flash는 2026-06 종료).
    # gemini-3.5-flash-lite가 안 되면 gemini-3.1-flash-lite로 바꿔 본다 (--list-models로 확인)
    "gemini": ProviderConfig(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-3.5-flash-lite",
        key_env="GEMINI_API_KEY",
        extra={"reasoning_effort": "low"},  # Gemini 3 계열은 생각을 끌 수 없어 낮게 (응답 속도)
    ),
    # Groq 무료 플랜 (분당 30회·하루 1,000회 수준). 입력을 학습에 쓰지 않는다고 밝힘
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1/",
        model="openai/gpt-oss-120b",
        key_env="GROQ_API_KEY",
        extra={"reasoning_effort": "low"},
    ),
}

DEFAULT_PROVIDER = "gemini"

# ── 호출·폴백 (SCORE-03) ─────────────────────────────────────────────────────
# 세션 종료 후 결과 화면이 뜨기까지 최악: 분류 (8초 × 2회 + 1초) + 문구 6초 ≈ 23초.
# 점수 계산은 GUI 밖 스레드에서 돌아 그동안 창이 멈추지 않는다 ("결과 정리 중" 표시).
CLASSIFY_TIMEOUT_SEC = 8.0   # 창 분류 요청 1회의 제한 시간
CLASSIFY_RETRIES = 1         # 분류 실패 시 다시 시도할 횟수 (네트워크·5xx·429·응답 형식 오류만)
RETRY_BACKOFF_SEC = 1.0      # 다시 시도하기 전 기다리는 시간
REPORT_TIMEOUT_SEC = 6.0     # 리포트 문구 요청 제한 시간. 실패하면 다시 시도하지 않고 템플릿 문구
TEMPERATURE = 0.0            # 같은 창 목록이면 같은 분류가 나오도록

REPORT_TEXT_MAX_CHARS = 300  # 리포트 문구 최대 길이 (넘으면 문장 단위로 자름)
MAX_ITEMS = 60               # 한 번에 보낼 창 항목 수 상한 (등장 슬롯이 많은 순). 넘는 항목은 unknown
