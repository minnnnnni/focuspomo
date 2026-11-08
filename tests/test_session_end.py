"""core/session_end.py, mocks/mock_scoring.py 테스트 (세션 종료 흐름 1-13, 집중도 점수 산출 명세)."""
import unittest

from core.session_end import finish_session, slots_for_scoring
from core.slot_log import SlotLog
from mocks.mock_calibration import make_calibration
from mocks.mock_scoring import (
    FALLBACK_REPORT_TEXT,
    INSUFFICIENT_NOTE,
    SCORE_NOTES,
    MockScorer,
    apply_grace,
    focus_score,
    round_half_up,
)
from mocks.mock_session import build_scenario, messages_only
from mocks.mock_window_slots import APP_VSCODE, APP_YOUTUBE
from schemas import (
    FINAL_TAGS,
    SCHEMA_VERSION,
    SLOT_SECONDS,
    SessionResult,
    SlotScore,
    empty_cv_part,
    empty_window_part,
    make_session_id,
)

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
        self.assertIsNone(out.result["focus_score"])
        # 카메라 기록이 전혀 없으면 window_only라 웹캠 조건을 건너뛰고, 창도 없으니 전부 미분류
        self.assertEqual(out.result["score_mode"], "window_only")
        self.assertTrue(all(s["final_tag"] == "unknown" for s in out.result["slot_scores"]))

    def test_empty_session(self):
        out = finish_session(SlotLog(START), calibration(), [], START, MockScorer())
        self.assertEqual(out.result["slot_scores"], [])
        self.assertIsNone(out.result["focus_score"])
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


def focus_slot(slot: int, app=APP_VSCODE, **fields) -> dict:
    """얼굴 있고 졸지 않은 슬롯."""
    app_name, domain = app
    return merged(slot, face_present=True, app_name=app_name, domain=domain, **fields)


class GraceTest(unittest.TestCase):
    """명세 4장 예시 표."""

    def test_spec_examples(self):
        F, D, A, Z, U = "focus", "distraction", "away", "drowsy", "unknown"
        cases = [
            ([3, 4, 5, 6, 7], [F, F, D, F, F], [F, F, F, F, F]),   # 10초 이탈 → 보정
            ([3, 4, 5, 6], [F, D, D, F], [F, D, D, F]),             # 20초 → 이탈 인정
            ([3, 4, 5, 6], [F, A, Z, F], [F, A, Z, F]),             # 종류가 달라도 연속 2슬롯
            ([3, 4, 5], [U, D, F], [U, D, F]),                      # 앞 이웃이 집중 아님
            ([3, 9, 10], [F, D, F], [F, D, F]),                     # 앞 이웃이 정지로 빠짐
            ([0, 1], [D, F], [D, F]),                               # 세션 첫 슬롯
            ([0, 1], [F, D], [F, D]),                               # 세션 마지막 슬롯
        ]
        for slots, raw, expected in cases:
            with self.subTest(raw=raw, slots=slots):
                self.assertEqual(apply_grace(slots, raw), expected)


class FocusScoreTest(unittest.TestCase):
    def test_spec_example(self):
        # 명세 6장: 집중 105, 딴짓 15, 자리비움 9, 졸음 6, 미분류 15슬롯 → 78점
        tags = ["focus"] * 105 + ["distraction"] * 15 + ["away"] * 9 + ["drowsy"] * 6 + ["unknown"] * 15
        self.assertEqual(focus_score([10.0] * len(tags), tags), 78)

    def test_seconds_not_slot_count(self):
        # 마지막 슬롯이 짧으면 그만큼만 센다: 집중 60초, 딴짓 60 + 2초 → 60/122
        tags = ["focus"] * 6 + ["distraction"] * 7
        self.assertEqual(focus_score([10.0] * 12 + [2.0], tags), round_half_up(100 * 60 / 122))

    def test_insufficient_data(self):
        self.assertIsNone(focus_score([10.0] * 5, ["focus"] * 5))  # 판정 50초 < 60초
        tags = ["focus"] * 8 + ["unknown"] * 22  # 판정 80초 < 세션 300초의 30%
        self.assertIsNone(focus_score([10.0] * 30, tags))

    def test_round_half_up(self):
        self.assertEqual(round_half_up(78.5), 79)
        self.assertEqual(round_half_up(78.49), 78)


class MockScorerTest(unittest.TestCase):
    def score(self, slots, llm_fail=False, session_end=START + 60):
        return MockScorer(llm_fail)(slots, calibration(), [], session_id="20261003-100000",
                                    session_start=START, session_end=session_end)

    def test_result_keys_match_schema(self):
        r = self.score(make_log().finalize(START + 60))
        self.assertEqual(set(r), set(SessionResult.__annotations__))
        self.assertEqual(set(r["slot_scores"][0]), set(SlotScore.__annotations__))
        self.assertEqual(set(r["tag_seconds"]), set(FINAL_TAGS))

    def test_normal_scenario(self):
        r = self.score(make_log().finalize(START + 60))
        tags = [s["final_tag"] for s in r["slot_scores"]]
        self.assertEqual(tags, ["focus", "focus", "away", "drowsy", "distraction", "focus"])
        self.assertEqual(r["score_mode"], "full")
        self.assertEqual(r["focus_score"], 50)  # 집중 30초 / 판정 60초
        self.assertIsNone(r["score_note"])
        self.assertEqual(r["judged_seconds"], 60)
        self.assertEqual((r["tag_seconds"]["focus"], r["tag_seconds"]["away"]), (30, 10))
        self.assertEqual(r["longest_focus_seconds"], 20)
        self.assertEqual(r["top_distractions"],
                         [{"app_name": "Chrome", "domain": "youtube.com", "video_category": None,
                           "background": False, "seconds": 10}])
        self.assertFalse(r["llm_call_failed"])

    def test_grace_applied_to_final_only(self):
        slots = [focus_slot(0), focus_slot(1, APP_YOUTUBE)] + [focus_slot(i) for i in range(2, 6)]
        r = self.score(slots)
        self.assertEqual(r["slot_scores"][1]["raw_tag"], "distraction")
        self.assertEqual(r["slot_scores"][1]["final_tag"], "focus")
        self.assertEqual(r["focus_score"], 100)

    def test_rule_distraction_beats_llm(self):
        slots = [focus_slot(i) for i in range(6)]
        slots[2]["rule_distraction"] = slots[3]["rule_distraction"] = "foreground"  # VS Code여도 규칙상 딴짓
        r = self.score(slots)
        self.assertEqual([s["window_label"] for s in r["slot_scores"]][2:4], ["distraction", "distraction"])
        self.assertEqual(r["focus_score"], round_half_up(100 * 4 / 6))

    def test_background_distraction_grouped_by_category(self):
        slots = [focus_slot(i) for i in range(6)]
        for i in (2, 3):
            slots[i].update(rule_distraction="background", background_category="Gaming", background_media_sec=10)
        r = self.score(slots)
        self.assertEqual(r["top_distractions"],
                         [{"app_name": None, "domain": None, "video_category": "Gaming",
                           "background": True, "seconds": 20}])

    def test_llm_fail_is_cam_only(self):
        r = self.score(make_log().finalize(START + 60), llm_fail=True)
        self.assertTrue(r["llm_call_failed"])
        self.assertEqual(r["report_text"], FALLBACK_REPORT_TEXT)
        self.assertEqual(r["score_mode"], "cam_only")
        self.assertEqual(r["score_note"], SCORE_NOTES["cam_only"])
        self.assertTrue(all(s["window_label"] == "unknown" for s in r["slot_scores"]))
        # 얼굴 있고 졸지 않은 슬롯은 창과 무관하게 집중: focus, focus, away, drowsy, focus, focus
        self.assertEqual(r["focus_score"], round_half_up(100 * 4 / 6))

    def test_no_camera_is_window_only(self):
        r = self.score([merged(i, app_name="VS Code") for i in range(6)])
        self.assertEqual(r["score_mode"], "window_only")
        self.assertEqual(r["focus_score"], 100)
        self.assertEqual(r["score_note"], SCORE_NOTES["window_only"])

    def test_no_camera_and_llm_fail_is_none(self):
        r = self.score([merged(i, app_name="VS Code") for i in range(6)], llm_fail=True)
        self.assertEqual(r["score_mode"], "none")
        self.assertIsNone(r["focus_score"])
        self.assertEqual(r["score_note"], SCORE_NOTES["none"])

    def test_insufficient_note(self):
        r = self.score([focus_slot(i, app=(None, None)) for i in range(6)])  # 창 조회 실패 → 전부 미분류
        self.assertIsNone(r["focus_score"])
        self.assertEqual(r["score_note"], INSUFFICIENT_NOTE)

    def test_blinks_per_minute(self):
        r = self.score([focus_slot(i, blink_count=3) for i in range(6)])
        self.assertEqual(r["blinks_per_minute"], 18.0)
        self.assertIsNone(self.score([focus_slot(i) for i in range(5)])["blinks_per_minute"])  # 50초 < 60초

    def test_last_slot_end_is_session_end(self):
        r = self.score([merged(0), merged(1)], session_end=START + 13.5)
        self.assertEqual([(s["start"], s["end"]) for s in r["slot_scores"]],
                         [(START, START + 10), (START + 10, START + 13.5)])

    def test_gap_in_slot_numbers_kept(self):
        r = self.score([merged(0), merged(3)])
        self.assertEqual([s["slot"] for s in r["slot_scores"]], [0, 3])
        self.assertEqual(r["slot_scores"][1]["start"], START + 30)

    def test_pause_gap_breaks_longest_focus(self):
        r = self.score([focus_slot(0), focus_slot(1), focus_slot(5), focus_slot(6), focus_slot(7)],
                       session_end=START + 80)
        self.assertEqual(r["longest_focus_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
