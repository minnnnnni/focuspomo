"""활성 창 추적 스레드 (WIN-01~03) — 개발계획서 2.2, 스키마 명세 v3 WindowSlot.

1초마다 앞에 있는 창과 미디어 재생 상태를 확인하고, 10초 슬롯마다 압축해 slot_queue에 넣는다:
    {"source": "window", "slot": WindowSlot}
    WindowSlot = slot, timestamp, app_name, domain, video_category, background_media_sec, background_category

- app_name/domain: 슬롯에서 가장 오래 앞에 있던 창 (계획서 2.2 '슬롯 내 여러 번 조회 후 최빈값'을 1초 간격으로).
- video_category: 그 창이 유튜브 영상이면 카테고리 (YouTube Data API, 같은 영상은 캐시).
- background_media_sec: 미디어가 재생 중인데 그 화면이 앞에 없던 초 = '틀어 두고 다른 일'.
  같은 브라우저라도 다른 탭을 보고 있으면 백그라운드로 센다 (미디어 제목이 창 제목에 없으면).
- background_category: 백그라운드 영상이 앞 화면에서 조회한 적 있는 영상이면 그 카테고리 (제목으로 매칭).
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

from schemas import WindowSlot, WindowSlotMessage, slot_index
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
    video_category: str | None
    media_playing: bool | None          # None = 측정 불가 (기능 꺼짐·패키지 없음 등)
    background_media: bool              # 재생 중인 미디어가 앞 화면에 없음
    background_category: str | None


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


def make_sample(timestamp: float, fg: ForegroundState, media: MediaState = _NO_MEDIA,
                youtube: YouTubeCategoryLookup | None = None) -> WindowSample:
    background = bool(media.playing) and not media_on_screen(fg, media)
    return {
        "timestamp": timestamp,
        "app_name": fg.app_name,
        "domain": fg.domain,
        "video_category": fg.video_category,
        "media_playing": media.playing,
        "background_media": background,
        "background_category": youtube.category_for_title(media.title) if (background and youtube) else None,
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


def build_window_slot(slot: int, samples: list[WindowSample]) -> WindowSlot:
    """1초 조회 묶음 → WindowSlot. samples는 비어 있으면 안 된다 (조회가 있는 슬롯만 보냄)."""
    app_name, domain = _most_common([(s["app_name"], s["domain"]) for s in samples])
    video_category = _most_common([s["video_category"] for s in samples
                                   if (s["app_name"], s["domain"]) == (app_name, domain) and s["video_category"]])
    measured = [s for s in samples if s["media_playing"] is not None]
    background = [s for s in measured if s["background_media"]]
    return {
        "slot": slot,
        "timestamp": samples[0]["timestamp"],
        "app_name": app_name,
        "domain": domain,
        "video_category": video_category,
        "background_media_sec": len(background) * SAMPLE_INTERVAL_SEC if measured else None,
        "background_category": _most_common([s["background_category"] for s in background
                                             if s["background_category"]]),
    }


def describe_samples(samples: list[WindowSample]) -> str:
    """디버깅용 슬롯 구성. 예: "VS Code 7s, Chrome(youtube.com)[Gaming] 3s | 백그라운드 재생 5s[Music]"."""
    counts: dict = {}
    for s in samples:
        key = (s["app_name"], s["domain"], s["video_category"])
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{a or '-'}{f'({d})' if d else ''}{f'[{c}]' if c else ''} {n}s"
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

    sample_fn / clock / wait_fn은 테스트용 주입 지점이다.
    """

    def __init__(self, slot_queue: queue.Queue, session_start: float, *,
                 use_domain_probe: bool = False, use_youtube: bool = False, use_media: bool = False,
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
        self.youtube: YouTubeCategoryLookup | None = None

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
        self.youtube = YouTubeCategoryLookup() if self.use_youtube else None
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
                buffer.append(self._safe_sample(sample, timestamp))
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
              f"background_media_sec={s['background_media_sec']!r} background_category={s['background_category']!r}")

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
