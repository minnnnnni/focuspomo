"""scoring/window_usage.py 테스트 — 리포트의 많이 쓴 창·구간별 기록·오래 쓴 창 (점수 명세 8장)."""
import unittest

from scoring.window_usage import (
    NO_WINDOW_TEXT,
    long_runs,
    segments,
    usage_by_window,
    window_name,
    window_runs,
)

T0 = 1_790_000_000.0


def row(slot: int, app="VS Code", domain=None, category=None, *, label="work", tag="focus", rule=None,
        blinks=0, seconds=10.0) -> dict:
    start = T0 + 10 * slot
    return {"slot": slot, "start": start, "end": start + seconds, "app_name": app, "domain": domain,
            "video_category": category, "rule_distraction": rule, "window_label": label,
            "raw_tag": tag, "final_tag": tag, "blink_count": blinks}


def youtube(slot: int, category=None, **kw) -> dict:
    kw.setdefault("label", "unknown")
    kw.setdefault("tag", "unknown")
    return row(slot, "Chrome", "youtube.com", category, **kw)


class WindowNameTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(window_name(row(0)), "VS Code")
        self.assertEqual(window_name(youtube(0)), "youtube.com")
        self.assertEqual(window_name(youtube(0, "Gaming")), "youtube.com [Gaming]")
        self.assertEqual(window_name(row(0, rule="background", label="distraction")), "VS Code + 백그라운드 재생")
        self.assertEqual(window_name(row(0, app=None, label="unknown")), NO_WINDOW_TEXT)


class UsageTest(unittest.TestCase):
    def test_sorted_by_time_then_first_seen(self):
        rows = [row(0), youtube(1), youtube(2), row(3, "Notion"), row(4)]
        usage = usage_by_window(rows)
        self.assertEqual([(u.name, u.seconds, u.slots, u.window_label) for u in usage],
                         [("VS Code", 20.0, 2, "work"), ("youtube.com", 20.0, 2, "unknown"), ("Notion", 10.0, 1, "work")])

    def test_short_last_slot_and_background_separate(self):
        rows = [row(0), row(1, rule="background", label="distraction", tag="distraction"), row(2, seconds=3.4)]
        usage = {u.name: round(u.seconds, 3) for u in usage_by_window(rows)}
        self.assertEqual(usage, {"VS Code": 13.4, "VS Code + 백그라운드 재생": 10.0})

    def test_empty(self):
        self.assertEqual(usage_by_window([]), [])
        self.assertEqual(segments([]), [])


class SegmentTest(unittest.TestCase):
    def test_groups_same_window_label_and_state(self):
        rows = [row(0, blinks=1), row(1, blinks=2), row(2, tag="away"), row(3), youtube(4), youtube(5)]
        segs = segments(rows)
        self.assertEqual([(s.name, s.final_tag, s.slots, s.seconds) for s in segs],
                         [("VS Code", "focus", 2, 20.0), ("VS Code", "away", 1, 10.0), ("VS Code", "focus", 1, 10.0),
                          ("youtube.com", "unknown", 2, 20.0)])
        self.assertEqual(segs[0].blink_count, 3)
        self.assertEqual((segs[3].start, segs[3].end, segs[3].offset(T0)), (T0 + 40, T0 + 60, 40.0))

    def test_pause_gap_and_order(self):
        """정지로 빠진 슬롯이 있으면 끊고, 입력 순서와 무관하게 slot 순."""
        segs = segments([row(3), row(0), row(1)])
        self.assertEqual([(s.start, s.slots) for s in segs], [(T0, 2), (T0 + 30, 1)])

    def test_window_runs_ignore_state(self):
        rows = [youtube(0), youtube(1, tag="away"), youtube(2), row(3)]
        runs = window_runs(rows)
        self.assertEqual([(r.name, r.slots) for r in runs], [("youtube.com", 3), ("VS Code", 1)])


class LongRunTest(unittest.TestCase):
    def test_finds_long_non_work_runs(self):
        rows = ([row(i) for i in range(6)] + [youtube(i) for i in range(6, 16)]           # 100초
                + [row(i) for i in range(16, 20)] + [row(i, "KakaoTalk", label="unknown", tag="unknown")
                                                     for i in range(20, 27)])            # 70초
        runs = long_runs(rows)
        self.assertEqual([(r.name, r.seconds, r.offset(T0)) for r in runs],
                         [("youtube.com", 100.0, 60.0), ("KakaoTalk", 70.0, 200.0)])

    def test_work_short_and_missing_windows_are_skipped(self):
        rows = ([row(i) for i in range(10)] + [youtube(i) for i in range(10, 15)]
                + [row(i, app=None, label="unknown", tag="unknown") for i in range(15, 25)])
        self.assertEqual(long_runs(rows), [])

    def test_distraction_counts_and_limit(self):
        rows = []
        for n, start in enumerate((0, 10, 20, 30)):
            rows += [youtube(start + i, "Gaming", label="distraction", tag="distraction") for i in range(6 + n)]
            rows.append(row(start + 9))
        runs = long_runs(rows, limit=2)
        self.assertEqual([r.slots for r in runs], [9, 8])


if __name__ == "__main__":
    unittest.main()
