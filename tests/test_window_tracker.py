"""window_tracker 테스트 (WIN-01~03, 스키마 v3) — 실제 시간·Windows API 없이 가짜 시계와 가짜 함수로 돌린다."""
import queue
import unittest
from unittest.mock import patch

from config.aggregation import BG_DISTRACTION_MIN_SEC, DISTRACTION_MIN_SEC
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
    distraction_domain_group,
    is_distraction_domain,
    make_sample,
    media_on_screen,
)
from window_tracker.youtube_probe import YouTubeCategoryLookup

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
        # 10초 중 6초가 실패해도 실제 창이 있었으면 그 창으로 대표한다 (점수 명세 2.1 3순위)
        self.assertEqual(slot["app_name"], "VS Code")

    def test_all_failed_is_none(self):
        def always_fail(clock, n, ts):
            raise OSError("잠금 화면")

        with self.assertLogs("window_tracker.tracker", level="ERROR"):
            slot = run_tracker(always_fail, max_samples=10)[0]["slot"]
        self.assertIsNone(slot["app_name"])

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
        samples = [sample(0)] * 7 + [sample(0, "Chrome", "youtube.com", "Education")] * 3
        self.assertIsNone(build_window_slot(0, samples)["video_category"])


class RuleDistractionTest(unittest.TestCase):
    """점수 명세 2.1 우선순위 압축 (WIN-04): 앞 화면 딴짓 > 백그라운드 딴짓 > 가장 오래 앞에 있던 창."""

    GAME = ("Chrome", "youtube.com", "Gaming")

    def test_short_rule_distraction_wins_over_longest_window(self):
        """VS Code 6초 + 유튜브 게임 4초 → 게임이 대표, foreground."""
        slot = build_window_slot(0, [sample(0)] * 6 + [sample(0, *self.GAME)] * 4)
        self.assertEqual((slot["app_name"], slot["domain"], slot["video_category"]), self.GAME)
        self.assertEqual(slot["rule_distraction"], "foreground")

    def test_threshold_is_inclusive(self):
        """정확히 DISTRACTION_MIN_SEC(3초)면 딴짓, 2초면 아님."""
        at = build_window_slot(0, [sample(0)] * 7 + [sample(0, *self.GAME)] * DISTRACTION_MIN_SEC)
        below = build_window_slot(0, [sample(0)] * 8 + [sample(0, *self.GAME)] * (DISTRACTION_MIN_SEC - 1))
        self.assertEqual(at["rule_distraction"], "foreground")
        self.assertIsNone(below["rule_distraction"])
        self.assertEqual((below["app_name"], below["video_category"]), ("VS Code", None))

    def test_seconds_add_up_across_rule_windows(self):
        """게임 2초 + 코미디 2초 = 규칙상 딴짓 4초. 대표는 그중 가장 오래 앞에 있던 창."""
        samples = ([sample(0)] * 6 + [sample(0, *self.GAME)] * 2
                   + [sample(0, "Chrome", "youtube.com", "Comedy")] * 2)
        slot = build_window_slot(0, samples)
        self.assertEqual(slot["rule_distraction"], "foreground")
        self.assertEqual(slot["video_category"], "Gaming")  # 동률이면 먼저 나온 것

    def test_non_rule_category_is_left_to_llm(self):
        """Music·Education 같은 카테고리는 규칙에 없어 None (LLM이 분류)."""
        slot = build_window_slot(0, [sample(0, "Chrome", "youtube.com", "Music")] * 10)
        self.assertIsNone(slot["rule_distraction"])
        self.assertEqual(slot["video_category"], "Music")

    def test_distraction_domain(self):
        with patch("window_tracker.tracker.DISTRACTION_DOMAINS", frozenset({"instagram.com"})):
            slot = build_window_slot(0, [sample(0)] * 7 + [sample(0, "Chrome", "instagram.com")] * 3)
        self.assertEqual((slot["domain"], slot["rule_distraction"]), ("instagram.com", "foreground"))

    def test_distraction_subdomain(self):
        """목록의 도메인은 하위 도메인까지 걸린다 (실제 config 목록)."""
        slot = build_window_slot(0, [sample(0)] * 7 + [sample(0, "Chrome", "m.instagram.com")] * 3)
        self.assertEqual((slot["domain"], slot["rule_distraction"]), ("m.instagram.com", "foreground"))

    def test_study_site_is_left_to_llm(self):
        slot = build_window_slot(0, [sample(0, "Chrome", "docs.python.org")] * 10)
        self.assertIsNone(slot["rule_distraction"])


    def test_reaches_merged_slot(self):
        def game_in_slot_0(clock, n, ts):
            return sample(ts, *self.GAME) if slot_index(ts, T0) == 0 else sample(ts)

        log = SlotLog(T0)
        for msg in make_cv_messages(T0, [FULL_FOCUS] * 2) + run_tracker(game_in_slot_0, max_samples=20):
            log.add(msg)
        self.assertEqual([r["rule_distraction"] for r in log.finalize()], ["foreground", None])

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


class BackgroundRuleTest(unittest.TestCase):
    """점수 명세 2.1 2순위: 딴짓 카테고리 영상을 틀어 두고 다른 창을 보는 경우."""

    @staticmethod
    def bg(cat="Gaming", app="VS Code"):
        return sample(0, app, playing=True, background=True, bg_cat=cat)

    def test_vscode_in_front_while_game_video_plays(self):
        """VS Code 앞 + 게임 영상 백그라운드 10초 → 대표는 VS Code, rule_distraction = background."""
        slot = build_window_slot(0, [self.bg()] * 10)
        self.assertEqual(slot["rule_distraction"], "background")
        self.assertEqual((slot["app_name"], slot["background_media_sec"], slot["background_category"]),
                         ("VS Code", 10, "Gaming"))

    def test_threshold_is_inclusive(self):
        """정확히 BG_DISTRACTION_MIN_SEC(5초)면 딴짓, 4초면 아님."""
        at = build_window_slot(0, [self.bg()] * BG_DISTRACTION_MIN_SEC + [sample(0)] * 5)
        below = build_window_slot(0, [self.bg()] * (BG_DISTRACTION_MIN_SEC - 1) + [sample(0)] * 6)
        self.assertEqual(at["rule_distraction"], "background")
        self.assertIsNone(below["rule_distraction"])

    def test_music_or_unknown_category_is_not_distraction(self):
        """공부하며 듣는 음악, 카테고리를 모르는 영상은 감점하지 않는다."""
        music = build_window_slot(0, [self.bg("Music")] * 10)
        unknown = build_window_slot(0, [self.bg(None)] * 10)
        self.assertIsNone(music["rule_distraction"])
        self.assertEqual(music["background_category"], "Music")
        self.assertIsNone(unknown["rule_distraction"])

    def test_foreground_beats_background(self):
        """앞 화면 딴짓이 3초 이상이면 백그라운드 딴짓이 더 길어도 foreground 하나만."""
        samples = [sample(0, "Chrome", "youtube.com", "Gaming")] * 3 + [self.bg()] * 7
        slot = build_window_slot(0, samples)
        self.assertEqual(slot["rule_distraction"], "foreground")
        self.assertEqual((slot["app_name"], slot["video_category"]), ("Chrome", "Gaming"))

    def test_category_comes_from_distraction_seconds(self):
        """음악 5초가 먼저 나와도 백그라운드 딴짓 슬롯의 카테고리는 딴짓 카테고리로 남긴다 (리포트 딴짓 항목)."""
        slot = build_window_slot(0, [self.bg("Music")] * 5 + [self.bg("Entertainment")] * 5)
        self.assertEqual((slot["rule_distraction"], slot["background_category"]), ("background", "Entertainment"))
        quiet = build_window_slot(0, [self.bg("Music")] * 5 + [sample(0)] * 5)
        self.assertEqual((quiet["rule_distraction"], quiet["background_category"]), (None, "Music"))

    def test_representative_skips_failed_lookups(self):
        """잠금 화면 등으로 조회가 실패한 초가 많아도 실제 창으로 대표한다."""
        slot = build_window_slot(0, [self.bg(app=None)] * 6 + [self.bg()] * 4)
        self.assertEqual((slot["app_name"], slot["rule_distraction"]), ("VS Code", "background"))

    def test_reaches_score_as_distraction(self):
        """병합 슬롯 → 점수 모듈에서 앞 화면이 작업이어도 딴짓 태그."""
        from scoring.focus_score import score_session

        def background_game(clock, n, ts):
            return sample(ts, playing=True, background=True, bg_cat="Gaming")

        log = SlotLog(T0)
        for msg in make_cv_messages(T0, [FULL_FOCUS]) + run_tracker(background_game, max_samples=10):
            log.add(msg)
        slots = log.finalize(T0 + 10)
        calibration = {"ear_baseline": 0.3, "valid_frames": 50, "face_detected_ratio": 1.0,
                       "started_at": T0 - 5, "ended_at": T0, "fallback_used": False}
        result = score_session(slots, calibration, [], session_id="x", session_start=T0, session_end=T0 + 10,
                               llm_labels={("VS Code", None, None): "work"}, report_text="")
        self.assertEqual(result["slot_scores"][0]["final_tag"], "distraction")
        self.assertTrue(result["top_distractions"][0]["background"])


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


class DomainMatchTest(unittest.TestCase):
    def test_subdomains_match_listed_domain(self):
        for domain in ("instagram.com", "m.instagram.com", "web.facebook.com", "x.com", "mobile.twitter.com",
                       "comic.naver.com", "m.comic.naver.com", "gall.dcinside.com", "chzzk.naver.com"):
            self.assertTrue(is_distraction_domain(domain), domain)

    def test_parent_or_lookalike_does_not_match(self):
        """naver.com 자체, AWS 콘솔, 이름만 비슷한 도메인은 걸리지 않는다."""
        for domain in ("naver.com", "blog.naver.com", "aws.amazon.com", "box.com", "netflix.com.evil.io",
                       "docs.python.org", "youtube.com", None, ""):
            self.assertFalse(is_distraction_domain(domain), domain)

    def test_case_and_trailing_dot(self):
        self.assertTrue(is_distraction_domain("Instagram.COM."))

    def test_group(self):
        self.assertEqual(distraction_domain_group("m.instagram.com"), "sns")
        self.assertEqual(distraction_domain_group("netflix.com"), "video")
        self.assertIsNone(distraction_domain_group("github.com"))

    def test_url_to_domain_then_match(self):
        """주소창 텍스트 → 도메인 → 규칙 (www. 제거 포함)."""
        self.assertTrue(is_distraction_domain(url_to_domain("https://www.instagram.com/reels/")))
        self.assertTrue(is_distraction_domain(url_to_domain("m.comic.naver.com/webtoon/list?titleId=1")))


class SharedYouTubeTest(unittest.TestCase):
    def test_tracker_uses_shared_lookup(self):
        shared = YouTubeCategoryLookup(api_key="test")
        tracker = WindowTracker(queue.Queue(), T0, use_youtube=True, youtube=shared)
        tracker._make_os_sample_fn(None)  # OS 조회는 sample()을 부를 때만 한다
        self.assertIs(tracker.youtube, shared)
        self.assertIsNotNone(tracker.sampler)

    def test_without_youtube_flag_shared_lookup_is_unused(self):
        tracker = WindowTracker(queue.Queue(), T0, youtube=YouTubeCategoryLookup(api_key="test"))
        tracker._make_os_sample_fn(None)
        self.assertIsNone(tracker.youtube)


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
