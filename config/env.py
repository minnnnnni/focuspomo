"""API 키 등 비밀 값 읽기 — 환경변수가 먼저, 없으면 레포 루트 .env.

.env는 .gitignore에 있어 커밋되지 않는다. 형식은 한 줄에 NAME=value (따옴표·UTF-8 BOM 허용).
값은 메모리에서만 쓰고 로그·예외 메시지에 넣지 않는다.

    from config.env import load_env_value
    key = load_env_value("GEMINI_API_KEY")
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"


def read_env_file(env_path: Path | None = None) -> dict[str, str]:
    """.env 파일 → {이름: 값}. 파일이 없거나 읽지 못하면 빈 dict."""
    path = DEFAULT_ENV_PATH if env_path is None else env_path
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, ValueError):  # 파일 없음, 인코딩 문제(UTF-16 등)
        return {}
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep:
            values[name.strip()] = value.strip().strip("\"'")
    return values


def load_env_value(name: str, env_path: Path | None = None) -> str | None:
    """환경변수 name, 없으면 .env의 name. 비어 있으면 None."""
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    return read_env_file(env_path).get(name) or None
