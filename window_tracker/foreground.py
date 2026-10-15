"""포그라운드 창 조회 (Windows) + 변화 감지 (WIN-01, WIN-02).

매초 창 핸들과 제목만 읽고(아주 가벼움), 둘 중 하나가 바뀌었을 때만
프로세스 이름·주소창(UI Automation)·유튜브 카테고리(API) 같은 무거운 조회를 다시 한다.
창 제목은 변화 감지와 백그라운드 재생 판정에만 메모리에서 쓰고, 저장하거나 내보내지 않는다.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from window_tracker.app_names import is_browser, resolve_app_name
from window_tracker.domain_probe import read_address_bar, url_to_domain
from window_tracker.youtube_probe import YouTubeCategoryLookup, extract_video

try:  # Windows 전용. 다른 OS에서는 함수를 주입한 단위 테스트만 가능
    import psutil
    import win32gui
    import win32process
except ImportError:
    psutil = win32gui = win32process = None

logger = logging.getLogger(__name__)

SELF_APP_NAME = "FocusPomo"  # 우리 앱 창 자체를 보고 있을 때


@dataclass(frozen=True)
class ForegroundState:
    app_name: str | None
    domain: str | None = None
    video_category: str | None = None  # 유튜브 영상이면 카테고리
    title: str = ""                    # 창 제목 — 메모리에서만 사용, 기록하지 않음


NONE_STATE = ForegroundState(None)


# ── OS 조회 ──────────────────────────────────────────

def _hwnd_to_pid(hwnd: int) -> int:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return pid


def _pid_to_exe(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _uwp_exe(frame_hwnd: int) -> str | None:
    """UWP 앱(계산기, 설정 등)은 ApplicationFrameHost 창 안에 실제 앱 창이 자식으로 붙어 있다."""
    host_pid = _hwnd_to_pid(frame_hwnd)
    children: list[int] = []

    def collect(hwnd, _extra):
        children.append(hwnd)
        return True

    try:
        win32gui.EnumChildWindows(frame_hwnd, collect, None)
    except Exception:
        return None
    for child in children:
        pid = _hwnd_to_pid(child)
        if pid != host_pid:
            return _pid_to_exe(pid)
    return None  # 최소화된 UWP 앱은 자식 창이 떨어져 있어 못 찾을 수 있음


def exe_for_hwnd(hwnd: int) -> str | None:
    """창 핸들 → exe 이름. 우리 앱 자신이면 SELF_APP_NAME, 실패하면 None."""
    pid = _hwnd_to_pid(hwnd)
    if pid == os.getpid():
        return SELF_APP_NAME
    exe = _pid_to_exe(pid)
    if exe and exe.lower() == "applicationframehost.exe":
        exe = _uwp_exe(hwnd) or exe
    return exe


def foreground_window() -> tuple[int, str | None]:
    """(포그라운드 창 핸들, exe 이름). 창이 없으면 (0, None)."""
    hwnd = win32gui.GetForegroundWindow()
    return (hwnd, exe_for_hwnd(hwnd)) if hwnd else (0, None)


def read_foreground_key() -> tuple[int, str]:
    """(창 핸들, 창 제목) — 매초 호출해도 부담 없는 가벼운 조회."""
    hwnd = win32gui.GetForegroundWindow()
    return hwnd, (win32gui.GetWindowText(hwnd) if hwnd else "")


def resolve_foreground(hwnd: int, title: str = "", *, use_domain_probe: bool = False,
                       youtube: YouTubeCategoryLookup | None = None) -> ForegroundState:
    """창 핸들 → 앱 이름, 도메인, 유튜브 카테고리. 시스템 창이면 NONE_STATE.

    youtube를 주면 도메인 추출도 함께 켜진다 (카테고리는 주소창의 영상 ID로 조회하므로).
    """
    exe = exe_for_hwnd(hwnd)
    if exe == SELF_APP_NAME:
        return ForegroundState(SELF_APP_NAME, title=title)
    app_name = resolve_app_name(exe, win32gui.GetClassName(hwnd))
    if app_name is None:
        return NONE_STATE
    domain = category = None
    if (use_domain_probe or youtube is not None) and is_browser(exe):
        text = read_address_bar(hwnd)
        domain = url_to_domain(text)
        video = extract_video(text) if youtube is not None else None
        if video is not None:
            info = youtube.lookup(video[1])
            category = info.category if info else None
    return ForegroundState(app_name, domain, category, title)


# ── 변화 감지 ────────────────────────────────────────

class ForegroundSampler:
    """창 핸들·제목이 바뀔 때만 resolve를 다시 호출한다. read_key / resolve는 테스트용 주입 지점."""

    def __init__(self, *, use_domain_probe: bool = False,
                 youtube: YouTubeCategoryLookup | None = None,
                 read_key: Callable[[], tuple[int, str]] | None = None,
                 resolve: Callable[[int, str], ForegroundState] | None = None):
        self._read_key = read_key or read_foreground_key
        self._resolve = resolve or (lambda hwnd, title: resolve_foreground(
            hwnd, title, use_domain_probe=use_domain_probe, youtube=youtube))
        self._last_key: tuple[int, str] | None = None
        self._last_state = NONE_STATE
        self.resolve_count = 0  # 무거운 조회를 몇 번 했는지 (확인용)

    def sample(self) -> ForegroundState:
        key = self._read_key()
        if key == self._last_key:
            return self._last_state
        hwnd, title = key
        if not hwnd:  # 창 전환 중, 잠금 화면 전환 중 등
            state = NONE_STATE
        else:
            self.resolve_count += 1
            try:
                state = self._resolve(hwnd, title)
            except Exception:
                logger.exception("활성 창 조회 실패 — 이번 초는 app_name=None")
                self._last_key = None  # 캐시하지 않고 다음 초에 다시 시도
                return NONE_STATE
        self._last_key, self._last_state = key, state
        return state
