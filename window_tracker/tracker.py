"""활성 창 추적 스레드 (WIN-01~03) — 개발계획서 2.2, 스키마 명세 v3 WindowSlot.

1초마다 앞에 있는 창과 미디어 재생 상태를 확인하고, 10초 슬롯마다 압축해 slot_queue에 넣는다:
    {"source": "window", "slot": WindowSlot}
    WindowSlot = slot, timestamp, app_name, domain, video_category, background_media_sec, background_category

- app_name/domain: 슬롯의 대표 창 — 점수 명세 2.1 우선순위 압축 (build_window_slot):
  1. 앞 화면에서 규칙상 딴짓인 초가 DISTRACTION_MIN_SEC 이상 → 그 창들 중 가장 오래 앞에 있던 것,
     rule_distraction = "foreground"
  2. 틀어 두기도 딴짓인 유튜브 영상이 백그라운드로 BG_DISTRACTION_MIN_SEC 이상 재생 → 조회에 성공한 창 중
     가장 오래 앞에 있던 것, rule_distraction = "background"
  3. 그 외 → 조회에 성공한 창 중 가장 오래 앞에 있던 것 (전부 실패면 None), rule_distraction = None
  규칙상 딴짓인 초: 딴짓 사이트(하위 도메인 포함, "instagram.com" → "m.instagram.com"), 또는 유튜브 영상 분류표
  (config/window_rules.py VIDEO_POLICIES)에서 앞 화면 딴짓인 영상. 분류표의 유예(grace_sec)는 같은 영상 분류를
  앞 화면에서 이어서 본 시간(video_watch_sec, 슬롯을 넘어 이어 셈)으로 판정한다 (window_tracker/video_rules.py).
- video_category: 그 창이 유튜브 영상이면 영상 분류 — 쇼츠면 "Shorts", 제목이 공부 배경음이면 "BGM",
  그 외 YouTube 카테고리 (YouTube Data API, 같은 영상은 캐시).
- video_run / video_run_sec (v5): 이 슬롯에서 앞 화면에 띄운 유튜브 영상 묶음(같은 분류를 이어서 본 것)의 번호와
  이 슬롯 끝까지 이어서 본 시간. 점수 모듈이 세션 종료 후 묶음 전체 길이로 유예 구간까지 소급해 정한다
  (유예를 넘긴 묶음은 전체 딴짓, 30초 이하로 끝난 묶음은 집중 — window_tracker/video_rules.video_run_label).
- background_media_sec: 미디어가 재생 중인데 그 화면이 앞에 없던 초 = '틀어 두고 다른 일'.
  같은 브라우저라도 다른 탭을 보고 있으면 백그라운드로 센다 (미디어 제목이 창 제목에 없으면).
- background_category: 백그라운드 영상의 영상 분류. 카테고리는 앞 화면에서 조회한 적 있는 영상만 제목으로 찾고,
  제목이 공부 배경음이면 카테고리와 상관없이 "BGM"이다.
  유튜브 조회 캐시는 앱 수명 동안 공유할 수 있다 (youtube 인자) — 이전 세션에 앞 화면에서 본 영상도 매칭된다.
  분류를 모르는 영상(한 번도 앞 화면에 띄우지 않은 영상)은 규칙에 걸리지 않는다 (불확실하면 감점하지 않음).
- 창 핸들·제목은 매초 읽지만, 주소창·API 같은 무거운 조회는 창이 바뀔 때만 한다 (foreground.py).
- 창 제목·영상 제목·영상 ID는 판정에만 쓰고 저장하거나 내보내지 않는다.
- 창 메시지는 슬롯이 끝난 뒤 도착한다 (CV와 같은 시점). 일시정지 중에도 멈추지 않는다 (명세 7-1).

기능 켜기 (기본은 모두 꺼짐 — 꺼진 기능의 필드는 None):
    use_domain_probe  브라우저 도메인 (pywinauto)
    use_youtube       유튜브 카테고리 (도메인 포함, .env의 YOUTUBE_API_KEY 필요)
    use_media         백그라운드 재생 (winrt-Windows.Media.Control 필요)

인터페이스는 INT-02 합의(SessionWorker)에 맞춰 바꿀 예정. 지금은:
    tracker = WindowTracker(slot_queue, session_start, use_youtube=True, use_media=True)
    tracker.start(); ...; tracker.stop(); tracker.join(timeout=2)   # stop 시 진행 중 슬롯 바로 전송

단독 실행 (레포 루트, venv 활성화 상태):
    python -m window_tracker.tracker --duration 60
    python -m window_tracker.tracker --duration 60 --youtube --media
"""
from __future__ import annotations

import argparse
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import TypedDict

from config.aggregation import BG_DISTRACTION_MIN_SEC, DISTRACTION_MIN_SEC
from config.window_rules import DISTRACTION_DOMAIN_GROUPS, DISTRACTION_DOMAINS
from schemas import RuleDistraction, WindowSlot, WindowSlotMessage, slot_index
from window_tracker import video_rules
from window_tracker.foreground import ForegroundSampler, ForegroundState
from window_tracker.media_probe import MediaProbe, MediaState
from window_tracker.youtube_probe import YouTubeCategoryLookup

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_SEC = 1  # SLOT_SECONDS의 약수. 팀 합의 후 config/aggregation.py로 옮길 후보


class WindowSample(TypedDict):
    """1초 조회 결과. 스레드 내부에서만 쓰고 큐로 보내지 않는다."""
    timestamp: float
    app_name: str | None
    domain: str | None
    video_category: str | None          # 앞 화면 유튜브 영상의 영상 분류
    media_playing: bool | None          # None = 측정 불가 (기능 꺼짐·패키지 없음 등)
    background_media: bool              # 재생 중인 미디어가 앞 화면에 없음
    background_category: str | None     # 백그라운드 영상의 영상 분류 (모르면 None)
    video_watch_sec: float              # 이번 초 전까지 같은 영상 분류를 앞 화면에서 이어서 본 시간 (트래커가 채움)
    video_run: int | None               # 이번 초가 속한 영상 묶음 번호, 영상이 아니면 None (트래커가 채움)


SampleFn = Callable[[float], WindowSample]
_NO_MEDIA = MediaState(playing=None)


# ── 1초 샘플 (순수 함수) ─────────────────────────────

def media_on_screen(fg: ForegroundState, media: MediaState) -> bool:
    """재생 중인 미디어가 지금 앞 화면에 보이는지.

    같은 앱이 앞에 있고, 미디어 제목이 창 제목(= 브라우저의 현재 탭 제목)에 들어 있으면 보이는 것으로 본다.
    미디어 제목을 모르면 같은 앱이기만 해도 보이는 것으로 본다 (불확실하면 감점 쪽으로 세지 않음).
    """
    if media.app is None or fg.app_name != media.app:
        return False
    if media.title:
        return media.title in fg.title
    return True


def background_video_class(media: MediaState, youtube: YouTubeCategoryLookup | None) -> str | None:
    """백그라운드 미디어의 영상 분류. 카테고리는 앞 화면에서 조회한 적 있는 영상만 알고, 배경음 제목은 제목만으로 안다."""
    category = youtube.category_for_title(media.title) if youtube is not None else None
    return video_rules.classify_video(None, category, media.title)


def make_sample(timestamp: float, fg: ForegroundState, media: MediaState = _NO_MEDIA,
                youtube: YouTubeCategoryLookup | None = None) -> WindowSample:
    """1초 조회 결과. video_watch_sec·video_run은 슬롯을 넘어 이어 세야 해서 트래커 루프가 채운다 (여기서는 기본값)."""
    background = bool(media.playing) and not media_on_screen(fg, media)
    return {
        "timestamp": timestamp,
        "app_name": fg.app_name,
        "domain": fg.domain,
        "video_category": fg.video_category,
        "media_playing": media.playing,
        "background_media": background,
        "background_category": background_video_class(media, youtube) if background else None,
        "video_watch_sec": 0,
        "video_run": None,
    }


def empty_sample(timestamp: float) -> WindowSample:
    return make_sample(timestamp, ForegroundState(None))


# ── 슬롯 압축 (순수 함수) ────────────────────────────

def _most_common(values: list) -> object | None:
    """가장 많이 나온 값, 동률이면 먼저 나온 값. 비어 있으면 None."""
    counts: dict = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0] if counts else None


def domain_suffixes(domain: str) -> list[str]:
    """도메인과 상위 도메인들 (최상위 한 단계는 뺌). "m.comic.naver.com" → [그대로, "comic.naver.com", "naver.com"]."""
    labels = domain.lower().rstrip(".").split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


def is_distraction_domain(domain: str | None) -> bool:
    """딴짓 사이트인지. 목록의 도메인과 그 하위 도메인이 걸린다 (config/window_rules.py)."""
    if not domain:
        return False
    return any(d in DISTRACTION_DOMAINS for d in domain_suffixes(domain))


def distraction_domain_group(domain: str | None) -> str | None:
    """걸린 딴짓 사이트 그룹 키 (예: "sns"). 디버깅·문서용, 판정에는 is_distraction_domain을 쓴다."""
    if not domain:
        return None
    for d in domain_suffixes(domain):
        for group, domains in DISTRACTION_DOMAIN_GROUPS.items():
            if d in domains:
                return group
    return None


def is_rule_distraction(domain: str | None, video_category: str | None, watched_sec: float = 0) -> bool:
    """앞 화면 창이 이번 초에 로컬 규칙상 딴짓인지 (점수 명세 2.1). 목록은 config/window_rules.py.

    딴짓 사이트이거나, 유튜브 영상 분류표에서 앞 화면 딴짓인 영상 (유예 중이면 아님 — watched_sec).
    """
    return is_distraction_domain(domain) or video_rules.is_foreground_distraction(video_category, watched_sec)


def is_rule_distraction_sample(sample: WindowSample) -> bool:
    return is_rule_distraction(sample["domain"], sample["video_category"], sample["video_watch_sec"])


def is_background_distraction(sample: WindowSample) -> bool:
    """틀어 두기도 딴짓인 영상이 앞 화면에 없이 재생 중인 초인지 (점수 명세 2.1 2순위). 분류를 모르면 아니다."""
    return sample["background_media"] and video_rules.is_background_distraction(sample["background_category"])


def slot_video_run(samples: list[WindowSample], app_name, domain, video_category) -> tuple[int | None, int | None]:
    """슬롯의 (영상 묶음 번호, 그 묶음을 이 슬롯 끝까지 이어서 본 시간).

    대표 창이 영상이면 그 영상의 묶음, 아니면 슬롯 안에서 마지막으로 앞 화면에 띄운 영상의 묶음
    (대표가 VS Code인 슬롯에 걸친 묶음도 전체 길이를 놓치지 않게). 영상이 없으면 (None, None).
    """
    videos = [s for s in samples if s["video_run"] is not None]
    if video_category is not None:
        videos = [s for s in videos if (s["app_name"], s["domain"], s["video_category"]) == (app_name, domain,
                                                                                                video_category)]
    if not videos:
        return None, None
    last = videos[-1]
    return last["video_run"], int(round(last["video_watch_sec"] + SAMPLE_INTERVAL_SEC))


def build_window_slot(slot: int, samples: list[WindowSample]) -> WindowSlot:
    """1초 조회 묶음 → WindowSlot. samples는 비어 있으면 안 된다 (조회가 있는 슬롯만 보냄).

    대표 창과 rule_distraction (점수 명세 2.1 우선순위 압축, 위에서부터 처음 해당하는 것):
    1. 앞 화면에서 규칙상 딴짓인 초가 DISTRACTION_MIN_SEC 이상 → 그 창들 중 최빈값, "foreground"
       (유튜브 영상은 분류표의 유예를 지난 초만 — 각 초의 video_watch_sec로 판정)
    2. 틀어 두기도 딴짓인 영상의 백그라운드 재생이 BG_DISTRACTION_MIN_SEC 이상 → 조회에 성공한 창 중 최빈값, "background"
    3. 그 외 → 조회에 성공한 창 중 최빈값 (전부 실패면 None), None
    앞 화면에 보이는 영상은 make_sample에서 백그라운드로 세지 않으므로 두 순위에 같은 영상이 두 번 들어가지 않는다.
    """
    fg_hits = [s for s in samples if is_rule_distraction_sample(s)]
    bg_hits = [s for s in samples if is_background_distraction(s)]
    measured_fg = [s for s in samples if s["app_name"] is not None] or samples
    rule: RuleDistraction | None
    if len(fg_hits) * SAMPLE_INTERVAL_SEC >= DISTRACTION_MIN_SEC:
        pool, rule = fg_hits, "foreground"
    elif len(bg_hits) * SAMPLE_INTERVAL_SEC >= BG_DISTRACTION_MIN_SEC:
        pool, rule = measured_fg, "background"
    else:
        pool, rule = measured_fg, None
    app_name, domain = _most_common([(s["app_name"], s["domain"]) for s in pool])
    video_category = _most_common([s["video_category"] for s in pool
                                   if (s["app_name"], s["domain"]) == (app_name, domain) and s["video_category"]])
    measured = [s for s in samples if s["media_playing"] is not None]
    background = [s for s in measured if s["background_media"]]
    # 백그라운드 딴짓이면 리포트의 딴짓 항목이 딴짓 카테고리로 묶이도록 그 초들에서 고른다
    category_source = bg_hits if rule == "background" else background
    video_run, video_run_sec = slot_video_run(samples, app_name, domain, video_category)
    return {
        "slot": slot,
        "timestamp": samples[0]["timestamp"],
        "app_name": app_name,
        "domain": domain,
        "video_category": video_category,
        "background_media_sec": len(background) * SAMPLE_INTERVAL_SEC if measured else None,
        "background_category": _most_common([s["background_category"] for s in category_source
                                             if s["background_category"]]),
        "rule_distraction": rule,
        "video_run": video_run,
        "video_run_sec": video_run_sec,
    }


def describe_samples(samples: list[WindowSample]) -> str:
    """디버깅용 슬롯 구성. 예: "VS Code 7s, Chrome(youtube.com)[Gaming]* 3s | 백그라운드 재생 5s[Music]".

    *는 규칙상 딴짓인 초가 있던 창. 유튜브 영상은 슬롯 끝까지 이어서 본 시간을 붙인다 (예: "[Music]~45s").
    """
    counts: dict = {}
    hits: set = set()
    watched: dict = {}
    for s in samples:
        key = (s["app_name"], s["domain"], s["video_category"])
        counts[key] = counts.get(key, 0) + 1
        if is_rule_distraction_sample(s):
            hits.add(key)
        if s["video_category"]:
            watched[key] = s["video_watch_sec"] + SAMPLE_INTERVAL_SEC
    parts = [f"{a or '-'}{f'({d})' if d else ''}{f'[{c}]' if c else ''}"
             f"{f'~{watched[(a, d, c)]:.0f}s' if (a, d, c) in watched else ''}"
             f"{'*' if (a, d, c) in hits else ''} {n}s"
             for (a, d, c), n in sorted(counts.items(), key=lambda kv: -kv[1])]
    text = ", ".join(parts)
    background = [s for s in samples if s["background_media"]]
    if background:
        category = _most_common([s["background_category"] for s in background if s["background_category"]])
        text += f" | 백그라운드 재생 {len(background)}s" + (f"[{category}]" if category else "")
    return text


# ── 스레드 ───────────────────────────────────────────

class WindowTracker(threading.Thread):
    """session_start + k초마다 조회하고, 슬롯이 바뀌면 이전 슬롯을 압축해 push한다.

    youtube: 세션끼리 공유할 유튜브 카테고리 조회 객체 (use_youtube일 때만 씀). 같은 영상은 API를 다시 부르지 않고,
        이전 세션에 앞 화면에서 본 영상이 백그라운드로 재생돼도 카테고리를 안다. 순수 파이썬 객체라 스레드를 넘겨도 된다.
    영상을 앞 화면에서 이어서 본 시간(video_watch)은 세션 안에서만 센다 (세션마다 트래커를 새로 만든다).
    sample_fn / clock / wait_fn은 테스트용 주입 지점이다.
    """

    def __init__(self, slot_queue: queue.Queue, session_start: float, *,
                 use_domain_probe: bool = False, use_youtube: bool = False, use_media: bool = False,
                 youtube: YouTubeCategoryLookup | None = None,
                 sample_fn: SampleFn | None = None,
                 clock: Callable[[], float] = time.time,
                 wait_fn: Callable[[float], bool] | None = None):
        super().__init__(name="WindowTracker", daemon=True)
        self.slot_queue = slot_queue
        self.session_start = session_start
        self.use_youtube = use_youtube
        self.use_domain_probe = use_domain_probe or use_youtube
        self.use_media = use_media
        self._sample_fn = sample_fn
        self._clock = clock
        self._stop_event = threading.Event()
        self._wait = wait_fn or self._stop_event.wait  # True를 돌려주면 종료 신호
        self.sampler: ForegroundSampler | None = None
        self._shared_youtube = youtube  # 앱 수명 캐시 (main.py가 세션마다 같은 것을 넘긴다). 없으면 세션마다 새로 만든다
        self.youtube: YouTubeCategoryLookup | None = None
        self.video_watch = video_rules.VideoWatchRun(sample_interval=SAMPLE_INTERVAL_SEC)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        uses_com = self._sample_fn is None and (self.use_domain_probe or self.use_media)
        com = _init_com() if uses_com else None
        media = MediaProbe() if self._sample_fn is None and self.use_media else None
        try:
            self._loop(self._sample_fn or self._make_os_sample_fn(media))
        finally:
            if media is not None:
                media.close()
            if com is not None:
                com.CoUninitialize()

    def _make_os_sample_fn(self, media: MediaProbe | None) -> SampleFn:
        # UI Automation·WinRT 객체는 스레드 단위라 이 스레드 안에서 만든다
        if self.use_youtube:
            self.youtube = self._shared_youtube if self._shared_youtube is not None else YouTubeCategoryLookup()
        if self.youtube is not None and not self.youtube.api_key:
            logger.warning("YOUTUBE_API_KEY가 없어 유튜브 카테고리는 None으로 기록합니다 (.env 확인)")
        self.sampler = ForegroundSampler(use_domain_probe=self.use_domain_probe, youtube=self.youtube)

        def sample(timestamp: float) -> WindowSample:
            fg = self.sampler.sample()
            state = media.sample() if media is not None else _NO_MEDIA
            return make_sample(timestamp, fg, state, self.youtube)

        return sample

    def _loop(self, sample: SampleFn) -> None:
        i = 0
        current_slot: int | None = None
        buffer: list[WindowSample] = []
        try:
            while not self._stop_event.is_set():
                target = self.session_start + SAMPLE_INTERVAL_SEC * i
                while (delay := target - self._clock()) > 0:
                    if self._wait(delay):
                        return
                timestamp = self._clock()
                slot = slot_index(timestamp, self.session_start)
                if current_slot is not None and slot != current_slot:
                    self._flush(current_slot, buffer)
                    buffer = []
                current_slot = slot
                row = self._safe_sample(sample, timestamp)
                # 이어서 본 시간은 슬롯을 넘어 세야 하므로 여기서 채운다 (조회 함수가 같은 dict를 돌려줘도 안전하게 복사)
                watched = self.video_watch.update(timestamp, row["video_category"])
                run = self.video_watch.run_id if row["video_category"] is not None else None
                buffer.append({**row, "video_watch_sec": watched, "video_run": run})
                # 조회가 오래 걸렸으면 지난 틱은 건너뛰고 다음 틱으로 (몰아서 조회하지 않음)
                i = int((timestamp - self.session_start) // SAMPLE_INTERVAL_SEC) + 1
        finally:
            if buffer:  # 세션 종료·중간 종료 시 남은 초만으로 마지막 슬롯 전송
                self._flush(current_slot, buffer)

    def _flush(self, slot: int, samples: list[WindowSample]) -> None:
        message: WindowSlotMessage = {"source": "window", "slot": build_window_slot(slot, samples)}
        logger.debug("slot %d 구성: %s", slot, describe_samples(samples))
        self.slot_queue.put(message)

    def _safe_sample(self, sample: SampleFn, timestamp: float) -> WindowSample:
        try:
            return sample(timestamp)
        except Exception:
            logger.exception("활성 창 조회 실패 — 이번 초는 측정 불가로 기록")
            return empty_sample(timestamp)


def _init_com():
    """주소창(UI Automation)·미디어(WinRT)는 COM을 쓰므로 이 스레드를 MTA로 초기화한다."""
    try:
        import pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        return pythoncom
    except Exception:
        return None


# ── 단독 실행 데모 ───────────────────────────────────

def _demo(duration: float, use_domain: bool, use_youtube: bool, use_media: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.setLevel(logging.DEBUG)  # 슬롯 구성(창별 초)도 출력
    q: queue.Queue = queue.Queue()
    start = time.time()
    tracker = WindowTracker(q, start, use_domain_probe=use_domain, use_youtube=use_youtube,
                            use_media=use_media)
    tracker.start()
    print(f"{duration:.0f}초 동안 매초 확인하고 10초마다 슬롯을 출력합니다. 창을 바꿔 가며 확인하세요 (Ctrl+C 중단).")

    def show(msg: WindowSlotMessage) -> None:
        s = msg["slot"]
        print(f"→ slot {s['slot']:>3} | +{s['timestamp'] - start:5.1f}s | app_name={s['app_name']!r} "
              f"domain={s['domain']!r} video_category={s['video_category']!r} "
              f"background_media_sec={s['background_media_sec']!r} background_category={s['background_category']!r} "
              f"rule_distraction={s['rule_distraction']!r} video_run={s['video_run']!r}/{s['video_run_sec']!r}s")

    try:
        while time.time() - start < duration:
            try:
                show(q.get(timeout=0.5))
            except queue.Empty:
                continue
    except KeyboardInterrupt:
        pass
    finally:
        tracker.stop()
        tracker.join(timeout=3)
        while not q.empty():  # stop 때 보낸 마지막 슬롯
            show(q.get())
        if tracker.sampler is not None:
            extra = f", 유튜브 API 요청 {tracker.youtube.request_count}회" if tracker.youtube else ""
            print(f"무거운 창 조회 {tracker.sampler.resolve_count}회 (창이 바뀔 때만){extra}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="활성 창 추적 단독 실행")
    parser.add_argument("--duration", type=float, default=60, help="실행 시간(초)")
    parser.add_argument("--domain", action="store_true", help="브라우저 도메인 추출")
    parser.add_argument("--youtube", action="store_true", help="유튜브 카테고리 (도메인 포함, .env 키 필요)")
    parser.add_argument("--media", action="store_true", help="백그라운드 재생 감지")
    args = parser.parse_args()
    _demo(args.duration, args.domain, args.youtube, args.media)
