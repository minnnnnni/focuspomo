"""mocks/ 가 스키마·2.6 압축 규칙대로 메시지를 만드는지 확인한다."""
import queue
import unittest

from config.aggregation import CALIBRATION_MIN_FACE_RATIO, CALIBRATION_SEC
from mocks.mock_calibration import SCENARIOS as CALIBRATION_SCENARIOS
from mocks.mock_calibration import MockCalibrator, make_calibration
from mocks.mock_cv_slots import FULL_AWAY, make_cv_messages
from mocks.mock_session import SCENARIOS, build_scenario, feed_queue, messages_only
from mocks.mock_window_slots import APP_VSCODE, window_message
from schemas import Calibration, CvSlot, CvSecond, WindowSlot, slot_index

T0 = 1_780_000_000.0


def cv_slots(messages):
    return {m["slot"]["slot"]: m for m in messages if m["source"] == "cv"}


def window_msgs(messages):
    return [m for m in messages if m["source"] == "window"]


class CvMockTest(unittest.TestCase):
    def test_keys_match_schema(self):
        msg = make_cv_messages(T0, ["PBCA-PPPPP"])[0]
        self.assertEqual(set(msg["slot"]), set(CvSlot.__annotations__))
        for sec in msg["seconds"]:
            self.assertEqual(set(sec), set(CvSecond.__annotations__))
        self.assertEqual(len(msg["seconds"]), 9)  # "-" 1초 누락

    def test_closed_run_carries_across_slots(self):
        a, b = make_cv_messages(T0, ["PPPPPPPPCC", "CCCPPPPPPP"])
        self.assertEqual(a["slot"]["closed_run_max"], 2)
        self.assertFalse(a["slot"]["drowsy"])
        self.assertEqual(b["slot"]["closed_run_max"], 5)
        self.assertTrue(b["slot"]["drowsy"])

    def test_missing_second_breaks_closed_run(self):
        (msg,) = make_cv_messages(T0, ["CCC-CCCPPP"])
        self.assertEqual(msg["slot"]["closed_run_max"], 3)
        self.assertFalse(msg["slot"]["drowsy"])

    def test_away_slot(self):
        (msg,) = make_cv_messages(T0, [FULL_AWAY])
        self.assertIs(msg["slot"]["face_present"], False)
        self.assertIsNone(msg["slot"]["ear_mean"])

    def test_no_seconds_gives_none(self):
        (msg,) = make_cv_messages(T0, ["-" * 10])
        self.assertIsNone(msg["slot"]["face_present"])
        self.assertFalse(msg["slot"]["drowsy"])
        self.assertEqual(msg["seconds"], [])

    def test_invalid_pattern(self):
        with self.assertRaises(ValueError):
            make_cv_messages(T0, ["P" * 11])


class WindowMockTest(unittest.TestCase):
    def test_keys_and_slot(self):
        msg = window_message(3, T0, APP_VSCODE, jitter=0.9)
        self.assertEqual(set(msg["slot"]), set(WindowSlot.__annotations__))
        self.assertEqual(slot_index(msg["slot"]["timestamp"], T0), 3)

    def test_jitter_out_of_slot_rejected(self):
        with self.assertRaises(ValueError):
            window_message(3, T0, APP_VSCODE, jitter=10.0)


class ScenarioTest(unittest.TestCase):
    def test_all_scenarios_build(self):
        for name in SCENARIOS:
            with self.subTest(name=name):
                timed = build_scenario(name, T0)
                arrivals = [t for t, _ in timed]
                self.assertEqual(arrivals, sorted(arrivals))

    def test_arrival_order_window_before_cv(self):
        msgs = messages_only(build_scenario("normal", T0))
        self.assertEqual(msgs[0]["source"], "window")
        self.assertEqual(msgs[0]["slot"]["slot"], 0)
        self.assertEqual((msgs[1]["source"], msgs[1]["slot"]["slot"]), ("window", 1))
        self.assertEqual((msgs[2]["source"], msgs[2]["slot"]["slot"]), ("cv", 0))

    def test_out_of_order_same_set(self):
        key = lambda m: (m["source"], m["slot"]["slot"])
        normal = messages_only(build_scenario("normal", T0))
        shuffled = messages_only(build_scenario("out_of_order", T0))
        self.assertNotEqual([key(m) for m in normal], [key(m) for m in shuffled])
        self.assertEqual(sorted(map(key, normal)), sorted(map(key, shuffled)))

    def test_missing(self):
        msgs = messages_only(build_scenario("missing", T0))
        self.assertNotIn(2, cv_slots(msgs))
        self.assertNotIn(4, [m["slot"]["slot"] for m in window_msgs(msgs)])
        slot3 = [m for m in window_msgs(msgs) if m["slot"]["slot"] == 3][0]
        self.assertIsNone(slot3["slot"]["app_name"])

    def test_duplicate_window_later_is_youtube(self):
        msgs = messages_only(build_scenario("duplicate_window", T0))
        slot1 = [m["slot"] for m in window_msgs(msgs) if m["slot"]["slot"] == 1]
        self.assertEqual(len(slot1), 2)
        self.assertEqual(slot1[-1]["domain"], "youtube.com")

    def test_short_last_slot(self):
        timed = build_scenario("short_last_slot", T0)
        last_cv = cv_slots(messages_only(timed))[5]
        self.assertEqual(len(last_cv["seconds"]), 4)
        self.assertAlmostEqual(timed[-1][0], T0 + 50 + 4 + 0.2)

    def test_full_session_length(self):
        msgs = messages_only(build_scenario("full_session", T0, seed=1))
        self.assertEqual(len(cv_slots(msgs)), 150)
        self.assertEqual(len(window_msgs(msgs)), 150)


class FeedQueueTest(unittest.TestCase):
    def test_feeds_all_messages_in_order(self):
        timed = build_scenario("drowsy_boundary", T0)
        q: queue.Queue = queue.Queue()
        feed_queue(q, timed, T0, speed=1000).join(timeout=2)
        got = [q.get_nowait() for _ in range(q.qsize())]
        self.assertEqual(got, messages_only(timed))


class CalibrationMockTest(unittest.TestCase):
    def passed(self, c):
        return c["face_detected_ratio"] >= CALIBRATION_MIN_FACE_RATIO

    def test_keys_match_schema(self):
        c = make_calibration(T0, 0.9)
        self.assertEqual(set(c), set(Calibration.__annotations__))
        self.assertEqual(c["ended_at"], T0 + CALIBRATION_SEC)
        self.assertFalse(c["fallback_used"])

    def test_invalid_ratio(self):
        with self.assertRaises(ValueError):
            make_calibration(T0, 1.5)

    def test_no_face(self):
        c = make_calibration(T0, 0.0)
        self.assertEqual(c["valid_frames"], 0)
        self.assertEqual(c["ear_baseline"], 0.0)

    def test_scenario_outcomes(self):
        expected = {
            "success": [True],
            "retry_success": [False, True],
            "fail_twice": [False, False],
            "no_face": [False, False],
            "borderline": [True],
        }
        self.assertEqual(set(expected), set(CALIBRATION_SCENARIOS))
        for name, outcomes in expected.items():
            calibrator = MockCalibrator(name)
            got = [self.passed(calibrator.run(T0 + i * 10)) for i in range(len(outcomes))]
            self.assertEqual(got, outcomes, name)

    def test_repeats_last_attempt(self):
        calibrator = MockCalibrator("retry_success")
        results = [calibrator.run(T0) for _ in range(4)]
        self.assertEqual([self.passed(c) for c in results], [False, True, True, True])
        self.assertEqual(calibrator.attempts, 4)

    def test_unknown_scenario(self):
        with self.assertRaises(ValueError):
            MockCalibrator("nope")


if __name__ == "__main__":
    unittest.main()
