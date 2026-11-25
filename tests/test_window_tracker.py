"""window_tracker 테스트 (WIN-01~05, 스키마 v4) — 실제 시간·Windows API 없이 가짜 시계와 가짜 함수로 돌린다."""
import queue
import unittest
from unittest.mock import patch

from config.aggregation import BG_DISTRACTION_MIN_SEC, DISTRACTION_MIN_SEC, VIDEO_WATCH_RESET_SEC
from config.window_rules import BGM, SETUP_GRACE_SEC, SHORTS, VIDEO_POLICIES
from core.slot_log import SlotLog
from mocks.mock_cv_slots import FULL_FOCUS, make_cv_messages
from schemas import WindowSlot, slot_index
from window_tracker.app_names import resolve_app_name
from window_tracker.domain_probe import url_to_domain
from window_tracker.foreground import NONE_STATE, ForegroundSampler, ForegroundState, resolve_foreground
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
from window_tracker.youtube_probe import VideoInfo, YouTubeCategoryLookup

T0 = 1_780_000_000.0


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def sample(t: float, app="VS Code", domain=None, cat=None, playing=None, background=False, bg_cat=None,
           watched=0, run=None) -> dict:
    """1초 조회 결과. watched(video_watch_sec)·run(video_run)은 build_window_slot을 직접 부를 때만 쓴다
    (트래커 루프는 직접 센다)."""
    return {"timestamp": t, "app_name": app, "domain": domain, "video_category": cat,
            "media_playing": playing, "background_media": background, "background_category": bg_cat,
            "video_watch_sec": watched, "video_run": run}


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

    def test_music_in_grace_is_not_rule(self):
        """Music은 이어서 본 시간이 유예(SETUP_GRACE_SEC) 안이면 규칙에 안 걸린다 (노래 고르는 중)."""
        slot = build_window_slot(0, [sample(0, "Chrome", "youtube.com", "Music")] * 10)
        self.assertIsNone(slot["rule_distraction"])
        self.assertEqual(slot["video_category"], "Music")
        late = build_window_slot(0, [sample(0, "Chrome", "youtube.com", "Music", watched=SETUP_GRACE_SEC)] * 10)
        self.assertEqual(late["rule_distraction"], "foreground")

    def test_lecture_is_not_rule(self):
        slot = build_window_slot(0, [sample(0, "Chrome", "youtube.com", "Education", watched=3000)] * 10)
        self.assertIsNone(slot["rule_distraction"])

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
        """공부하며 듣는 음악·배경음·강의, 분류를 모르는 영상은 틀어 둬도 감점하지 않는다."""
        for category in ("Music", BGM, "Education", "News & Politics", "Travel & Events"):
            with self.subTest(category=category):
                slot = build_window_slot(0, [self.bg(category)] * 10)
                self.assertIsNone(slot["rule_distraction"])
                self.assertEqual(slot["background_category"], category)
        self.assertIsNone(build_window_slot(0, [self.bg(None)] * 10)["rule_distraction"])

    def test_vlog_and_shorts_in_background_are_distraction(self):
        for category in ("People & Blogs", "Entertainment", SHORTS):
            with self.subTest(category=category):
                self.assertEqual(build_window_slot(0, [self.bg(category)] * 10)["rule_distraction"], "background")

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


def video(ts, category, **kw):
    """앞 화면 유튜브 영상 (재생 중이라 백그라운드는 아님)."""
    return sample(ts, "Chrome", "youtube.com", category, playing=True, **kw)


def timeline(*parts):
    """[(초 수, 조회 함수(ts) -> 샘플), ...] → 트래커용 sample_fn. 모자라면 마지막 조회를 이어 쓴다."""
    plan = [fn for n, fn in parts for _ in range(n)]

    def sample_fn(clock, n, ts):
        return plan[min(n, len(plan)) - 1](ts)

    return sample_fn, len(plan)


def rules(parts) -> list:
    sample_fn, total = timeline(*parts)
    return [m["slot"]["rule_distraction"] for m in run_tracker(sample_fn, max_samples=total)]


class VideoWatchScenarioTest(unittest.TestCase):
    """유튜브 영상 분류표 (WIN-05): 이어서 본 시간·유예·백그라운드 — 트래커 루프로 슬롯을 넘어 센다."""

    MUSIC = staticmethod(lambda ts: video(ts, "Music"))
    CODE = staticmethod(lambda ts: sample(ts))
    CODE_WITH_MUSIC = staticmethod(lambda ts: sample(ts, playing=True, background=True, bg_cat="Music"))
    KAKAO = staticmethod(lambda ts: sample(ts, "KakaoTalk"))

    def test_picking_music_then_working_is_not_distraction(self):
        """노래를 20초 고르고 VS Code로 가서 틀어 두면 어느 슬롯도 딴짓이 아니다."""
        sample_fn, total = timeline((20, self.MUSIC), (40, self.CODE_WITH_MUSIC))
        msgs = run_tracker(sample_fn, max_samples=total)
        self.assertEqual([m["slot"]["rule_distraction"] for m in msgs], [None] * 6)
        self.assertEqual([m["slot"]["video_category"] for m in msgs], ["Music", "Music"] + [None] * 4)
        self.assertEqual(msgs[3]["slot"]["background_category"], "Music")

    def test_keeping_music_video_on_screen_becomes_distraction(self):
        """뮤비를 계속 앞 화면에 띄워 두면 유예(60초)가 지난 슬롯부터 딴짓."""
        self.assertEqual(rules([(90, self.MUSIC)]), [None] * 6 + ["foreground"] * 3)

    def test_grace_crossed_mid_slot(self):
        """5초부터 보기 시작 → 65초부터 딴짓인 초. 슬롯 6(60~69초)은 딴짓 5초 ≥ DISTRACTION_MIN_SEC."""
        self.assertEqual(rules([(5, self.CODE), (75, self.MUSIC)]), [None] * 6 + ["foreground", "foreground"])

    def test_short_break_keeps_counting(self):
        """카톡을 잠깐(5초) 봤다 돌아오면 이어서 센다 → 50 + 10초 뒤(65초)부터 딴짓."""
        self.assertEqual(rules([(50, self.MUSIC), (5, self.KAKAO), (25, self.MUSIC)]),
                         [None] * 6 + ["foreground", "foreground"])

    def test_long_break_starts_over(self):
        """다른 창에 VIDEO_WATCH_RESET_SEC보다 오래 있다 오면 (곡 넘기러 온 것) 다시 유예."""
        away = VIDEO_WATCH_RESET_SEC + 5
        self.assertEqual(rules([(50, self.MUSIC), (away, self.CODE_WITH_MUSIC), (80 - 50 - away, self.MUSIC)]),
                         [None] * 8)

    def test_other_music_video_keeps_counting(self):
        """뮤비 → 다음 뮤비(같은 분류)는 이어서 센다."""
        other = lambda ts: sample(ts, "Edge", "youtube.com", "Music", playing=True)
        self.assertEqual(rules([(40, self.MUSIC), (40, other)]), [None] * 6 + ["foreground", "foreground"])

    def test_switching_from_lecture_to_music_gets_grace(self):
        """강의를 오래 보다가 음악을 고르러 가도 분류가 바뀌어 0부터 센다."""
        lecture = lambda ts: video(ts, "Education")
        self.assertEqual(rules([(300, lecture), (50, self.MUSIC)]), [None] * 35)

    def test_lecture_is_never_distraction(self):
        self.assertEqual(rules([(600, lambda ts: video(ts, "Science & Technology"))]), [None] * 60)

    def test_game_video_is_distraction_immediately(self):
        self.assertEqual(rules([(7, self.CODE), (3, lambda ts: video(ts, "Gaming"))]), ["foreground"])

    def test_shorts_are_distraction_immediately(self):
        self.assertEqual(rules([(7, self.CODE), (3, lambda ts: video(ts, SHORTS))]), ["foreground"])

    def test_news_has_longer_grace(self):
        grace = VIDEO_POLICIES["News & Politics"].grace_sec
        self.assertEqual(rules([(grace + 10, lambda ts: video(ts, "News & Politics"))]),
                         [None] * (grace // 10) + ["foreground"])

    def test_bgm_video_on_screen_is_like_music(self):
        self.assertEqual(rules([(70, lambda ts: video(ts, BGM))]), [None] * 6 + ["foreground"])

    def test_sleep_starts_over(self):
        """노트북을 덮었다 열면 (조회가 끊긴 시간 = 다른 창) 다시 유예."""
        def sample_fn(clock, n, ts):
            if n == 50:
                clock.now += 300
            return video(ts, "Music")

        msgs = run_tracker(sample_fn, max_samples=100)
        self.assertEqual({m["slot"]["rule_distraction"] for m in msgs}, {None})

    def test_slot_carries_run_number_and_length(self):
        """슬롯마다 영상 묶음 번호와 이 슬롯 끝까지 이어서 본 시간 (v5). 대표가 VS Code여도 걸친 묶음을 보낸다."""
        sample_fn, total = timeline((25, self.MUSIC), (5, self.CODE), (20, self.CODE),
                                    (10, lambda ts: video(ts, "Education")))
        slots = [m["slot"] for m in run_tracker(sample_fn, max_samples=total)]
        self.assertEqual([(s["video_run"], s["video_run_sec"]) for s in slots],
                         [(0, 10), (0, 20), (0, 25), (None, None), (None, None), (1, 10)])
        # 음악 5초 + VS Code 5초 → 동률이면 먼저 나온 음악이 대표
        self.assertEqual((slots[2]["app_name"], slots[2]["video_category"]), ("Chrome", "Music"))
        self.assertEqual(slots[3]["app_name"], "VS Code")


def score_timeline(*parts) -> list[str]:
    """트래커 → 슬롯 로그 병합 → 점수 모듈까지. 웹캠은 계속 집중, VS Code는 작업 (LLM 대신)."""
    from scoring.focus_score import score_session

    sample_fn, total = timeline(*parts)
    n_slots = -(-total // 10)
    log = SlotLog(T0)
    for msg in make_cv_messages(T0, [FULL_FOCUS] * n_slots) + run_tracker(sample_fn, max_samples=total):
        log.add(msg)
    slots = log.finalize(T0 + total)
    calibration = {"ear_baseline": 0.3, "valid_frames": 50, "face_detected_ratio": 1.0,
                   "started_at": T0 - 5, "ended_at": T0, "fallback_used": False}
    result = score_session(slots, calibration, [], session_id="x", session_start=T0, session_end=T0 + total,
                           llm_labels={("VS Code", None, None): "work"}, report_text="")
    return [s["final_tag"] for s in result["slot_scores"]]


class RetroactiveScoreTest(unittest.TestCase):
    """유튜브 영상 분류표 소급: 같은 분류를 이어서 본 묶음의 전체 길이로 그 묶음의 슬롯을 한꺼번에 정한다."""

    MUSIC = staticmethod(VideoWatchScenarioTest.MUSIC)
    CODE_WITH_MUSIC = staticmethod(VideoWatchScenarioTest.CODE_WITH_MUSIC)

    def test_keeping_music_video_on_screen_is_all_distraction(self):
        """뮤비 70초: 유예 60초 구간까지 소급해 전부 딴짓."""
        self.assertEqual(score_timeline((70, self.MUSIC)), ["distraction"] * 7)

    def test_picking_music_for_20_seconds_is_focus(self):
        """20초 고르고 VS Code로 가서 틀어 둠 → 고른 시간도 집중."""
        self.assertEqual(score_timeline((20, self.MUSIC), (40, self.CODE_WITH_MUSIC)), ["focus"] * 6)

    def test_picking_for_45_seconds_is_unknown(self):
        """30초는 넘고 유예(60초)는 안 넘김 → 미분류 (점수 분모에서 빠짐)."""
        self.assertEqual(score_timeline((40, self.MUSIC), (20, self.CODE_WITH_MUSIC)),
                         ["unknown"] * 4 + ["focus"] * 2)

    def test_coming_back_to_skip_a_song_is_focus(self):
        """틀어 두고 일하다 곡을 넘기러 8초 → 새 묶음 8초 → 집중."""
        self.assertEqual(score_timeline((20, self.MUSIC), (40, self.CODE_WITH_MUSIC), (8, self.MUSIC),
                                        (22, self.CODE_WITH_MUSIC)),
                         ["focus"] * 9)

    def test_short_break_does_not_split_the_run(self):
        """뮤비 50초 → 카톡 5초 → 뮤비 25초: 한 묶음 75초라 앞의 50초도 딴짓.
        카톡 5초 + 뮤비 5초 슬롯은 동률이라 먼저 나온 카톡이 대표 → LLM 몫 (여기서는 라벨이 없어 미분류)."""
        kakao = VideoWatchScenarioTest.KAKAO
        self.assertEqual(score_timeline((50, self.MUSIC), (5, kakao), (25, self.MUSIC)),
                         ["distraction"] * 5 + ["unknown"] + ["distraction"] * 2)


class ForegroundVideoClassTest(unittest.TestCase):
    """resolve_foreground: 주소창·API 제목·창 제목으로 영상 분류 (Windows API는 가짜로)."""

    class FakeGui:
        @staticmethod
        def GetClassName(hwnd):
            return "Chrome_WidgetWin_1"

    class FakeYouTube:
        def __init__(self, category="Education", title="운영체제 3강"):
            self.info = VideoInfo(category, "27", title, "채널")

        def lookup(self, video_id):
            return self.info

    def resolve(self, address, *, youtube=None, title="영상 - YouTube - Chrome"):
        with patch("window_tracker.foreground.exe_for_hwnd", return_value="chrome.exe"), \
                patch("window_tracker.foreground.win32gui", self.FakeGui), \
                patch("window_tracker.foreground.read_address_bar", return_value=address):
            return resolve_foreground(1, title, use_domain_probe=True, youtube=youtube)

    def test_category_from_api(self):
        state = self.resolve("youtube.com/watch?v=dQw4w9WgXcQ", youtube=self.FakeYouTube())
        self.assertEqual((state.app_name, state.domain, state.video_category), ("Chrome", "youtube.com", "Education"))

    def test_shorts_beat_category_and_need_no_api(self):
        address = "youtube.com/shorts/dQw4w9WgXcQ"
        self.assertEqual(self.resolve(address, youtube=self.FakeYouTube()).video_category, SHORTS)
        self.assertEqual(self.resolve(address).video_category, SHORTS)

    def test_bgm_title_from_api_or_window(self):
        playlist = self.FakeYouTube("People & Blogs", "[Playlist] 집중이 잘 되는 피아노")
        self.assertEqual(self.resolve("youtube.com/watch?v=dQw4w9WgXcQ", youtube=playlist).video_category, BGM)
        no_api = self.resolve("youtube.com/watch?v=dQw4w9WgXcQ", title="lofi hip hop radio - YouTube - Chrome")
        self.assertEqual(no_api.video_category, BGM)

    def test_unknown_without_api_and_home_page(self):
        self.assertIsNone(self.resolve("youtube.com/watch?v=dQw4w9WgXcQ").video_category)
        home = self.resolve("youtube.com/results?search_query=lofi", youtube=self.FakeYouTube())
        self.assertEqual((home.domain, home.video_category), ("youtube.com", None))


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
        self.assertEqual(s["video_watch_sec"], 0)

    def test_background_bgm_title_beats_category(self):
        """플리 채널이 People & Blogs를 골라도 제목이 배경음이면 BGM (틀어 두기 허용)."""
        class FakeYouTube:
            def category_for_title(self, title):
                return "People & Blogs"

        playlist = MediaState(True, "Chrome", "공부할 때 듣는 플리 | 새벽 감성")
        vlog = MediaState(True, "Chrome", "대학생 일상 브이로그")
        self.assertEqual(make_sample(1.0, self.VSCODE, playlist, FakeYouTube())["background_category"], BGM)
        self.assertEqual(make_sample(1.0, self.VSCODE, vlog, FakeYouTube())["background_category"], "People & Blogs")

    def test_background_bgm_title_without_lookup(self):
        """앞 화면에서 조회한 적 없어도(또는 --no-youtube) 제목이 배경음이면 BGM, 아니면 모름(None)."""
        lofi = MediaState(True, "Chrome", "lofi hip hop radio - beats to relax/study to")
        self.assertEqual(make_sample(1.0, self.VSCODE, lofi)["background_category"], BGM)
        self.assertIsNone(make_sample(1.0, self.VSCODE, self.PLAYING)["background_category"])

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
    def test_describe_shows_watch_time(self):
        samples = [sample(0)] * 6 + [sample(0, "Chrome", "youtube.com", "Music", watched=60 + i) for i in range(4)]
        self.assertEqual(describe_samples(samples), "VS Code 6s, Chrome(youtube.com)[Music]~64s* 4s")

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
