"""CV·창 mock을 합쳐 세션 하나 분량의 slot_queue 메시지를 만든다.

- 도착 시각: 슬롯 k의 창 메시지는 슬롯 시작 직후, CV 메시지는 슬롯이 끝난 뒤 (개발계획서 1장).
- 경계 케이스: 도착 순서 뒤바뀜 / 한쪽 누락 / 창 메시지 중복 / 마지막 슬롯 10초 미만.
- feed_queue(): 별도 threading.Thread에서 도착 시각에 맞춰 queue.Queue에 put (GUI 개발용).

    python -m mocks.mock_session           # 시나리오 목록과 메시지 요약 출력
"""
from __future__ import annotations

import queue
import random
import threading
import time
from typing import Literal

from mocks.mock_cv_slots import FULL_AWAY, FULL_FOCUS, make_cv_messages, random_cv_patterns
from mocks.mock_window_slots import (
    APP_DOCS,
    APP_FAILED,
    APP_KAKAO,
    APP_VSCODE,
    APP_YOUTUBE,
    App,
    make_window_messages,
    random_apps,
    window_message,
)
from schemas import SlotMessage, slot_start

TimedMessage = tuple[float, SlotMessage]  # (도착 시각 epoch 초, 메시지)
Order = Literal["arrival", "reversed", "shuffled"]

CV_DELAY_SEC = 0.2          # 슬롯이 끝난 뒤 CV 메시지가 도착하기까지
DUPLICATE_JITTER_SEC = 0.8  # 중복 창 메시지의 조회 지터

SCENARIOS: tuple[str, ...] = (
    "normal",
    "drowsy_boundary",
    "out_of_order",
    "missing",
    "duplicate_window",
    "short_last_slot",
    "empty",
    "full_session",
)


def build_session(
    session_start: float,
    cv_patterns: list[str],
    apps: list[App],
    *,
    drop_cv: set[int] | frozenset[int] = frozenset(),
    drop_window: set[int] | frozenset[int] = frozenset(),
    duplicate_window: dict[int, App] | None = None,
    order: Order = "arrival",
    seed: int = 0,
) -> list[TimedMessage]:
    """세션 메시지를 도착 시각 순으로 돌려준다.

    drop_cv / drop_window: 메시지가 끝내 오지 않는 슬롯 번호.
    duplicate_window: {slot: app} — 같은 슬롯 창 메시지를 한 번 더 보냄 (나중 것이 이겨야 함).
    order: "arrival" 외에는 도착 시각은 그대로 두고 메시지 순서만 뒤섞는다.
    """
    timed: list[TimedMessage] = []
    for msg, pattern in zip(make_cv_messages(session_start, cv_patterns), cv_patterns):
        slot = msg["slot"]["slot"]
        if slot not in drop_cv:
            arrival = slot_start(slot, session_start) + len(pattern) + CV_DELAY_SEC
            timed.append((arrival, msg))
    for msg in make_window_messages(session_start, apps):
        if msg["slot"]["slot"] not in drop_window:
            timed.append((msg["slot"]["timestamp"], msg))
    for slot, app in (duplicate_window or {}).items():
        dup = window_message(slot, session_start, app, jitter=DUPLICATE_JITTER_SEC)
        timed.append((dup["slot"]["timestamp"], dup))

    timed.sort(key=lambda t: t[0])
    if order == "arrival":
        return timed
    arrivals = [t for t, _ in timed]
    messages = [m for _, m in timed]
    if order == "reversed":
        messages.reverse()
    else:
        random.Random(seed).shuffle(messages)
    return list(zip(arrivals, messages))


def build_scenario(name: str, session_start: float, seed: int = 0) -> list[TimedMessage]:
    """이름 붙은 시나리오. 이름은 SCENARIOS 참고."""
    normal_cv = [FULL_FOCUS, "PBPPPBPPPP", FULL_AWAY, "PPPPCCCCCP", "PBPPPPPPPP", FULL_FOCUS]
    normal_apps = [APP_VSCODE, APP_DOCS, APP_KAKAO, APP_VSCODE, APP_YOUTUBE, APP_VSCODE]

    match name:
        case "normal":
            return build_session(session_start, normal_cv, normal_apps)
        case "drowsy_boundary":
            # 슬롯 0 끝 2초 + 슬롯 1 앞 3초 감김 → 슬롯 1만 drowsy (closed_run 이어 세기)
            return build_session(session_start, ["PPPPPPPPCC", "CCCPPPPPPP"], [APP_VSCODE] * 2)
        case "out_of_order":
            return build_session(session_start, normal_cv, normal_apps, order="shuffled", seed=seed)
        case "missing":
            # 슬롯 2 CV 누락, 슬롯 4 창 누락, 슬롯 3 창 조회 실패, 슬롯 1 초 요약 일부 누락
            apps = list(normal_apps)
            apps[3] = APP_FAILED
            cv = list(normal_cv)
            cv[1] = "PP--PPPPPP"
            return build_session(session_start, cv, apps, drop_cv={2}, drop_window={4})
        case "duplicate_window":
            # 슬롯 1 창 메시지가 두 번: DOCS 다음 YOUTUBE → YOUTUBE가 최종
            return build_session(session_start, normal_cv, normal_apps,
                                 duplicate_window={1: APP_YOUTUBE})
        case "short_last_slot":
            # 중간 종료: 마지막 슬롯(5)은 4초만 있음
            cv = normal_cv[:-1] + ["PPBP"]
            return build_session(session_start, cv, normal_apps)
        case "empty":
            return []
        case "full_session":
            rng = random.Random(seed)
            n = 150  # 25분
            return build_session(session_start, random_cv_patterns(n, rng), random_apps(n, rng))
    raise ValueError(f"unknown scenario: {name!r} (choose from {SCENARIOS})")


def messages_only(timed: list[TimedMessage]) -> list[SlotMessage]:
    return [m for _, m in timed]


def feed_queue(
    slot_queue: queue.Queue,
    timed: list[TimedMessage],
    session_start: float,
    *,
    speed: float = 1.0,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """메시지를 도착 시각에 맞춰 slot_queue에 넣는 데몬 스레드를 시작해 돌려준다.

    speed=10이면 10배 빠르게 (25분 세션이 2.5분). 스레드는 큐에 쓰기만 한다.
    """
    stop = stop_event or threading.Event()

    def run() -> None:
        wall_start = time.time()
        for arrival, msg in timed:
            due = wall_start + (arrival - session_start) / speed
            if stop.wait(max(0.0, due - time.time())):
                return
            slot_queue.put(msg)

    thread = threading.Thread(target=run, name="mock-slot-feeder", daemon=True)
    thread.start()
    return thread


def _describe(msg: SlotMessage) -> str:
    s = msg["slot"]
    if msg["source"] == "cv":
        return (f"cv     slot={s['slot']:>3} face={s['face_present']!s:5} drowsy={s['drowsy']!s:5} "
                f"run_max={s['closed_run_max']} blink={s['blink_count']} seconds={len(msg['seconds'])}")
    return f"window slot={s['slot']:>3} app={s['app_name']} domain={s['domain']}"


if __name__ == "__main__":
    start = time.time()
    for scenario in SCENARIOS:
        timed = build_scenario(scenario, start)
        print(f"\n## {scenario} ({len(timed)} messages)")
        for arrival, msg in timed[:14]:
            print(f"  +{arrival - start:6.2f}s  {_describe(msg)}")
        if len(timed) > 14:
            print("  ...")
