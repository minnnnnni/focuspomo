"""로컬 저장 (개발계획서 3장).

- data/sessions.jsonl: 세션마다 SessionResult 한 줄 (1-17에서 추가)
- data/focus_log/{session_id}.json: FocusLogFile (1-18에서 추가)

GUI 없이 테스트할 수 있도록 PySide6를 쓰지 않는다.
모든 함수는 data_dir을 인자로 받아 테스트에서 임시 폴더를 쓸 수 있게 한다.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SESSIONS_FILENAME = "sessions.jsonl"


def sessions_path(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / SESSIONS_FILENAME


def load_previous_baseline(data_dir: Path = DATA_DIR) -> float | None:
    """sessions.jsonl에서 가장 최근 세션의 ear_baseline을 읽는다 (캘리브레이션 폴백용, 2.0).

    파일이 없거나 쓸 수 있는 값이 하나도 없으면 None (= 첫 세션, 폴백 불가).
    깨진 줄(저장 중 종료 등)이나 ear_baseline이 양의 실수가 아닌 줄은 건너뛰고
    그 앞의 세션 값을 쓴다.
    """
    path = sessions_path(data_dir)
    try:
        # 깨진 바이트가 있어도 그 줄만 JSON 파싱에서 걸러지도록 replace로 읽는다
        with path.open(encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None
    except OSError:
        # 읽기 실패로 앱이 멈추지 않게 "직전 값 없음"으로 본다 (세션 시작 거부로 이어짐)
        logger.warning("sessions.jsonl을 읽지 못했습니다: %s", path, exc_info=True)
        return None

    for line in reversed(lines):
        baseline = _baseline_from_line(line)
        if baseline is not None:
            return baseline
    return None


def _baseline_from_line(line: str) -> float | None:
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    value = record.get("ear_baseline")
    # bool은 int의 하위 타입이라 따로 막는다
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)
