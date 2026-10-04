"""core/slot_log.py 병합 규칙 테스트 (개발계획서 2.6 "GUI 병합 규칙")."""
import unittest

from core.slot_log import SlotLog
from mocks.mock_cv_slots import FULL_FOCUS, make_cv_messages
from mocks.mock_session import build_scenario, build_session, messages_only
from mocks.mock_window_slots import APP_DOCS, APP_VSCODE, APP_YOUTUBE, window_message
from schemas import MergedSlot, empty_cv_part, empty_window_part, final_tag, slot_start

T0 = 1_780_000_000.0


def run(messages, session_start=T0) -> SlotLog:
    log = SlotLog(session_start)
    for msg in messages:
        log.add(msg)
    return log


def by_slot(merged):
    return {row["slot"]: row for row in merged}


class MergeTest(unittest.TestCase):
    def test_normal_session(self):
        log = run(messages_only(build_scenario("normal", T0)))
        merged = log.finalize()
        self.assertEqual([r["slot"] for r in merged], list(range(6)))
        for row in merged:
            self.assertEqual(set(row), set(MergedSlot.__annotations__))
            self.assertEqual(row["timestamp"], slot_start(row["slot"], T0))
        rows = by_slot(merged)
        self.assertEqual((rows[1]["app_name"], rows[1]["domain"]), APP_DOCS)
        self.assertEqual(rows[1]["blink_count"], 2)
        self.assertIs(rows[2]["face_present"], False)
        self.assertTrue(rows[3]["drowsy"])
        self.assertEqual(len(log), 6)

    def test_cv_seconds_accumulated(self):
        log = run(messages_only(build_scenario("normal", T0)))
        seconds = log.cv_seconds()
        self.assertEqual(len(seconds), 60)
        stamps = [s["timestamp"] for s in seconds]
        self.assertEqual(stamps, sorted(stamps))


class OrderTest(unittest.TestCase):
    def test_order_independent(self):
        cv = [FULL_FOCUS, "PBPPPBPPPP", "PPPPCCCCCP"]
        apps = [APP_VSCODE, APP_DOCS, APP_YOUTUBE]
        base = run(messages_only(build_session(T0, cv, apps)))
        variants = [build_session(T0, cv, apps, order="reversed")]
        variants += [build_session(T0, cv, apps, order="shuffled", seed=s) for s in range(5)]
        for timed in variants:
            log = run(messages_only(timed))
            self.assertEqual(log.finalize(), base.finalize())
            self.assertEqual(log.cv_seconds(), base.cv_seconds())

    def test_window_first_and_cv_first(self):
        cv_msg = make_cv_messages(T0, [FULL_FOCUS])[0]
        win_msg = window_message(0, T0, APP_VSCODE)
        self.assertEqual(run([cv_msg, win_msg]).finalize(), run([win_msg, cv_msg]).finalize())


class MissingTest(unittest.TestCase):
    def test_one_side_missing(self):
        rows = by_slot(run(messages_only(build_scenario("missing", T0))).finalize())
        # 슬롯 2: CV 메시지 없음 → CV 기본값, 창 정보는 유지
        for key, value in empty_cv_part().items():
            self.assertEqual(rows[2][key], value, key)
        self.assertEqual(rows[2]["app_name"], "KakaoTalk")
        self.assertEqual(rows[2]["timestamp"], slot_start(2, T0))
        # 슬롯 4: 창 메시지 없음 → 창 기본값, CV는 유지
        for key, value in empty_window_part().items():
            self.assertEqual(rows[4][key], value, key)
        self.assertEqual(rows[4]["blink_count"], 1)
        # 슬롯 3: 창 조회 실패(None)는 그대로
        self.assertIsNone(rows[3]["app_name"])

    def test_missing_seconds_not_in_cv_seconds(self):
        log = run(messages_only(build_scenario("missing", T0)))
        # 6슬롯 중 슬롯 2 CV 없음(-10), 슬롯 1 요약 2개 누락(-2)
        self.assertEqual(len(log.cv_seconds()), 48)

    def assert_unknown_slot(self, row, slot):
        """양쪽 메시지가 없는 슬롯: 0/집중이 아니라 '판단할 근거 없음' 상태."""
        self.assertEqual(row["slot"], slot)
        self.assertEqual(row["timestamp"], slot_start(slot, T0))
        self.assertIsNone(row["face_present"])
        self.assertIsNone(row["app_name"])
        self.assertIsNone(row["domain"])
        self.assertIsNone(row["ear_mean"])
        self.assertFalse(row["drowsy"])
        self.assertEqual(final_tag(row["face_present"], row["drowsy"], "unknown"), "unknown")

    def test_both_missing_gap_filled(self):
        timed = build_session(T0, [FULL_FOCUS] * 4, [APP_VSCODE] * 4,
                              drop_cv={1, 2}, drop_window={2})
        merged = run(messages_only(timed)).finalize()
        self.assertEqual([r["slot"] for r in merged], [0, 1, 2, 3])
        self.assertEqual(merged[1]["app_name"], "VS Code")  # 슬롯 1: 창만 있음
        self.assert_unknown_slot(merged[2], 2)               # 슬롯 2: 양쪽 모두 없음
        self.assertEqual(set(merged[2]), set(MergedSlot.__annotations__))

    def test_leading_slots_filled(self):
        log = run([window_message(3, T0, APP_VSCODE)])
        merged = log.finalize()
        self.assertEqual([r["slot"] for r in merged], [0, 1, 2, 3])
        self.assert_unknown_slot(merged[0], 0)

    def test_session_end_fills_trailing_slots(self):
        log = run(messages_only(build_session(T0, [FULL_FOCUS] * 2, [APP_VSCODE] * 2)))
        merged = log.finalize(session_end=T0 + 45)  # 슬롯 0~4 (4번은 5초)
        self.assertEqual([r["slot"] for r in merged], [0, 1, 2, 3, 4])
        for slot in (2, 3, 4):
            self.assert_unknown_slot(merged[slot], slot)

    def test_session_end_on_boundary(self):
        log = run([])
        self.assertEqual(len(log.finalize(session_end=T0 + 30)), 3)
        self.assertEqual(len(log.finalize(session_end=T0 + 30 + 1e-9)), 3)
        self.assertEqual(len(log.finalize(session_end=T0 + 30.5)), 4)
        self.assertEqual(log.finalize(session_end=T0), [])

    def test_session_end_before_messages_keeps_messages(self):
        log = run(messages_only(build_session(T0, [FULL_FOCUS] * 3, [APP_VSCODE] * 3)))
        self.assertEqual(len(log.finalize(session_end=T0 + 5)), 3)

    def test_short_last_slot(self):
        log = run(messages_only(build_scenario("short_last_slot", T0)))
        self.assertEqual(len(log.finalize()), 6)
        self.assertEqual(len(log.cv_seconds()), 54)


class DuplicateTest(unittest.TestCase):
    def test_duplicate_window_later_wins(self):
        rows = by_slot(run(messages_only(build_scenario("duplicate_window", T0))).finalize())
        self.assertEqual((rows[1]["app_name"], rows[1]["domain"]), APP_YOUTUBE)

    def test_duplicate_window_reversed_arrival(self):
        early = window_message(1, T0, APP_DOCS, jitter=0.05)
        late = window_message(1, T0, APP_YOUTUBE, jitter=0.8)
        for order in ([early, late], [late, early]):
            rows = by_slot(run(order).finalize())
            self.assertEqual(rows[1]["domain"], "youtube.com")

    def test_duplicate_window_same_timestamp_last_arrival_wins(self):
        a = window_message(0, T0, APP_DOCS)
        b = window_message(0, T0, APP_VSCODE)
        self.assertEqual(run([a, b]).finalize()[0]["app_name"], "VS Code")

    def test_duplicate_cv_replaces_seconds(self):
        first = make_cv_messages(T0, ["PPPPPPPPPP"])[0]
        second = make_cv_messages(T0, ["PPPP"])[0]
        log = run([first, second])
        self.assertEqual(len(log.cv_seconds()), 4)
        self.assertEqual(len(log.finalize()), 1)


class EdgeTest(unittest.TestCase):
    def test_empty_session(self):
        log = run([])
        self.assertEqual(log.finalize(), [])
        self.assertEqual(log.cv_seconds(), [])
        self.assertEqual(len(log), 0)

    def test_full_session(self):
        log = run(messages_only(build_scenario("full_session", T0, seed=3)))
        merged = log.finalize()
        self.assertEqual([r["slot"] for r in merged], list(range(150)))

    def test_finalize_is_repeatable_and_returns_copies(self):
        log = run(messages_only(build_scenario("missing", T0)))
        first = log.finalize()
        first[0]["app_name"] = "changed"
        log.cv_seconds()[0]["blink_count"] = 99
        self.assertEqual(log.finalize()[0]["app_name"], "VS Code")
        self.assertNotEqual(log.cv_seconds()[0]["blink_count"], 99)

    def test_input_message_not_aliased(self):
        msg = window_message(0, T0, APP_VSCODE)
        log = run([msg])
        msg["slot"]["app_name"] = "changed"
        self.assertEqual(log.finalize()[0]["app_name"], "VS Code")

    def test_late_message_after_finalize(self):
        log = run([window_message(0, T0, APP_VSCODE)])
        self.assertIsNone(log.finalize()[0]["face_present"])
        log.add(make_cv_messages(T0, [FULL_FOCUS])[0])
        self.assertIs(log.finalize()[0]["face_present"], True)

    def test_unknown_source(self):
        with self.assertRaises(ValueError):
            SlotLog(T0).add({"source": "posture", "slot": {}})  # type: ignore[typeddict-item]


if __name__ == "__main__":
    unittest.main()
