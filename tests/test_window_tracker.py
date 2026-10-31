"""window_tracker 테스트 (WIN-01~03, 스키마 v3) — 실제 시간·Windows API 없이 가짜 시계와 가짜 함수로 돌린다."""
import queue
import unittest

from core.slot_log import SlotLog
from mocks.mock_cv_slots import FULL_FOCUS, make_cv_messages
from schemas import WindowSlot, slot_index
from window_tracker.app_names import resolve_app_name
from window_tracker.domain_probe import url_to_domain
from window_tracker.foreground import NONE_STATE, ForegroundSampler, ForegroundState
from window_tracker.media_probe import MediaState, media_app_name
from window_tracker.tracker import (
    WindowTracker,
    build_window_slot,
    describe_samples,
    make_sample,
    media_on_screen,
)

T0 = 1_780_000_000.0


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def sample(t: float, app="VS Code", domain=None, cat=None, playing=None, background=False, bg_cat=None) -> dict:
    return {"timestamp": t, "app_name": app, "domain": domain, "video_category": cat,
            "media_playing": playing, "background_media": background, "background_category": bg_cat}


def run_tracker(sample_fn, *, clock_start=T0, max_samples=25) -> list[dict]:
    """가짜 시계로 트래커를 현재 스레드에서 실행하고 큐 메시지 목록을 돌려준다.

    sample_fn(clock, n, timestamp): n은 1부터 세는 조회 번호.
    max_samples번 조회한 뒤 다음 대기에서 종료 신호를 보내 남은 슬롯을 flush하게 한다.
    """
    q: queue.Queue = queue.Queue()
    clock = FakeClock(clock_start)
    count = {"n": 0}

    def fn(ts):
        count["n"] += 1
        return sample_fn(clock, count["n"], ts)

    def fake_wait(delay):
        if count["n"] >= max_samples:
            return True
        clock.now += delay
        return False

    WindowTracker(q, T0, sample_fn=fn, clock=clock, wait_fn=fake_wait).run()
    return [q.get_nowait() for _ in range(q.qsize())]


def plain(clock, n, ts):
    return sample(ts)


class TrackerTimingTest(unittest.TestCase):
    def test_slot_every_10s_and_flush_on_stop(self):
        msgs = run_tracker(plain, max_samples=25)
        self.assertEqual([m["slot"]["slot"] for m in msgs], [0, 1, 2])  # 마지막은 5초짜리
        self.assertEqual([m["slot"]["timestamp"] - T0 for m in msgs], [0, 10, 20])

    def test_message_matches_schema(self):
        msg = run_tracker(plain, max_samples=10)[0]
        self.assertEqual(set(msg), {"source", "slot"})
        self.assertEqual(msg["source"], "window")
        self.assertEqual(set(msg["slot"]), set(WindowSlot.__annotations__))
        self.assertEqual(msg["slot"]["slot"], slot_index(msg["slot"]["timestamp"], T0))

    def test_slow_sample_skips_ticks_without_duplicates(self):
        timestamps = []

        def slow_at_4th(clock, n, ts):
            timestamps.append(ts - T0)
            if n == 4:
                clock.now += 2.5
            return sample(ts)

        run_tracker(slow_at_4th, max_samples=12)
        self.assertEqual(timestamps[:9], [0, 1, 2, 3, 5.5, 6, 7, 8, 9])
        self.assertEqual(len(timestamps), len(set(timestamps)))

    def test_exception_records_none_and_continues(self):
        def flaky(clock, n, ts):
            if 1 <= n <= 6:
                raise OSError("창이 조회 직전에 닫힘")
            return sample(ts)

        with self.assertLogs("window_tracker.tracker", level="ERROR") as logs:
            slot = run_tracker(flaky, max_samples=10)[0]["slot"]
        self.assertEqual(len(logs.records), 6)
        self.assertIsNone(slot["app_name"])  # 10초 중 6초가 실패 → 최빈값이 None

    def test_late_start_samples_immediately_then_aligns(self):
        timestamps = []

        def record(clock, n, ts):
            timestamps.append(round(ts - T0, 1))
            return sample(ts)

        run_tracker(record, clock_start=T0 + 3.4, max_samples=3)
        self.assertEqual(timestamps, [3.4, 4.0, 5.0])

    def test_resume_from_current_slot_after_sleep(self):
        def sleep_once(clock, n, ts):
            if n == 2:
                clock.now += 300  # 노트북 덮었다 열기
            return sample(ts)

        msgs = run_tracker(sleep_once, max_samples=4)
        self.assertEqual([m["slot"]["slot"] for m in msgs], [0, 30])


class BuildSlotTest(unittest.TestCase):
    def test_short_visit_does_not_win_but_is_counted(self):
        """VS Code 4초 → 유튜브 3초 → VS Code 3초."""
        samples = ([sample(i) for i in range(4)]
                   + [sample(i, "Chrome", "youtube.com") for i in range(4, 7)]
                   + [sample(i) for i in range(7, 10)])
        slot = build_window_slot(0, samples)
        self.assertEqual((slot["app_name"], slot["domain"]), ("VS Code", None))
        self.assertEqual(slot["timestamp"], 0)
        self.assertEqual(describe_samples(samples), "VS Code 7s, Chrome(youtube.com) 3s")

    def test_mostly_youtube(self):
        samples = [sample(0)] * 3 + [sample(0, "Chrome", "youtube.com")] * 7
        self.assertEqual(build_window_slot(0, samples)["domain"], "youtube.com")

    def test_tie_goes_to_first_seen(self):
        samples = [sample(0, "Notion")] * 5 + [sample(0)] * 5
        self.assertEqual(build_window_slot(0, samples)["app_name"], "Notion")

    def test_video_category_of_representative_window(self):
        samples = ([sample(0, "Chrome", "youtube.com", "Gaming")] * 4
                   + [sample(0, "Chrome", "youtube.com", "Music")] * 3 + [sample(0)] * 3)
        slot = build_window_slot(0, samples)
        self.assertEqual((slot["domain"], slot["video_category"]), ("youtube.com", "Gaming"))

    def test_no_category_when_representative_is_not_youtube(self):
        samples = [sample(0)] * 7 + [sample(0, "Chrome", "youtube.com", "Gaming")] * 3
        self.assertIsNone(build_window_slot(0, samples)["video_category"])

    def test_background_media_seconds_and_category(self):
        samples = ([sample(0, playing=True, background=True, bg_cat="Music")] * 5
                   + [sample(0, playing=True, background=True, bg_cat="Gaming")] * 3
                   + [sample(0, "Chrome", "youtube.com", "Gaming", playing=True)] * 2)
        slot = build_window_slot(0, samples)
        self.assertEqual(slot["background_media_sec"], 8)
        self.assertEqual(slot["background_category"], "Music")
        self.assertIn("백그라운드 재생 8s[Music]", describe_samples(samples))

    def test_paused_media_is_zero_not_none(self):
        slot = build_window_slot(0, [sample(0, playing=False)] * 10)
        self.assertEqual(slot["background_media_sec"], 0)
        self.assertIsNone(slot["background_category"])

    def test_unmeasured_media_is_none(self):
        """미디어 감지를 끄거나 패키지가 없으면 측정 불가(None) — 감점하지 않는다."""
        slot = build_window_slot(0, [sample(0)] * 10)
        self.assertIsNone(slot["background_media_sec"])


class BackgroundMediaTest(unittest.TestCase):
    """VS Code를 앞에 두고 유튜브를 틀어 두는 경우 등."""

    CHROME_YT = ForegroundState("Chrome", "youtube.com", "Gaming", "롤 하이라이트 - YouTube - Chrome")
    CHROME_DOCS = ForegroundState("Chrome", "docs.python.org", None, "threading - Python - Chrome")
    VSCODE = ForegroundState("VS Code", title="tracker.py - focuspomo")
    PLAYING = MediaState(True, "Chrome", "롤 하이라이트")

    def test_on_screen_or_not(self):
        self.assertTrue(media_on_screen(self.CHROME_YT, self.PLAYING))
        self.assertFalse(media_on_screen(self.CHROME_DOCS, self.PLAYING))  # 같은 Chrome이지만 다른 탭
        self.assertFalse(media_on_screen(self.VSCODE, self.PLAYING))
        self.assertTrue(media_on_screen(self.CHROME_DOCS, MediaState(True, "Chrome", None)))  # 제목 모르면 안 셈

    def test_vscode_in_front_while_youtube_plays(self):
        class FakeYouTube:
            def category_for_title(self, title):
                return {"롤 하이라이트": "Gaming"}.get(title)

        s = make_sample(1.0, self.VSCODE, self.PLAYING, FakeYouTube())
        self.assertTrue(s["background_media"])
        self.assertEqual(s["background_category"], "Gaming")

    def test_paused_or_unmeasured_is_not_background(self):
        paused = make_sample(1.0, self.VSCODE, MediaState(False))
        unknown = make_sample(1.0, self.VSCODE, MediaState(None))
        self.assertFalse(paused["background_media"])
        self.assertFalse(unknown["background_media"])
        self.assertIsNone(unknown["media_playing"])

    def test_media_app_names(self):
        self.assertEqual(media_app_name("Chrome"), "Chrome")
        self.assertEqual(media_app_name("MSEdge"), "Edge")
        self.assertEqual(media_app_name("Spotify.exe"), "Spotify")
        self.assertEqual(media_app_name("308046B0AF4A39CB"), "Firefox")
        self.assertIsNone(media_app_name(None))


class SlotLogCompatibilityTest(unittest.TestCase):
    """김민서 core/slot_log.py가 창 메시지를 그대로 병합하는지."""

    def test_merge_with_cv_messages(self):
        def youtube_in_slot_1(clock, n, ts):
            in_slot_1 = slot_index(ts, T0) == 1
            return sample(ts, "Chrome", "youtube.com") if in_slot_1 else sample(ts)

        log = SlotLog(T0)
        for msg in make_cv_messages(T0, [FULL_FOCUS] * 3) + run_tracker(youtube_in_slot_1, max_samples=30):
            log.add(msg)
        merged = log.finalize()
        self.assertEqual([(r["slot"], r["app_name"], r["domain"]) for r in merged],
                         [(0, "VS Code", None), (1, "Chrome", "youtube.com"), (2, "VS Code", None)])


    def test_v3_fields_reach_merged_slot(self):
        def background_youtube(clock, n, ts):
            return sample(ts, playing=True, background=True, bg_cat="Gaming")

        log = SlotLog(T0)
        for msg in make_cv_messages(T0, [FULL_FOCUS]) + run_tracker(background_youtube, max_samples=10):
            log.add(msg)
        row = log.finalize()[0]
        self.assertEqual((row["background_media_sec"], row["background_category"]), (10, "Gaming"))

    def test_old_format_message_gets_defaults(self):
        """v2 형식(필드 4개) 메시지가 와도 새 필드는 기본값(None)으로 채워진다."""
        log = SlotLog(T0)
        log.add({"source": "window", "slot": {"slot": 0, "timestamp": T0, "app_name": "VS Code", "domain": None}})
        row = log.finalize()[0]
        self.assertEqual(row["app_name"], "VS Code")
        self.assertIsNone(row["video_category"])
        self.assertIsNone(row["background_media_sec"])


class ForegroundSamplerTest(unittest.TestCase):
    def test_heavy_lookup_only_when_window_changes(self):
        keys = iter([(1, "a"), (1, "a"), (1, "a"), (2, "b"), (2, "b"), (1, "a2"), (0, "")])
        resolved = []

        def resolve(hwnd, title):
            resolved.append(hwnd)
            return ForegroundState(f"app{hwnd}", title=title)

        sampler = ForegroundSampler(read_key=lambda: next(keys), resolve=resolve)
        states = [sampler.sample() for _ in range(7)]
        self.assertEqual(resolved, [1, 2, 1])  # 7초 중 3번만 (제목이 바뀐 a2 포함)
        self.assertEqual(states[2].app_name, "app1")
        self.assertIs(states[6], NONE_STATE)    # 창 없음은 조회 없이 None

    def test_failure_is_not_cached(self):
        calls = {"n": 0}

        def resolve(hwnd, title):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("일시적 실패")
            return ForegroundState("VS Code")

        sampler = ForegroundSampler(read_key=lambda: (1, "a"), resolve=resolve)
        with self.assertLogs("window_tracker.foreground", level="ERROR"):
            self.assertIs(sampler.sample(), NONE_STATE)
        self.assertEqual(sampler.sample().app_name, "VS Code")


class AppNameAndDomainTest(unittest.TestCase):
    def test_app_names(self):
        self.assertEqual(resolve_app_name("Code.exe", "Chrome_WidgetWin_1"), "VS Code")
        self.assertEqual(resolve_app_name("chrome.exe", "Chrome_WidgetWin_1"), "Chrome")
        self.assertEqual(resolve_app_name("Obsidian.exe", "x"), "Obsidian")
        self.assertEqual(resolve_app_name("explorer.exe", "CabinetWClass"), "File Explorer")

    def test_system_windows_are_none(self):
        self.assertIsNone(resolve_app_name("LockApp.exe", "x"))
        self.assertIsNone(resolve_app_name("explorer.exe", "Progman"))
        self.assertIsNone(resolve_app_name("explorer.exe", "XamlExplorerHostIslandWindow"))
        self.assertIsNone(resolve_app_name(None, "x"))

    def test_domain_only(self):
        self.assertEqual(url_to_domain("www.youtube.com/watch?v=abc"), "youtube.com")
        self.assertEqual(url_to_domain("https://github.com/org/repo?tab=1"), "github.com")
        self.assertIsNone(url_to_domain("chrome://newtab"))
        self.assertIsNone(url_to_domain("파이썬 스레드"))
        self.assertIsNone(url_to_domain(None))


if __name__ == "__main__":
    unittest.main()
