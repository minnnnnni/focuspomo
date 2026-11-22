"""세션 설정 저장 (data/settings.json): 집중·휴식 길이와 긴 휴식 주기 (core.pomodoro.PomodoroSettings),
추적 모드 (core.tracking.TrackingMode, "tracking_mode" 키).

- 두 값은 화면이 달라(설정 화면 / 대기 화면) 따로 저장하지만 한 파일에 둔다. 한쪽을 저장할 때 다른 쪽 키는 그대로 둔다.

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
from core.tracking import DEFAULT_TRACKING_MODE, TrackingMode
from storage.session_store import DATA_DIR

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"
TRACKING_MODE_KEY = "tracking_mode"


def settings_path(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / SETTINGS_FILENAME


def _read(data_dir: Path) -> dict | None:
    """settings.json의 dict. 없거나 읽지 못했거나 dict가 아니면 None."""
    path = settings_path(data_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("설정 파일을 읽지 못해 기본값을 씁니다: %s", path, exc_info=True)
        return None
    try:
        data = json.loads(text)
    except ValueError:
        logger.warning("설정 파일이 깨져 기본값을 씁니다: %s", path)
        return None
    return data if isinstance(data, dict) else None


def _write(updates: dict, data_dir: Path) -> Path:
    """읽을 수 있는 기존 키는 두고 updates만 바꿔 저장한다. 실패하면 OSError."""
    data = _read(data_dir) or {}
    data.update(updates)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = settings_path(data_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_settings(data_dir: Path = DATA_DIR) -> PomodoroSettings:
    """저장된 설정. 없거나 읽지 못하면 기본값."""
    data = _read(data_dir)
    return PomodoroSettings() if data is None else PomodoroSettings.from_dict(data)


def save_settings(settings: PomodoroSettings, data_dir: Path = DATA_DIR) -> Path:
    """설정을 저장한다 (추적 모드는 그대로). 실패하면 OSError."""
    return _write(settings.clamped().to_dict(), data_dir)


def load_tracking_mode(data_dir: Path = DATA_DIR) -> TrackingMode:
    """저장된 추적 모드. 없거나 모르는 값이면 기본값(웹캠 + 창)."""
    data = _read(data_dir)
    return DEFAULT_TRACKING_MODE if data is None else TrackingMode.parse(data.get(TRACKING_MODE_KEY))


def save_tracking_mode(mode: TrackingMode, data_dir: Path = DATA_DIR) -> Path:
    """추적 모드를 저장한다 (세션 설정은 그대로). 실패하면 OSError."""
    return _write({TRACKING_MODE_KEY: TrackingMode(mode).value}, data_dir)
