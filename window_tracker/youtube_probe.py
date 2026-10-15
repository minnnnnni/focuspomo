"""유튜브 영상 카테고리 조회 — 스파이크 (YouTube Data API v3, Windows 실기 테스트 전).

주소창 텍스트 → 영상 ID → videos.list(part=snippet) → categoryId → 카테고리 이름 (예: "Gaming").

- API 키는 환경변수 YOUTUBE_API_KEY 또는 레포 루트 .env에서 읽는다 (.env는 절대 커밋 금지).
- 같은 영상은 캐시해서 한 번만 조회한다 (videos.list 1회 = 할당량 1, 하루 기본 10,000).
- 어떤 이유로든 실패하면 None. 영상 ID·제목은 저장하지 않고, 바깥으로 내보낼 값은 카테고리뿐이다.
- 카테고리는 업로더가 직접 고른 값이라 실제 내용과 다를 수 있다.

확인용 실행 (레포 루트, venv 활성화 상태):
    python -m window_tracker.youtube_probe --video-id dQw4w9WgXcQ   # API 키만 확인 (브라우저 불필요)
    python -m window_tracker.youtube_probe --watch 60               # 5초마다 브라우저 창을 읽어 카테고리 출력
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

API_URL = "https://www.googleapis.com/youtube/v3/videos"

# videoCategories.list(regionCode=KR) 기준 주요 카테고리. 없는 ID는 "Unknown(<id>)"으로 표시
CATEGORY_NAMES: dict[str, str] = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "19": "Travel & Events",
    "20": "Gaming",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "music.youtube.com"}


# ── 주소창 텍스트 → 영상 ID ──────────────────────────

def extract_video(url_text: str | None) -> tuple[str, str] | None:
    """주소창 텍스트 → (kind, video_id). 유튜브 영상 페이지가 아니면 None.

    kind: "watch"(일반 영상, youtu.be 포함) | "shorts" | "live" | "embed"
    """
    if not url_text:
        return None
    text = url_text.strip()
    if not text or " " in text:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
    path = [p for p in parts.path.split("/") if p]

    kind = video_id = None
    if host == "youtu.be" and path:
        kind, video_id = "watch", path[0]
    elif host in _YOUTUBE_HOSTS and path[:1] == ["watch"]:
        kind = "watch"
        video_id = urllib.parse.parse_qs(parts.query).get("v", [None])[0]
    elif host in _YOUTUBE_HOSTS and len(path) >= 2 and path[0] in ("shorts", "live", "embed"):
        kind, video_id = path[0], path[1]

    if video_id and _VIDEO_ID_RE.match(video_id):
        return kind, video_id
    return None


# ── API 키 ───────────────────────────────────────────

def load_api_key(env_path: Path = Path(".env")) -> str | None:
    key = os.environ.get("YOUTUBE_API_KEY")
    if key:
        return key.strip()
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, ValueError):  # 파일 없음, 인코딩 문제(UTF-16 등)
        return None
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() == "YOUTUBE_API_KEY":
            return value.strip().strip("\"'") or None
    return None


# ── 카테고리 조회 ─────────────────────────────────────

@dataclass(frozen=True)
class VideoInfo:
    category: str      # 예: "Gaming" — 트래커에 붙일 때 바깥으로 내보낼 값은 이것뿐
    category_id: str
    title: str         # 확인용 출력에만 사용
    channel: str       # 확인용 출력에만 사용


def _normalize_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def _http_get_json(url: str, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:  # 메시지에 URL(=API 키)이 들어가지 않게 본문만 남긴다
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {body}") from None


class YouTubeCategoryLookup:
    def __init__(self, api_key: str | None = None, *,
                 fetch_json: Callable[[str, float], dict] = _http_get_json,
                 timeout: float = 3.0):
        self.api_key = api_key if api_key is not None else load_api_key()
        self._fetch = fetch_json
        self._timeout = timeout
        self._cache: dict[str, VideoInfo | None] = {}
        self._category_by_title: dict[str, str] = {}  # 백그라운드 미디어 제목 매칭용 (메모리에만)
        self.request_count = 0  # 할당량 확인용

    def lookup(self, video_id: str) -> VideoInfo | None:
        if not self.api_key:
            return None
        if video_id in self._cache:
            return self._cache[video_id]
        try:
            info = self._request(video_id)
        except Exception as e:  # 네트워크·키 오류는 캐시하지 않고 다음 슬롯에서 재시도
            log.warning("유튜브 카테고리 조회 실패: %s", e)
            return None
        self._cache[video_id] = info  # 삭제·비공개 영상(None)도 캐시
        if info is not None and info.title:
            self._category_by_title[_normalize_title(info.title)] = info.category
        return info

    def category_for_title(self, title: str | None) -> str | None:
        """앞 화면에서 조회한 적 있는 영상이면 제목으로 카테고리를 찾는다 (API 호출 없음)."""
        if not title:
            return None
        return self._category_by_title.get(_normalize_title(title))

    def _request(self, video_id: str) -> VideoInfo | None:
        query = urllib.parse.urlencode({"part": "snippet", "id": video_id, "key": self.api_key})
        self.request_count += 1
        data = self._fetch(f"{API_URL}?{query}", self._timeout)
        items = data.get("items") or []
        if not items:
            return None
        snippet = items[0].get("snippet") or {}
        category_id = str(snippet.get("categoryId") or "")
        if not category_id:
            return None
        return VideoInfo(
            category=CATEGORY_NAMES.get(category_id, f"Unknown({category_id})"),
            category_id=category_id,
            title=snippet.get("title", ""),
            channel=snippet.get("channelTitle", ""),
        )


# ── 확인용 실행 ──────────────────────────────────────

def _check_video_id(video_id: str) -> None:
    lookup = YouTubeCategoryLookup()
    if not lookup.api_key:
        print("API 키를 못 찾았습니다. 레포 루트 .env에 YOUTUBE_API_KEY=... 를 넣어 주세요.")
        return
    info = lookup.lookup(video_id)
    print(f"{video_id} → {info}" if info else f"{video_id} → 조회 실패 (위 경고 메시지 확인)")


def _watch(duration: float, interval: float = 5.0) -> None:
    from window_tracker.app_names import display_name, is_browser
    from window_tracker.domain_probe import read_address_bar, url_to_domain
    from window_tracker.foreground import foreground_window

    lookup = YouTubeCategoryLookup()
    if not lookup.api_key:
        print("API 키를 못 찾았습니다. 레포 루트 .env에 YOUTUBE_API_KEY=... 를 넣어 주세요.")
        return
    print(f"{duration:.0f}초 동안 {interval:.0f}초마다 확인합니다. 브라우저에서 유튜브 영상을 띄워 보세요.")
    start = time.time()
    while time.time() - start < duration:
        elapsed = time.time() - start
        hwnd, exe = foreground_window()
        if not hwnd or not is_browser(exe):
            line = f"{display_name(exe) if exe else None} (브라우저 아님)"
        else:
            text = read_address_bar(hwnd)
            video = extract_video(text)
            if text is None:
                line = f"{display_name(exe)} | 주소창 읽기 실패"
            elif video is None:
                line = f"{display_name(exe)} | 유튜브 영상 아님 (domain={url_to_domain(text)!r})"
            else:
                kind, video_id = video
                info = lookup.lookup(video_id)
                line = (f"{display_name(exe)} | {kind} | category={info.category!r} | {info.title[:40]}"
                        if info else f"{display_name(exe)} | {kind} | category=None (조회 실패)")
        print(f"[+{elapsed:5.1f}s] {line}")
        time.sleep(interval)
    print(f"API 요청 {lookup.request_count}회 (캐시 덕분에 같은 영상은 1회만)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="유튜브 카테고리 조회 확인")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video-id", help="영상 ID 하나로 API 키·응답 확인")
    group.add_argument("--watch", type=float, metavar="SECONDS", help="브라우저 창을 주기적으로 읽어 확인")
    args = parser.parse_args()
    if args.video_id:
        _check_video_id(args.video_id)
    else:
        _watch(args.watch)
