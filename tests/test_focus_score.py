"""scoring/focus_score.py 테스트 (SCORE-01, 집중도 점수 산출 명세 3·4·6·7·8장).

LLM 라벨은 테스트에서 직접 넘긴다 (mock 앱 목록과 무관하게 규칙만 확인).
"""
import copy
import unittest

from config.scoring import BLINK_RATE_MIN_SEC, CAM_USABLE_RATIO, MIN_JUDGED_SEC, TOP_DISTRACTIONS_N
from mocks.mock_calibration import make_calibration
from schemas import (
    FINAL_TAGS,
    SCHEMA_VERSION,
    SLOT_SECONDS,
    SessionResult,
    SlotScore,
    empty_cv_part,
    empty_window_part,
)
from scoring.focus_score import (
    INSUFFICIENT_NOTE,
    SCORE_NOTES,
    choose_mode,
    focus_score,
    longest_focus,
    round_half_up,
    score_session,
)

START = 1_790_753_400.0
CODE = ("Code.exe", None, None)
SO = ("chrome.exe", "stackoverflow.com", None)
GAME = ("chrome.exe", "youtube.com", "Gaming")
LABELS = {CODE: "work", SO: "work", GAME: "distraction"}


def slot(n: int, window=CODE, face=True, **fields) -> dict:
    """얼굴 있고(face=True) 졸지 않은 슬롯. window=None이면 창 조회 실패."""
    row = {"slot": n, "timestamp": START + n * SLOT_SECONDS, **empty_cv_part(), **empty_window_part()}
    row["face_present"] = face
    if window is not None:
        row["app_name"], row["domain"], row["video_category"] = window
    row.update(fields)
    return row


def run(slots, labels=LABELS, session_end=None, pauses=None, report_text="문구") -> SessionResult:
    if session_end is None:
        session_end = START + (max((s["slot"] for s in slots), default=-1) + 1) * SLOT_SECONDS
    return score_session(slots, make_calibration(START - 5.0, face_detected_ratio=1.0), pauses or [],
                         session_id="20261109-100000", session_start=START, session_end=session_end,
                         llm_labels=labels, report_text=report_text)


def tags(result: SessionResult, key: str = "final_tag") -> list[str]:
    return [s[key] for s in result["slot_scores"]]


class ResultShapeTest(unittest.TestCase):
    def test_keys_match_schema(self):
        r = run([slot(i) for i in range(6)])
        self.assertEqual(set(r), set(SessionResult.__annotations__))
        self.assertEqual(set(r["slot_scores"][0]), set(SlotScore.__annotations__))
        self.assertEqual(set(r["tag_seconds"]), set(FINAL_TAGS))
        self.assertEqual(r["schema_version"], SCHEMA_VERSION)

    def test_meta_copied(self):
        pauses = [{"start": START + 100, "end": START + 130}]
        r = run([slot(0)], pauses=pauses, report_text="안녕")
        self.assertEqual((r["session_id"], r["session_start"], r["report_text"]), ("20261109-100000", START, "안녕"))
        self.assertEqual(r["pause_intervals"], pauses)
        self.assertIsNot(r["pause_intervals"][0], pauses[0])
        self.assertEqual(r["ear_baseline"], make_calibration(START - 5.0, face_detected_ratio=1.0)["ear_baseline"])

    def test_inputs_not_mutated(self):
        slots = [slot(3), slot(1, GAME), slot(2)]
        before = copy.deepcopy(slots)
        run(slots, session_end=START + 40)
        self.assertEqual(slots, before)

    def test_slots_sorted_by_number(self):
        r = run([slot(2), slot(0), slot(1)])
        self.assertEqual([s["slot"] for s in r["slot_scores"]], [0, 1, 2])

    def test_empty_session(self):
        r = run([], session_end=START)
        self.assertEqual(r["slot_scores"], [])
        self.assertIsNone(r["focus_score"])
        self.assertEqual(r["score_mode"], "window_only")  # 웹캠 측정 0초 → 웹캠 X, LLM은 성공
        self.assertEqual((r["judged_seconds"], r["longest_focus_seconds"]), (0, 0))
        self.assertEqual(r["top_distractions"], [])
        self.assertIsNone(r["blinks_per_minute"])

    def test_deterministic(self):
        slots = [slot(i, GAME if i % 3 == 0 else CODE) for i in range(12)]
        self.assertEqual(run(slots), run(slots))


class TagTest(unittest.TestCase):
    """3장 우선순위: no_cam > away > drowsy > distraction > focus > unknown."""

    def test_priority(self):
        slots = [
            slot(0, face=None),                       # 카메라 측정 불가
            slot(1, GAME, face=False),                # 자리비움이 딴짓보다 우선
            slot(2, GAME, drowsy=True),               # 졸음이 딴짓보다 우선
            slot(3, GAME),
            slot(4, CODE),
            slot(5, ("Notepad.exe", None, None)),     # LLM 응답에 없는 창
            slot(6, None),                            # 창 조회 실패
        ] + [slot(i) for i in range(7, 12)]
        r = run(slots)
        self.assertEqual(tags(r)[:7], ["no_cam", "away", "drowsy", "distraction", "focus", "unknown", "unknown"])

    def test_rule_distraction_beats_llm_label(self):
        r = run([slot(0, CODE, rule_distraction="foreground")])
        self.assertEqual(r["slot_scores"][0]["window_label"], "distraction")
        self.assertEqual(r["slot_scores"][0]["rule_distraction"], "foreground")

    def test_invalid_llm_label_is_unknown(self):
        r = run([slot(0)], labels={CODE: "공부"})
        self.assertEqual(r["slot_scores"][0]["window_label"], "unknown")

    def test_slot_fields_copied(self):
        r = run([slot(0, GAME, blink_count=4)])
        s = r["slot_scores"][0]
        self.assertEqual((s["app_name"], s["domain"], s["video_category"], s["blink_count"]),
                         ("chrome.exe", "youtube.com", "Gaming", 4))


class NoGraceTest(unittest.TestCase):
    """4장: 유예 보정 없음. final_tag는 항상 raw_tag."""

    def test_short_lapses_not_corrected(self):
        cases = [
            [slot(0), slot(1), slot(2, GAME), slot(3), slot(4)],           # 집중·집중·딴짓·집중·집중
            [slot(0), slot(1, drowsy=True), slot(2)],                      # 집중·졸음·집중
            [slot(0), slot(1, face=False), slot(2)],                       # 집중·자리비움·집중
            [slot(0), slot(1, CODE, rule_distraction="foreground"), slot(2)],
        ]
        for slots in cases:
            with self.subTest(raw=[s["slot"] for s in slots]):
                r = run(slots)
                self.assertEqual(tags(r), tags(r, "raw_tag"))
                self.assertNotEqual(tags(r)[1 if len(slots) == 3 else 2], "focus")

    def test_alternating_distraction_counts(self):
        # 10초씩 번갈아 딴짓: 집중 5 / 판정 10 → 50점 (예전 유예 보정이었다면 마지막 1칸만 남아 90점)
        r = run([slot(i, GAME if i % 2 else CODE) for i in range(10)])
        self.assertEqual(r["focus_score"], 50)


class SecondsTest(unittest.TestCase):
    def test_last_slot_ends_at_session_end(self):
        r = run([slot(0), slot(1)], session_end=START + 13.5)
        self.assertEqual([(s["start"], s["end"]) for s in r["slot_scores"]],
                         [(START, START + 10), (START + 10, START + 13.5)])

    def test_slot_after_session_end_has_zero_length(self):
        r = run([slot(0), slot(1)], session_end=START + 8)
        self.assertEqual([s["end"] - s["start"] for s in r["slot_scores"]], [8, 0])

    def test_gap_in_slot_numbers(self):
        r = run([slot(0), slot(3)], session_end=START + 40)
        self.assertEqual([(s["slot"], s["start"]) for s in r["slot_scores"]], [(0, START), (3, START + 30)])

    def test_tag_seconds_rounded_half_up(self):
        # 집중 60초 + 짧은 마지막 딴짓 slot 2.5초 → 딴짓 2.5 → 3
        slots = [slot(i) for i in range(6)] + [slot(6, GAME)]
        r = run(slots, session_end=START + 62.5)
        self.assertEqual(r["tag_seconds"]["focus"], 60)
        self.assertEqual(r["tag_seconds"]["distraction"], 3)
        self.assertEqual(r["judged_seconds"], 63)  # 62.5 → 63
        self.assertEqual(r["tag_seconds"]["away"], 0)


class FocusScoreTest(unittest.TestCase):
    def test_spec_example(self):
        # 6장: 집중 105, 딴짓 15, 자리비움 9, 졸음 6, 미분류 15슬롯 → 78점
        t = ["focus"] * 105 + ["distraction"] * 15 + ["away"] * 9 + ["drowsy"] * 6 + ["unknown"] * 15
        self.assertEqual(focus_score([10.0] * len(t), t), 78)

    def test_spec_example_end_to_end(self):
        slots = ([slot(i) for i in range(105)]
                 + [slot(i, GAME) for i in range(105, 120)]
                 + [slot(i, face=False) for i in range(120, 129)]
                 + [slot(i, drowsy=True) for i in range(129, 135)]
                 + [slot(i, None) for i in range(135, 150)])
        r = run(slots)
        self.assertEqual(r["focus_score"], 78)
        self.assertEqual(r["judged_seconds"], 1350)
        self.assertEqual(r["tag_seconds"], {"focus": 1050, "distraction": 150, "away": 90, "drowsy": 60,
                                            "unknown": 150, "no_cam": 0})
        self.assertIsNone(r["score_note"])

    def test_seconds_not_slot_count(self):
        t = ["focus"] * 6 + ["distraction"] * 7
        self.assertEqual(focus_score([10.0] * 12 + [2.0], t), round_half_up(100 * 60 / 122))

    def test_unknown_and_no_cam_excluded_from_denominator(self):
        t = ["focus"] * 6 + ["unknown"] * 4 + ["no_cam"] * 4
        self.assertEqual(focus_score([10.0] * len(t), t), 100)

    def test_insufficient_by_seconds(self):
        self.assertIsNone(focus_score([10.0] * 5, ["focus"] * 5))  # 50초 < 60초
        self.assertEqual(focus_score([10.0] * 6, ["focus"] * 6), 100)  # 경계: 60초면 계산
        self.assertEqual(MIN_JUDGED_SEC, 60)

    def test_insufficient_by_ratio(self):
        t = ["focus"] * 8 + ["unknown"] * 22  # 판정 80초 < 300초의 30%
        self.assertIsNone(focus_score([10.0] * 30, t))
        t = ["focus"] * 9 + ["unknown"] * 21  # 90초 = 30% → 계산
        self.assertEqual(focus_score([10.0] * 30, t), 100)

    def test_round_half_up(self):
        self.assertEqual(round_half_up(78.5), 79)
        self.assertEqual(round_half_up(78.49), 78)
        self.assertEqual(round_half_up(0.5), 1)

    def test_insufficient_note_beats_mode_note(self):
        r = run([slot(i, None) for i in range(10)])  # 전부 미분류
        self.assertEqual(r["score_mode"], "full")
        self.assertIsNone(r["focus_score"])
        self.assertEqual(r["score_note"], INSUFFICIENT_NOTE)


class ModeTest(unittest.TestCase):
    """7장 점수 모드와 폴백."""

    def test_choose_mode_boundary(self):
        n = 10
        cam = int(CAM_USABLE_RATIO * n)  # 30% 측정 → 웹캠 사용 가능
        slots = [slot(i) for i in range(cam)] + [slot(i, face=None) for i in range(cam, n)]
        self.assertEqual(choose_mode(slots, [10.0] * n, llm_ok=True), "full")
        slots = [slot(i) for i in range(cam - 1)] + [slot(i, face=None) for i in range(cam - 1, n)]
        self.assertEqual(choose_mode(slots, [10.0] * n, llm_ok=True), "window_only")

    def test_full(self):
        r = run([slot(i, GAME if i in (2, 3) else CODE) for i in range(10)])
        self.assertEqual((r["score_mode"], r["focus_score"], r["score_note"]), ("full", 80, None))
        self.assertFalse(r["llm_call_failed"])

    def test_cam_only_when_llm_failed(self):
        slots = [slot(0, GAME), slot(1, None), slot(2, rule_distraction="foreground"), slot(3, face=False)] \
            + [slot(i) for i in range(4, 10)]
        r = run(slots, labels=None)
        self.assertEqual(r["score_mode"], "cam_only")
        self.assertTrue(r["llm_call_failed"])
        self.assertEqual(r["score_note"], SCORE_NOTES["cam_only"])
        # LLM 딴짓(GAME)·창 조회 실패도 얼굴 있고 졸지 않으면 집중, 규칙상 딴짓만 딴짓으로 남음
        self.assertEqual(tags(r)[:4], ["focus", "focus", "distraction", "away"])
        self.assertEqual(r["slot_scores"][0]["window_label"], "unknown")
        self.assertEqual(r["focus_score"], 80)

    def test_window_only_without_camera(self):
        slots = [slot(i, GAME if i == 0 else CODE, face=None) for i in range(10)]
        r = run(slots)
        self.assertEqual(r["score_mode"], "window_only")
        self.assertEqual(tags(r)[:2], ["distraction", "focus"])  # no_cam 판정을 건너뜀
        self.assertEqual((r["focus_score"], r["score_note"]), (90, SCORE_NOTES["window_only"]))

    def test_none_without_camera_and_llm(self):
        r = run([slot(i, face=None) for i in range(10)], labels=None)
        self.assertEqual(r["score_mode"], "none")
        self.assertIsNone(r["focus_score"])
        self.assertEqual(r["score_note"], SCORE_NOTES["none"])
        self.assertTrue(r["llm_call_failed"])

    def test_empty_labels_is_llm_success(self):
        # 보낼 창이 없어 호출하지 않음 → 성공 (llm_call_failed False)
        r = run([slot(i, None) for i in range(10)], labels={})
        self.assertEqual(r["score_mode"], "full")
        self.assertFalse(r["llm_call_failed"])


class AggregateTest(unittest.TestCase):
    """8장 집계값."""

    def test_top_distractions_sorted_and_limited(self):
        apps = [("a.exe", None, None), ("b.exe", None, None), ("c.exe", None, None), ("d.exe", None, None)]
        labels = {w: "distraction" for w in apps} | {CODE: "work"}
        counts = [1, 3, 2, 4]
        slots, n = [], 0
        for w, c in zip(apps, counts):
            for _ in range(c):
                slots.append(slot(n, w))
                n += 1
        r = run(slots, labels=labels)
        self.assertEqual(len(r["top_distractions"]), TOP_DISTRACTIONS_N)
        self.assertEqual([(d["app_name"], d["seconds"]) for d in r["top_distractions"]],
                         [("d.exe", 40), ("b.exe", 30), ("c.exe", 20)])

    def test_top_distractions_separate_video_category(self):
        study = ("chrome.exe", "youtube.com", "Education")
        r = run([slot(0, GAME), slot(1, study), slot(2, GAME)], labels=LABELS | {study: "distraction"})
        self.assertEqual([(d["video_category"], d["seconds"]) for d in r["top_distractions"]],
                         [("Gaming", 20), ("Education", 10)])

    def test_background_grouped_by_category(self):
        slots = [slot(i) for i in range(6)]
        for i in (2, 3):
            slots[i].update(rule_distraction="background", background_category="Gaming", background_media_sec=10)
        r = run(slots)
        self.assertEqual(r["top_distractions"],
                         [{"app_name": None, "domain": None, "video_category": "Gaming",
                           "background": True, "seconds": 20}])

    def test_away_slot_not_in_top_distractions(self):
        r = run([slot(0, GAME, face=False), slot(1)])
        self.assertEqual(r["top_distractions"], [])

    def test_longest_focus_breaks_on_gap_and_lapse(self):
        slots = [slot(0), slot(1), slot(2, GAME), slot(3), slot(4), slot(5), slot(8), slot(9)]
        r = run(slots, session_end=START + 100)
        self.assertEqual(r["longest_focus_seconds"], 30)  # 3·4·5
        self.assertEqual(longest_focus([], []), 0.0)

    def test_longest_focus_pause_gap(self):
        r = run([slot(0), slot(1), slot(5), slot(6), slot(7)], session_end=START + 80)
        self.assertEqual(r["longest_focus_seconds"], 30)

    def test_blinks_per_minute_uses_face_present_time(self):
        slots = [slot(i, blink_count=3) for i in range(6)] + [slot(6, face=False, blink_count=9)]
        r = run(slots)
        self.assertEqual(r["blinks_per_minute"], 18.0)  # 18회 / 1분 (자리비움 슬롯 제외)

    def test_blinks_per_minute_none_when_short(self):
        n = BLINK_RATE_MIN_SEC // SLOT_SECONDS - 1
        self.assertIsNone(run([slot(i, blink_count=3) for i in range(n)])["blinks_per_minute"])


if __name__ == "__main__":
    unittest.main()
