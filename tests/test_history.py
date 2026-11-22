"""core/history.py 테스트: schema_version별 읽기(3-03), 횟수·시간 집계."""
import unittest

from core.history import (
    SessionSummary,
    average_score,
    summarize,
    summarize_all,
    tag_episodes,
    total_focus_seconds,
)

START = 1_790_753_400.0


def slot(n: int, tag: str, start: float = START) -> dict:
    return {"slot": n, "start": start + n * 10, "end": start + n * 10 + 10, "final_tag": tag}


def v4_record(session_id="20261003-100000", start=START, **overrides) -> dict:
    record = {
        "schema_version": 4,
        "session_id": session_id,
        "session_start": start,
        "session_end": start + 1620.0,
        "pause_intervals": [{"start": start + 600.0, "end": start + 720.0}],
        "slot_scores": [slot(0, "focus", start), slot(1, "drowsy", start), slot(2, "drowsy", start),
                        slot(3, "focus", start), slot(4, "drowsy", start), slot(5, "distraction", start)],
        "score_mode": "full",
        "focus_score": 78,
        "judged_seconds": 1350,
        "tag_seconds": {"focus": 1050, "drowsy": 60, "away": 90, "distraction": 150, "unknown": 150, "no_cam": 0},
    }
    record.update(overrides)
    return record


def v3_record(session_id="20261002-100000", start=START - 86400) -> dict:
    """v3 기록: 점수 필드가 score_total 등 이전 방식, tag_seconds·focus_score 없음."""
    return {
        "schema_version": 3,
        "session_id": session_id,
        "session_start": start,
        "session_end": start + 60.0,
        "ear_baseline": 0.3,
        "pause_intervals": [],
        "slot_scores": [dict(slot(0, "focus", start), score=1), dict(slot(1, "away", start), score=-2),
                        dict(slot(2, "focus", start), score=1)],
        "score_total": 0,
        "drowsy_count": 0,
        "away_count": 1,
        "distraction_count": 0,
    }


class SummarizeV4Test(unittest.TestCase):
    def test_uses_stored_score_fields(self):
        s = summarize(v4_record())
        self.assertFalse(s.legacy)
        self.assertEqual(s.focus_score, 78)
        self.assertEqual(s.score_mode, "full")
        self.assertEqual(s.judged_seconds, 1350)
        self.assertEqual(s.focus_seconds, 1050)  # tag_seconds 그대로 (슬롯 합 20초가 아니라)

    def test_active_time_excludes_pause(self):
        s = summarize(v4_record())
        self.assertEqual(s.pause_count, 1)
        self.assertEqual(s.pause_seconds, 120.0)
        self.assertEqual(s.active_seconds, 1500.0)

    def test_counts_are_episodes(self):
        s = summarize(v4_record())
        self.assertEqual(s.drowsy_count, 2)  # 1~2번 슬롯 묶음 + 4번
        self.assertEqual(s.distraction_count, 1)
        self.assertEqual(s.away_count, 0)

    def test_end_before_start_kept_with_zero_time(self):
        """시계 변경 등으로 end < start여도 기록은 건너뛰지 않고 시간만 0으로."""
        s = summarize(v4_record(session_end=START - 5, pause_intervals=[]))
        self.assertEqual(s.active_seconds, 0.0)

    def test_score_none_kept(self):
        s = summarize(v4_record(focus_score=None, score_mode="none"))
        self.assertIsNone(s.focus_score)
        self.assertFalse(s.legacy)

    def test_missing_tag_seconds_falls_back_to_slots(self):
        record = v4_record()
        del record["tag_seconds"]
        self.assertEqual(summarize(record).focus_seconds, 20)


class SummarizeOldVersionTest(unittest.TestCase):
    def test_v3_has_no_score(self):
        s = summarize(v3_record())
        self.assertTrue(s.legacy)
        self.assertIsNone(s.focus_score)
        self.assertIsNone(s.score_mode)
        self.assertIsNone(s.judged_seconds)

    def test_v3_focus_time_from_slots(self):
        self.assertEqual(summarize(v3_record()).focus_seconds, 20)

    def test_v3_counts_from_slots_not_stored_counts(self):
        s = summarize(v3_record())
        self.assertEqual(s.away_count, 1)
        self.assertEqual(s.drowsy_count, 0)

    def test_v3_ignores_v4_named_fields(self):
        """v3 이하는 점수 방식이 달라 focus_score 같은 키가 있어도 쓰지 않는다."""
        record = dict(v3_record(), focus_score=55)
        self.assertIsNone(summarize(record).focus_score)

    def test_v1_without_pause_intervals(self):
        record = v3_record()
        record["schema_version"] = 1
        del record["pause_intervals"]
        s = summarize(record)
        self.assertEqual(s.pause_count, 0)
        self.assertEqual(s.pause_seconds, 0)
        self.assertEqual(s.active_seconds, 60.0)

    def test_newer_version_read_like_v4(self):
        self.assertEqual(summarize(v4_record(schema_version=5)).focus_score, 78)


class InvalidRecordTest(unittest.TestCase):
    def test_rejects_bad_required_fields(self):
        cases = {
            "no version": {"schema_version": None},
            "bool version": {"schema_version": True},
            "no start": {"session_start": None},
            "nan end": {"session_end": float("nan")},
            "pause not list": {"pause_intervals": {}},
            "bad pause": {"pause_intervals": [{"start": "x", "end": 1}]},
            "slots not list": {"slot_scores": None},
            "slot not dict": {"slot_scores": [1]},
            "id not str": {"session_id": 1},
        }
        for name, override in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                summarize(v4_record(**override))

    def test_summarize_all_skips_bad_and_sorts(self):
        records = [v4_record("20261003-100000"), {"schema_version": 4}, v3_record(), "not a dict"]
        with self.assertLogs("core.history", level="WARNING"):
            result = summarize_all(records)
        self.assertEqual([s.session_id for s in result], ["20261002-100000", "20261003-100000"])


class TagEpisodesTest(unittest.TestCase):
    def test_gap_in_slot_numbers_splits(self):
        """정지로 빠진 슬롯이 있으면 다른 묶음."""
        slots = [slot(0, "drowsy"), slot(1, "drowsy"), slot(3, "drowsy")]
        self.assertEqual(tag_episodes(slots, "drowsy"), 2)

    def test_unsorted_input(self):
        slots = [slot(2, "drowsy"), slot(0, "drowsy"), slot(1, "drowsy")]
        self.assertEqual(tag_episodes(slots, "drowsy"), 1)

    def test_empty(self):
        self.assertEqual(tag_episodes([], "drowsy"), 0)


class AggregateTest(unittest.TestCase):
    def test_average_ignores_none(self):
        summaries = [summarize(v4_record(focus_score=80)), summarize(v4_record(focus_score=None)),
                     summarize(v3_record()), summarize(v4_record(focus_score=60))]
        self.assertEqual(average_score(summaries), 70)

    def test_average_none_when_no_scores(self):
        self.assertIsNone(average_score([summarize(v3_record())]))
        self.assertIsNone(average_score([]))

    def test_total_focus(self):
        self.assertEqual(total_focus_seconds([summarize(v4_record()), summarize(v3_record())]), 1070)

    def test_summary_is_frozen(self):
        s = summarize(v4_record())
        self.assertIsInstance(s, SessionSummary)
        with self.assertRaises(AttributeError):
            s.focus_score = 1  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
