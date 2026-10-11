"""core/session_end.py, mocks/mock_scoring.py 테스트 (세션 종료 흐름 1-13, 개발계획서 2.3~2.5)."""
import unittest

from core.session_end import finish_session, slots_for_scoring
from core.slot_log import SlotLog
from mocks.mock_calibration import make_calibration
from mocks.mock_scoring import FALLBACK_REPORT_TEXT, MockScorer, slot_score
from mocks.mock_session import build_scenario, messages_only
from schemas import SCHEMA_VERSION, SLOT_SECONDS, empty_cv_part, empty_window_part, make_session_id

START = 1_790_753_400.0


def make_log(scenario: str = "normal") -> SlotLog:
    log = SlotLog(START)
    for msg in messages_only(build_scenario(scenario, START)):
        log.add(msg)
    return log


def calibration():
    return make_calibration(START - 5.0, face_detected_ratio=1.0)


def merged(slot: int, **fields) -> dict:
    row = {"slot": slot, "timestamp": START + slot * SLOT_SECONDS, **empty_cv_part(), **empty_window_part()}
    row.update(fields)
    return row


class SlotsForScoringTest(unittest.TestCase):
    def test_no_pause_keeps_all(self):
        slots = [merged(i) for i in range(3)]
        kept, excluded = slots_for_scoring(slots, START, [])
        self.assertEqual(kept, slots)
        self.assertEqual(excluded, [])

    def test_any_overlap_excluded(self):
        slots = [merged(i) for i in range(5)]
        # 15~21초 정지 → 슬롯 1(10~20), 2(20~30) 제외
        kept, excluded = slots_for_scoring(slots, START, [{"start": START + 15, "end": START + 21}])
        self.assertEqual(excluded, [1, 2])
        self.assertEqual([s["slot"] for s in kept], [0, 3, 4])

    def test_touching_boundary_kept(self):
        slots = [merged(i) for i in range(3)]
        kept, excluded = slots_for_scoring(slots, START, [{"start": START + 10, "end": START + 20}])
        self.assertEqual(excluded, [1])
        self.assertEqual([s["slot"] for s in kept], [0, 2])


class FinishSessionTest(unittest.TestCase):
    def test_scorer_gets_unpaused_slots_and_log_keeps_all(self):
        scorer = MockScorer()
        pauses = [{"start": START + 25, "end": START + 31}]
        out = finish_session(make_log(), calibration(), pauses, START + 60, scorer)
        self.assertEqual([s["slot"] for s in scorer.calls[0]], [0, 1, 4, 5])
        self.assertEqual(out.excluded_slots, [2, 3])
        self.assertEqual([s["slot"] for s in out.focus_log["merged_slots"]], list(range(6)))
        self.assertEqual([s["slot"] for s in out.result["slot_scores"]], [0, 1, 4, 5])

    def test_focus_log_fields(self):
        log = make_log()
        pauses = [{"start": START + 40, "end": START + 45}, {"start": START + 5, "end": START + 6}]
        out = finish_session(log, calibration(), pauses, START + 60, MockScorer())
        fl = out.focus_log
        self.assertEqual(fl["schema_version"], SCHEMA_VERSION)
        self.assertEqual(fl["session_id"], make_session_id(START))
        self.assertEqual(fl["session_start"], START)
        self.assertEqual(fl["calibration"], calibration())
        self.assertEqual([p["start"] for p in fl["pause_intervals"]], [START + 5, START + 40])  # 시간순
        self.assertEqual(fl["cv_seconds"], log.cv_seconds())
        self.assertEqual(fl["merged_slots"], log.finalize(START + 60))
        self.assertEqual(out.result["pause_intervals"], fl["pause_intervals"])

    def test_session_identity_passed_to_scorer(self):
        out = finish_session(make_log(), calibration(), [], START + 55, MockScorer())
        r = out.result
        self.assertEqual((r["session_id"], r["session_start"], r["session_end"]),
                         (make_session_id(START), START, START + 55))
        self.assertEqual(r["ear_baseline"], calibration()["ear_baseline"])

    def test_missing_slots_filled_until_session_end(self):
        log = SlotLog(START)
        out = finish_session(log, calibration(), [], START + 25, MockScorer())
        self.assertEqual([s["slot"] for s in out.focus_log["merged_slots"]], [0, 1, 2])
        self.assertEqual(out.result["score_total"], 0)
        self.assertTrue(all(s["final_tag"] == "unknown" for s in out.result["slot_scores"]))

    def test_empty_session(self):
        out = finish_session(SlotLog(START), calibration(), [], START, MockScorer())
        self.assertEqual(out.result["slot_scores"], [])
        self.assertEqual(out.result["score_total"], 0)
        self.assertEqual(out.focus_log["merged_slots"], [])
        self.assertEqual(out.focus_log["cv_seconds"], [])

    def test_inputs_not_mutated_by_scorer(self):
        def scorer(slots, cal, pauses, **kw):
            for s in slots:
                s["app_name"] = "changed"
            pauses.clear()
            return MockScorer()([], cal, [], **kw)

        pauses = [{"start": START + 1, "end": START + 2}]
        out = finish_session(make_log(), calibration(), pauses, START + 60, scorer)
        self.assertNotIn("changed", [s["app_name"] for s in out.focus_log["merged_slots"]])
        self.assertEqual(len(out.focus_log["pause_intervals"]), 1)
        self.assertEqual(len(pauses), 1)


class MockScorerTest(unittest.TestCase):
    def score(self, slots, llm_fail=False, session_end=START + 60):
        return MockScorer(llm_fail)(slots, calibration(), [], session_id="20261003-100000",
                                    session_start=START, session_end=session_end)

    def test_slot_score_formula(self):
        cases = [
            (merged(0, face_present=True, blink_count=5), "work", 3),         # 깜빡임 캡 3
            (merged(0, face_present=True, drowsy=True), "work", -2),
            (merged(0, face_present=False, drowsy=True), "unknown", -4),      # 졸음·이탈 동시 (v8)
            (merged(0, face_present=True, drowsy=True), "distraction", -3),
            (merged(0, face_present=None), "unknown", 0),                     # 측정 불가는 감점 없음
        ]
        for slot, label, expected in cases:
            with self.subTest(slot=slot, label=label):
                self.assertEqual(slot_score(slot, label), expected)

    def test_normal_scenario_totals(self):
        slots = make_log().finalize(START + 60)
        r = self.score(slots)
        tags = [s["final_tag"] for s in r["slot_scores"]]
        self.assertEqual(tags, ["focus", "focus", "away", "drowsy", "distraction", "focus"])
        self.assertEqual([s["score"] for s in r["slot_scores"]], [0, 2, -3, -2, 0, 0])
        self.assertEqual(r["score_total"], -3)
        self.assertEqual(r["blink_bonus_total"], 3)
        self.assertEqual((r["drowsy_count"], r["away_count"], r["distraction_count"]), (1, 1, 2))
        self.assertFalse(r["llm_call_failed"])

    def test_llm_fail_fallback(self):
        r = self.score(make_log().finalize(START + 60), llm_fail=True)
        self.assertTrue(r["llm_call_failed"])
        self.assertEqual(r["report_text"], FALLBACK_REPORT_TEXT)
        self.assertTrue(all(s["window_label"] == "unknown" for s in r["slot_scores"]))
        self.assertEqual(r["distraction_count"], 0)
        self.assertEqual(r["score_total"], 0 + 2 - 2 - 2 + 1 + 0)

    def test_last_slot_end_is_session_end(self):
        r = self.score([merged(0), merged(1)], session_end=START + 13.5)
        self.assertEqual([(s["start"], s["end"]) for s in r["slot_scores"]],
                         [(START, START + 10), (START + 10, START + 13.5)])

    def test_gap_in_slot_numbers_kept(self):
        r = self.score([merged(0), merged(3)])
        self.assertEqual([s["slot"] for s in r["slot_scores"]], [0, 3])
        self.assertEqual(r["slot_scores"][1]["start"], START + 30)


if __name__ == "__main__":
    unittest.main()
