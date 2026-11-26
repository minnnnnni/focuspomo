"""window_tracker/video_rules.py 테스트 (WIN-05, 유튜브 영상 분류표)."""
import unittest

from config.aggregation import VIDEO_WATCH_RESET_SEC
from config.window_rules import BGM, SETUP_FOCUS_SEC, SETUP_GRACE_SEC, SHORTS, VIDEO_POLICIES
from window_tracker.video_rules import (
    VideoWatchRun,
    classify_video,
    foreground_label,
    is_background_distraction,
    is_bgm_title,
    is_foreground_distraction,
    video_run_label,
    video_run_totals,
)


class ClassifyTest(unittest.TestCase):
    def test_order_shorts_then_bgm_then_category(self):
        self.assertEqual(classify_video("shorts", "Education", "lofi 공부 쇼츠"), SHORTS)
        self.assertEqual(classify_video("watch", "People & Blogs", "공부할 때 듣는 플리"), BGM)
        self.assertEqual(classify_video("watch", "Gaming", "롤 하이라이트"), "Gaming")
        self.assertEqual(classify_video(None, "Music", None), "Music")

    def test_unknown(self):
        self.assertIsNone(classify_video("watch", None, "운영체제 3강"))
        self.assertIsNone(classify_video(None, None, None))
        self.assertEqual(classify_video("shorts", None, None), SHORTS)  # 쇼츠는 주소만으로

    def test_bgm_titles(self):
        for title in ("lofi hip hop radio - beats to relax/study to", "[플리] 새벽 감성 노래 모음",
                      "공부할 때 듣는 플레이리스트", "Study With Me 3 hours (pomodoro)", "스터디윗미 | 도서관",
                      "10 Hours White Noise", "백색 소음 카페", "빗소리 ASMR", "Coffee Shop Ambience",
                      "집중 음악 🎧", "작업할 때 듣는 노래", "노동요 모음", "K-POP PLAYLIST 2026", "게임 BGM 모음"):
            with self.subTest(title=title):
                self.assertTrue(is_bgm_title(title))

    def test_not_bgm_titles(self):
        """강의·일반 영상 제목, 비슷하지만 다른 말은 배경음이 아니다."""
        for title in ("1시간 만에 끝내는 파이썬", "피아노 강의 1강", "운영체제 스케줄링", "도와주세요 플리즈",
                      "플리마켓 브이로그", "Display list 구현", "대학생 일상 브이로그", "", None):
            with self.subTest(title=title):
                self.assertFalse(is_bgm_title(title))


class JudgeTest(unittest.TestCase):
    def test_study_video_is_work_however_long(self):
        for watched in (0, 59, 3600):
            self.assertEqual(foreground_label("Education", watched), "work")
            self.assertFalse(is_foreground_distraction("Science & Technology", watched))

    def test_music_grace(self):
        """유예는 '이번 초 전까지 이어서 본 시간'이 grace_sec가 되는 초부터 끝난다 (61번째 초부터 딴짓)."""
        self.assertEqual(foreground_label("Music", 0), "unknown")
        self.assertEqual(foreground_label("Music", SETUP_GRACE_SEC - 1), "unknown")
        self.assertEqual(foreground_label("Music", SETUP_GRACE_SEC), "distraction")
        self.assertEqual(foreground_label(BGM, SETUP_GRACE_SEC), "distraction")

    def test_news_grace_is_longer(self):
        grace = VIDEO_POLICIES["News & Politics"].grace_sec
        self.assertGreater(grace, SETUP_GRACE_SEC)
        self.assertFalse(is_foreground_distraction("News & Politics", grace - 1))
        self.assertTrue(is_foreground_distraction("News & Politics", grace))

    def test_clear_distraction_is_immediate(self):
        for name in ("Gaming", "Entertainment", "People & Blogs", SHORTS):
            self.assertTrue(is_foreground_distraction(name, 0))

    def test_unknown_or_unlisted(self):
        self.assertEqual(foreground_label("Howto & Style", 3600), "unknown")
        self.assertEqual(foreground_label("Unknown(44)", 3600), "unknown")
        self.assertEqual(foreground_label(None), "unknown")
        self.assertFalse(is_foreground_distraction(None, 3600))

    def test_background(self):
        for name in ("Gaming", "Entertainment", "Comedy", "Film & Animation", "Sports", "People & Blogs",
                     "Autos & Vehicles", SHORTS):
            self.assertTrue(is_background_distraction(name), name)
        for name in ("Music", BGM, "Education", "Science & Technology", "News & Politics", "Travel & Events",
                     "Pets & Animals", "Howto & Style", "Unknown(44)", None):
            self.assertFalse(is_background_distraction(name), name)

class RunLabelTest(unittest.TestCase):
    """세션 종료 후 묶음 전체 길이로 정하는 라벨 (소급)."""

    def test_grace_categories_by_run_length(self):
        """30초 이하 집중 / 30초 초과 ~ 유예 이하 미분류 / 유예 초과 딴짓 (묶음 전체)."""
        for name in ("Music", BGM, "Travel & Events", "Pets & Animals"):
            with self.subTest(name=name):
                self.assertEqual(video_run_label(name, 5), "work")
                self.assertEqual(video_run_label(name, SETUP_FOCUS_SEC), "work")
                self.assertEqual(video_run_label(name, SETUP_FOCUS_SEC + 1), "unknown")
                self.assertEqual(video_run_label(name, SETUP_GRACE_SEC), "unknown")
                self.assertEqual(video_run_label(name, SETUP_GRACE_SEC + 1), "distraction")

    def test_news_uses_its_own_grace(self):
        grace = VIDEO_POLICIES["News & Politics"].grace_sec
        self.assertEqual(video_run_label("News & Politics", 120), "unknown")
        self.assertEqual(video_run_label("News & Politics", grace + 1), "distraction")
        self.assertEqual(video_run_label("News & Politics", 20), "work")

    def test_run_length_does_not_matter_for_other_categories(self):
        for sec in (None, 5, 3600):
            self.assertEqual(video_run_label("Education", sec), "work")
            self.assertEqual(video_run_label("Howto & Style", sec), "unknown")
            self.assertEqual(video_run_label("Unknown(44)", sec), "unknown")
            self.assertEqual(video_run_label("Gaming", sec), "distraction")
            self.assertEqual(video_run_label(SHORTS, sec), "distraction")

    def test_unknown_run_length_is_unknown(self):
        """묶음 정보가 없으면(v4 기록 등) 유예 분류는 판단하지 않는다."""
        self.assertEqual(video_run_label("Music", None), "unknown")

    def test_totals_take_max_per_run(self):
        slots = [{"video_run": 0, "video_run_sec": 10}, {"video_run": 0, "video_run_sec": 20},
                 {"video_run": None, "video_run_sec": None}, {"video_run": 1, "video_run_sec": 4},
                 {"video_run": 0, "video_run_sec": 25}, {}]
        self.assertEqual(video_run_totals(slots), {0: 25, 1: 4})


class WatchRunTest(unittest.TestCase):
    def feed(self, run: VideoWatchRun, start: float, classes: list) -> list[float]:
        return [run.update(start + i, c) for i, c in enumerate(classes)]

    def test_counts_seconds_before_this_one(self):
        self.assertEqual(self.feed(VideoWatchRun(), 0, ["Music"] * 4), [0, 1, 2, 3])

    def test_other_windows_pause_counting(self):
        run = VideoWatchRun()
        got = self.feed(run, 0, ["Music"] * 3 + [None] * 5 + ["Music"] * 2)
        self.assertEqual(got, [0, 1, 2] + [0] * 5 + [3, 4])

    def test_long_away_resets(self):
        """다른 창에 VIDEO_WATCH_RESET_SEC초까지는 이어 세고, 그보다 오래면 0부터."""
        at = self.feed(VideoWatchRun(), 0, ["Music"] * 3 + [None] * VIDEO_WATCH_RESET_SEC + ["Music"])
        over = self.feed(VideoWatchRun(), 0, ["Music"] * 3 + [None] * (VIDEO_WATCH_RESET_SEC + 1) + ["Music"])
        self.assertEqual((at[-1], over[-1]), (3, 0))

    def test_class_change_resets(self):
        self.assertEqual(self.feed(VideoWatchRun(), 0, ["Education"] * 3 + ["Music"] * 2 + ["Education"]),
                         [0, 1, 2, 0, 1, 0])

    def test_run_ids(self):
        """새 묶음마다 번호가 하나씩 늘고, 잠깐 다른 창을 본 것은 같은 묶음이다."""
        run = VideoWatchRun()
        ids = []
        for t, c in enumerate(["Music"] * 2 + [None] * 3 + ["Music"] + [None] * (VIDEO_WATCH_RESET_SEC + 1)
                              + ["Music", "Education"]):
            run.update(t, c)
            ids.append(run.run_id if c is not None else None)
        self.assertEqual([i for i in ids if i is not None], [0, 0, 0, 1, 2])

    def test_gap_in_timestamps_resets(self):
        """절전 등으로 조회가 끊기면 그동안 다른 창에 있던 것과 같다."""
        run = VideoWatchRun()
        self.feed(run, 0, ["Music"] * 30)
        self.assertEqual(run.update(330, "Music"), 0)

    def test_sample_interval(self):
        run = VideoWatchRun(sample_interval=2)
        self.assertEqual([run.update(t, "Music") for t in (0, 2, 4)], [0, 2, 4])


if __name__ == "__main__":
    unittest.main()
