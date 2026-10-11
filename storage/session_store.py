"""로컬 저장 (개발계획서 3장).

- data/sessions.jsonl: 세션마다 SessionResult 한 줄 (append_session)
- data/focus_log/{session_id}.json: FocusLogFile (save_focus_log)
- 기록 삭제: 위 두 가지 전체 (delete_all_records, 프라이버시 원칙)

session_id는 schemas.make_session_id()로 만든 "YYYYMMDD-HHMMSS"만 받는다.
파일명에 그대로 쓰이므로 형식이 다르면 저장 전에 ValueError로 막는다.

GUI 없이 테스트할 수 있도록 PySide6를 쓰지 않는다.
모든 함수는 data_dir을 인자로 받아 테스트에서 임시 폴더를 쓸 수 있게 한다.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
from pathlib import Path

from schemas import FocusLogFile, SessionResult

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SESSIONS_FILENAME = "sessions.jsonl"
FOCUS_LOG_DIRNAME = "focus_log"
SESSION_ID_PATTERN = re.compile(r"\d{8}-\d{6}")


def sessions_path(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / SESSIONS_FILENAME


def focus_log_dir(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / FOCUS_LOG_DIRNAME


def focus_log_path(session_id: str, data_dir: Path = DATA_DIR) -> Path:
    return focus_log_dir(data_dir) / f"{check_session_id(session_id)}.json"


def check_session_id(session_id: str) -> str:
    """session_id가 "YYYYMMDD-HHMMSS" 형식인지 확인하고 그대로 돌려준다. 아니면 ValueError.

    파일명으로 쓰이므로 콜론·경로 구분자 등이 섞인 값이 저장 경로를 바꾸지 못하게 막는다.
    """
    if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"session_id must be 'YYYYMMDD-HHMMSS': {session_id!r}")
    return session_id


def ensure_data_dir(data_dir: Path = DATA_DIR) -> Path:
    """data_dir과 focus_log/ 폴더를 없으면 만든다 (이미 있으면 그대로)."""
    focus_log_dir(data_dir).mkdir(parents=True, exist_ok=True)
    return data_dir


def append_session(result: SessionResult, data_dir: Path = DATA_DIR) -> Path:
    """SessionResult를 sessions.jsonl 끝에 한 줄로 추가하고 파일 경로를 돌려준다.

    한글 report_text를 그대로 읽을 수 있게 ensure_ascii=False, UTF-8로 쓴다.
    NaN·inf처럼 JSON 표준이 아닌 값이 있으면 다른 언어·도구에서 못 읽으므로 ValueError로 막는다.
    줄 전체를 한 번에 쓰고 flush·fsync하므로, 쓰다가 꺼져도 깨지는 것은 마지막 줄뿐이다
    (읽는 쪽은 깨진 줄을 건너뛴다).
    """
    check_session_id(result["session_id"])
    line = json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n"
    ensure_data_dir(data_dir)
    path = sessions_path(data_dir)
    if not _ends_with_newline(path):
        # 이전 저장이 줄 중간에서 끊겼으면 새 줄까지 같이 깨지지 않게 줄을 바꾼다
        line = "\n" + line
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return path


def _ends_with_newline(path: Path) -> bool:
    """파일이 없거나 비었거나 줄바꿈으로 끝나면 True."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return True
            f.seek(-1, os.SEEK_END)
            return f.read(1) == b"\n"
    except FileNotFoundError:
        return True


def save_focus_log(log: FocusLogFile, data_dir: Path = DATA_DIR) -> Path:
    """FocusLogFile을 focus_log/{session_id}.json으로 저장하고 파일 경로를 돌려준다.

    임시 파일에 다 쓴 뒤 이름을 바꾸므로 저장 중 꺼져도 반쪽짜리 파일이 남지 않는다.
    같은 session_id 파일이 이미 있으면 덮어쓰지 않고 FileExistsError (기록 유실 방지).
    """
    path = focus_log_path(log["session_id"], data_dir)
    text = json.dumps(log, ensure_ascii=False, allow_nan=False)
    ensure_data_dir(data_dir)
    if path.exists():
        raise FileExistsError(f"focus_log already exists: {path}")
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def delete_all_records(data_dir: Path = DATA_DIR) -> None:
    """sessions.jsonl과 focus_log/ 폴더 전체를 지운다 (앱 내 "기록 삭제", 3장).

    지울 것이 없어도 오류가 아니다. data_dir 안의 다른 파일은 건드리지 않는다.
    지우는 중 실패하면 OSError를 그대로 올린다 (GUI가 "삭제하지 못함"을 알려야 하므로).
    """
    sessions_path(data_dir).unlink(missing_ok=True)
    log_dir = focus_log_dir(data_dir)
    if log_dir.is_dir():
        shutil.rmtree(log_dir)


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
