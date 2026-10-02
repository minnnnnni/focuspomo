"""WindowSlotMessage mock 생성기 (창 스레드 대역, 2주차에 실제 window_tracker로 교체).

창 스레드는 session_start + 10*k 시각에 조회하므로 timestamp는 슬롯 시작 + 약간의 지터다.
지터는 [0, 10) 범위여야 slot_index()가 같은 슬롯을 가리킨다.
"""
from __future__ import annotations

import random

from schemas import WindowSlotMessage, slot_index, slot_start

# (app_name, domain)
App = tuple[str | None, str | None]

APP_VSCODE: App = ("VS Code", None)
APP_DOCS: App = ("Chrome", "docs.python.org")
APP_NOTION: App = ("Notion", None)
APP_YOUTUBE: App = ("Chrome", "youtube.com")
APP_KAKAO: App = ("KakaoTalk", None)
APP_FAILED: App = (None, None)  # 잠금 화면 등 조회 실패

WORK_APPS: tuple[App, ...] = (APP_VSCODE, APP_DOCS, APP_NOTION)
DISTRACTION_APPS: tuple[App, ...] = (APP_YOUTUBE, APP_KAKAO)


def window_message(
    slot: int,
    session_start: float,
    app: App,
    jitter: float = 0.05,
) -> WindowSlotMessage:
    """슬롯 하나의 창 메시지. jitter는 슬롯 시작으로부터 조회까지 걸린 초."""
    timestamp = slot_start(slot, session_start) + jitter
    if slot_index(timestamp, session_start) != slot:
        raise ValueError(f"jitter {jitter} moves timestamp out of slot {slot}")
    app_name, domain = app
    return {
        "source": "window",
        "slot": {
            "slot": slot,
            "timestamp": timestamp,
            "app_name": app_name,
            "domain": domain,
        },
    }


def make_window_messages(session_start: float, apps: list[App]) -> list[WindowSlotMessage]:
    """슬롯 0부터 순서대로 창 메시지를 만든다."""
    return [window_message(slot, session_start, app) for slot, app in enumerate(apps)]


def random_apps(n_slots: int, rng: random.Random) -> list[App]:
    """긴 세션용 무작위 창. 같은 앱이 몇 슬롯 이어지도록 만든다."""
    apps: list[App] = []
    current = rng.choice(WORK_APPS)
    for _ in range(n_slots):
        if rng.random() < 0.2:
            roll = rng.random()
            if roll < 0.7:
                current = rng.choice(WORK_APPS)
            elif roll < 0.95:
                current = rng.choice(DISTRACTION_APPS)
            else:
                current = APP_FAILED
        apps.append(current)
    return apps
