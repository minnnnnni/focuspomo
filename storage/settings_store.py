"""세션 설정 저장 (data/settings.json): 집중·휴식 길이와 긴 휴식 주기 (core.pomodoro.PomodoroSettings).

- 파일이 없거나 깨졌으면 기본값으로 읽는다 (앱은 그대로 켜진다). 범위를 벗어난 값은 범위 안으로 맞춘다.
- 기록 삭제(session_store.delete_all_records)는 이 파일을 지우지 않는다 (세션 기록이 아니라 환경설정).
- 저장은 임시 파일에 쓴 뒤 바꿔치기해서, 쓰다 실패해도 이전 설정이 남는다.

GUI 없이 테스트할 수 있도록 PySide6를 쓰지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core.pomodoro import PomodoroSettings
from storage.session_store import DATA_DIR

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"


def settings_path(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / SETTINGS_FILENAME


def load_settings(data_dir: Path = DATA_DIR) -> PomodoroSettings:
    """저장된 설정. 없거나 읽지 못하면 기본값."""
    path = settings_path(data_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return PomodoroSettings()
    except OSError:
        logger.warning("설정 파일을 읽지 못해 기본값을 씁니다: %s", path, exc_info=True)
        return PomodoroSettings()
    try:
        data = json.loads(text)
    except ValueError:
        logger.warning("설정 파일이 깨져 기본값을 씁니다: %s", path)
        return PomodoroSettings()
    return PomodoroSettings.from_dict(data)


def save_settings(settings: PomodoroSettings, data_dir: Path = DATA_DIR) -> Path:
    """설정을 저장한다. 실패하면 OSError."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = settings_path(data_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings.clamped().to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
