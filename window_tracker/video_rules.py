"""유튜브 영상 분류표 적용 (점수 명세 2.1, docs/유튜브_영상_분류표.md).

표(config/window_rules.py VIDEO_POLICIES)는 영상 분류마다 세 가지를 정한다:
- 앞 화면 라벨: 영상을 띄워 두고 있을 때 작업 / 미분류 / 딴짓
- 유예: 딴짓 분류라도 고르는 시간(grace_sec)을 둔다. 같은 분류를 앞 화면에서 이어서 본 묶음 하나의 전체 길이로
  세션 종료 후 그 묶음의 슬롯을 한꺼번에 정한다 (소급, video_run_label):
    grace_sec 초과 → 묶음 전체 딴짓 / SETUP_FOCUS_SEC 이하 → 집중 (고르고 나감) / 그 사이 → 미분류
- 백그라운드: 앞 화면에 없이 재생만 될 때 딴짓인지, 무시할지 (무시하면 앞 화면 창으로 판정)

창 스레드(window_tracker.tracker)는 1초마다 이 모듈로 규칙상 딴짓인 초를 고르고(유예를 넘긴 뒤의 초 포함),
슬롯마다 묶음 번호·길이(video_run, video_run_sec)를 보낸다. 점수 모듈(scoring.focus_score)은 규칙상 딴짓이 아닌
유튜브 영상 슬롯의 라벨을 여기서 정한다 (LLM에 보내지 않음).
"""
from __future__ import annotations

import math
import re

from config.aggregation import VIDEO_WATCH_RESET_SEC
from config.window_rules import (
    BGM,
    BGM_TITLE_PATTERNS,
    DEFAULT_VIDEO_POLICY,
    SETUP_FOCUS_SEC,
    SHORTS,
    VIDEO_POLICIES,
    VideoPolicy,
)
from schemas import WindowLabel

_BGM_RE = re.compile("|".join(f"(?:{p})" for p in BGM_TITLE_PATTERNS), re.IGNORECASE)


# ── 영상 분류 ────────────────────────────────────────

def is_bgm_title(title: str | None) -> bool:
    """제목이 공부 배경음(플리·lofi·백색소음·스터디윗미 등)인지."""
    return bool(title) and _BGM_RE.search(title) is not None


def classify_video(kind: str | None, category: str | None, title: str | None) -> str | None:
    """유튜브 영상 → 영상 분류 (VIDEO_POLICIES의 키). 영상인지 모르면 None.

    kind: youtube_probe.extract_video의 "watch" / "shorts" / "live" / "embed" (백그라운드 영상은 몰라서 None)
    category: YouTube 카테고리 이름 (조회 실패·API 꺼짐이면 None)
    title: 영상 제목 (앞 화면은 API 제목, 없으면 창 제목 / 백그라운드는 미디어 제목)

    쇼츠 > 배경음 제목 > 카테고리 순이다. 쇼츠·배경음은 주소·제목만 보므로 API 키가 없어도 정해진다.
    """
    if kind == "shorts":
        return SHORTS
    if is_bgm_title(title):
        return BGM
    return category


def policy_for(video_class: str | None) -> VideoPolicy:
    return VIDEO_POLICIES.get(video_class, DEFAULT_VIDEO_POLICY) if video_class else DEFAULT_VIDEO_POLICY


# ── 판정 ─────────────────────────────────────────────

def foreground_label(video_class: str | None, watched_sec: float = 0) -> WindowLabel:
    """앞 화면에 있는 영상의 이번 초 라벨 (세션 중 창 스레드가 쓰는 값, 소급 전).

    watched_sec: 이번 초 전까지 같은 분류를 앞 화면에서 이어서 본 시간 (VideoWatchRun.update).
    딴짓 분류라도 watched_sec < grace_sec이면 아직 고르는 중일 수 있어 미분류. 최종 라벨은 video_run_label.
    """
    policy = policy_for(video_class)
    if policy.foreground == "distraction" and watched_sec < policy.grace_sec:
        return "unknown"
    return policy.foreground


def is_foreground_distraction(video_class: str | None, watched_sec: float = 0) -> bool:
    """앞 화면 영상이 이번 초에 규칙상 딴짓인지. 영상이 아니면(None) 아니다."""
    return video_class is not None and foreground_label(video_class, watched_sec) == "distraction"


def is_background_distraction(video_class: str | None) -> bool:
    """앞 화면에 없이 재생 중인 영상이 딴짓인지. 분류를 모르면(None) 아니다 (불확실하면 감점하지 않음)."""
    return video_class is not None and policy_for(video_class).background_distraction


def video_run_label(video_class: str, run_sec: float | None) -> WindowLabel:
    """규칙상 딴짓이 아닌 유튜브 영상 슬롯의 최종 창 라벨 (점수 모듈이 LLM 대신 쓴다, 세션 종료 후 소급).

    run_sec: 그 슬롯이 속한 묶음(같은 분류를 앞 화면에서 이어서 본 시간)의 전체 길이. 모르면 None (v4 기록 등).
    - 작업·미분류 분류 → 그대로
    - 바로 딴짓인 분류(grace_sec 0) → 딴짓. 대표 창이 이 영상이면 딴짓이다 (보통은 이미 규칙상 딴짓이고, 여기로 오는 것은
      나머지 초가 조회 실패라 DISTRACTION_MIN_SEC보다 짧게 보고도 대표가 된 슬롯)
    - 유예가 있는 분류 → 묶음이 grace_sec를 넘겼으면 딴짓 (유예 구간까지 소급), SETUP_FOCUS_SEC 이하면 집중
      (노래만 고르고 나감), 그 사이·모르면 미분류
    """
    policy = policy_for(video_class)
    if policy.foreground != "distraction":
        return policy.foreground
    if policy.grace_sec == 0:
        return "distraction"
    if run_sec is None:
        return "unknown"
    if run_sec > policy.grace_sec:
        return "distraction"
    if run_sec <= SETUP_FOCUS_SEC:
        return "work"
    return "unknown"


def video_run_totals(slots) -> dict[int, float]:
    """슬롯 목록 → {묶음 번호: 그 묶음을 이어서 본 전체 시간}. 묶음 길이는 슬롯마다 누적값이라 최댓값이 전체다."""
    totals: dict[int, float] = {}
    for s in slots:
        run, sec = s.get("video_run"), s.get("video_run_sec")
        if run is not None and sec is not None:
            totals[run] = max(totals.get(run, 0.0), sec)
    return totals


# ── 이어서 본 시간 ───────────────────────────────────

class VideoWatchRun:
    """앞 화면에서 같은 영상 분류를 이어서 본 묶음과 그 시간. 슬롯이 바뀌어도 이어서 센다 (CV closed_run과 같은 방식).

    - 다른 창(유튜브 홈·검색 포함)을 보는 초는 늘지 않는다. 다른 창에 VIDEO_WATCH_RESET_SEC보다 오래 있었으면
      새 묶음으로 0부터 센다. 그래서 노래를 틀고 다른 창으로 갔다가 곡만 넘기러 잠깐 오면 매번 짧은 새 묶음이다.
    - 분류가 바뀌면(예: Education → Music) 새 묶음이다. 강의를 오래 보다가 음악을 고르러 가도 유예가 있다.
    - 같은 분류의 다른 영상(뮤비 → 다음 뮤비)은 같은 묶음으로 이어서 센다.
    - 시간은 조회 횟수 × sample_interval로 센다. 절전 등으로 조회가 끊긴 시간은 다른 창에 있던 것과 같다.
    - 묶음 번호(run_id)는 세션(트래커) 안에서 0부터 붙는다. 점수 모듈이 묶음별 전체 길이로 소급할 때 쓴다.
    """

    def __init__(self, *, sample_interval: float = 1.0, reset_sec: float = VIDEO_WATCH_RESET_SEC):
        self.sample_interval = sample_interval
        self.reset_sec = reset_sec
        self.video_class: str | None = None
        self.run_id: int | None = None  # 지금(마지막으로 본) 묶음 번호
        self.watched = 0.0
        self.last_seen = -math.inf
        self._next_id = 0

    def update(self, timestamp: float, video_class: str | None) -> float:
        """이번 초의 앞 화면 영상 분류를 넣고, 이번 초 전까지 이 묶음을 이어서 본 시간을 돌려준다. 영상이 아니면 0.

        영상이면 이번 초가 속한 묶음 번호가 run_id에 들어간다.
        """
        if video_class is None:
            return 0.0
        gap = timestamp - self.last_seen - self.sample_interval  # 그사이 다른 창에 있던 시간
        if video_class != self.video_class or gap > self.reset_sec:
            self.video_class, self.watched = video_class, 0.0
            self.run_id, self._next_id = self._next_id, self._next_id + 1
        before = self.watched
        self.watched += self.sample_interval
        self.last_seen = timestamp
        return before
