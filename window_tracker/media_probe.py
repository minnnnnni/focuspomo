"""시스템 미디어 재생 상태 — Windows 미디어 세션 API(GSMTC) 스파이크 (Windows 실기 테스트 전).

볼륨 키를 누르면 뜨는 '지금 재생 중' 패널과 같은 정보원이다. Chrome·Edge의 유튜브, Spotify 등
미디어 세션을 지원하는 앱의 재생/일시정지 상태, 앱, 제목을 읽는다.

설치: python -m pip install winrt-Windows.Media.Control winrt-Windows.Foundation
설치되어 있지 않거나 API 호출이 실패하면 playing=None(측정 불가)을 돌려준다.
미디어 제목은 '지금 앞 화면에 보이는 영상인지' 판정과 카테고리 매칭에만 쓰고 기록하지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing

# 세션의 앱 ID(AUMID) 안에 들어 있는 문자열 → app_names.py와 같은 표시 이름 (실기 테스트로 보완 필요)
MEDIA_APP_NAMES: dict[str, str] = {
    "msedge": "Edge",
    "chrome": "Chrome",
    "whale": "Whale",
    "firefox": "Firefox",
    "308046b0af4a39cb": "Firefox",  # Firefox가 쓰는 AUMID
    "spotify": "Spotify",
}


def media_app_name(aumid: str | None) -> str | None:
    if not aumid:
        return None
    key = aumid.lower()
    for token, name in MEDIA_APP_NAMES.items():
        if token in key:
            return name
    return aumid.removesuffix(".exe")


@dataclass(frozen=True)
class MediaState:
    playing: bool | None          # None = 측정 불가
    app: str | None = None
    title: str | None = None


UNAVAILABLE = MediaState(playing=None)
IDLE = MediaState(playing=False)


async def _await(operation):
    return await operation


class MediaProbe:
    """스레드 하나에서 계속 쓰는 것을 전제로 한다 (자기 이벤트 루프를 가짐)."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._manager = None
        self._failed = False

    def _ensure_manager(self):
        if self._manager is not None or self._failed:
            return self._manager
        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Manager,
            )
            self._loop = asyncio.new_event_loop()
            self._manager = self._loop.run_until_complete(_await(Manager.request_async()))
        except Exception as e:
            log.warning("미디어 세션 API 사용 불가 — media_playing=None으로 기록: %s", e)
            self._failed = True
        return self._manager

    def sample(self) -> MediaState:
        manager = self._ensure_manager()
        if manager is None:
            return UNAVAILABLE
        try:
            for session in manager.get_sessions():
                info = session.get_playback_info()
                if info is not None and int(info.playback_status) == PLAYING:
                    return MediaState(True, media_app_name(session.source_app_user_model_id),
                                      self._title(session))
            return IDLE
        except Exception as e:
            log.debug("미디어 상태 조회 실패: %s", e)
            return UNAVAILABLE

    def _title(self, session) -> str | None:
        try:
            props = self._loop.run_until_complete(_await(session.try_get_media_properties_async()))
            return (props.title or "").strip() or None
        except Exception:
            return None

    def close(self) -> None:
        if self._loop is not None:
            self._loop.close()
