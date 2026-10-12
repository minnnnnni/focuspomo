"""cv_module 슬롯 압축 테스트 (웹캠 없음).

CvPipeline.add_frame(t, ear)에 15fps 가짜 EAR을 넣어
1초 요약 → closed_run → 10초 슬롯 메시지까지 한 번에 검증한다.
"""
import unittest
from collections.abc import Callable

from config.aggregation import BLINK_MAX_SEC, DROWSY_MIN_RUN_SEC, FACE_PRESENT_MIN_RATIO
from core.slot_log import SlotLog
from cv_module.pipeline import CvPipeline
from schemas import SLOT_SECONDS, CvSlotMessage, slot_start

T0 = 1_780_000_000.0
FPS = 15                 # CV 처리 fps (임시 15, 팀 문서 CV-01)
BASELINE = 0.30
OPEN = 0.30              # closure 0
CLOSED = 0.05            # closure 0.83 → 감김
SKIP = object()          # 이 프레임은 아예 안 들어옴 (카메라 멈춤 등)

# 프레임 번호 → EAR(None = 얼굴 없음, SKIP = 프레임 없음)
EarFn = Callable[[int], object]


def frames(sec: float) -> int:
    return round(sec * FPS)


def closed_between(begin: float, end: float, *, otherwise: object = OPEN) -> EarFn:
    """[begin, end)초 동안 감김, 나머지는 otherwise."""
    return lambda i: CLOSED if frames(begin) <= i < frames(end) else otherwise


def feed(ear_fn: EarFn, duration: float, pipeline: CvPipeline | None = None) -> tuple[list[CvSlotMessage], list[CvSlotMessage]]:
    """duration초 분량 프레임을 넣고 (add_frame이 낸 메시지, finish가 낸 메시지)를 돌려준다."""
    p = pipeline or CvPipeline(T0, BASELINE)
    during: list[CvSlotMessage] = []
    for i in range(frames(duration)):
        ear = ear_fn(i)
        if ear is SKIP:
            continue
        during += p.add_frame(T0 + i / FPS, ear)
    return during, p.finish()


def run_all(ear_fn: EarFn, duration: float) -> dict[int, CvSlotMessage]:
    during, last = feed(ear_fn, duration)
    msgs = during + last
    slots = [m["slot"]["slot"] for m in msgs]
    assert slots == sorted(set(slots)), f"슬롯 번호가 중복되거나 순서가 뒤바뀜: {slots}"
    return {m["slot"]["slot"]: m for m in msgs}


class DrowsyAcrossSlotsTest(unittest.TestCase):
    def test_closure_across_slot_boundary(self):
        # ① 8~12초 감김(4초): 슬롯 0에는 2초뿐 → 졸음 아님, 슬롯 1에서 이어 세어 4초 → 졸음
        msgs = run_all(closed_between(8, 12), 20)
        s0, s1 = msgs[0]["slot"], msgs[1]["slot"]
        self.assertFalse(s0["drowsy"])
        self.assertEqual(s0["closed_run_max"], 2)
        self.assertTrue(s1["drowsy"])
        self.assertEqual(s1["closed_run_max"], DROWSY_MIN_RUN_SEC)
        # 긴 감김은 깜빡임으로 세지 않는다
        self.assertEqual(s0["blink_count"] + s1["blink_count"], 0)

    def test_run_shorter_than_threshold_is_not_drowsy(self):
        # ② 기준보다 1초 짧은 감김(3초) → 졸음 아님
        s = run_all(closed_between(3, 3 + DROWSY_MIN_RUN_SEC - 1), 10)[0]["slot"]
        self.assertFalse(s["drowsy"])
        self.assertEqual(s["closed_run_max"], DROWSY_MIN_RUN_SEC - 1)

    def test_run_equal_to_threshold_is_drowsy(self):
        s = run_all(closed_between(3, 3 + DROWSY_MIN_RUN_SEC), 10)[0]["slot"]
        self.assertTrue(s["drowsy"])
        self.assertEqual(s["closed_run_max"], DROWSY_MIN_RUN_SEC)

    def test_open_eyes(self):
        s = run_all(lambda i: OPEN, 10)[0]["slot"]
        self.assertFalse(s["drowsy"])
        self.assertEqual(s["closed_run_max"], 0)
        self.assertEqual(s["blink_count"], 0)
        self.assertTrue(s["face_present"])
        self.assertAlmostEqual(s["ear_mean"], OPEN)
        self.assertIsNone(s["gaze_off_screen"])


class BlinkTest(unittest.TestCase):
    # 감김 길이 = 감긴 프레임 수 / FPS (감김 시작 프레임 ~ 다시 뜬 프레임)
    MAX_BLINK_FRAMES = int(BLINK_MAX_SEC * FPS)   # 15fps에서 7프레임 = 0.47초

    def closed_frames(self, start: int, n: int) -> EarFn:
        return lambda i: CLOSED if start <= i < start + n else OPEN

    def test_short_closure_is_blink(self):
        # ③ 0.5초 이하 감김 → 깜빡임 1회
        for n in (1, 3, self.MAX_BLINK_FRAMES):
            with self.subTest(frames=n):
                s = run_all(self.closed_frames(frames(2.2), n), 10)[0]["slot"]
                self.assertEqual(s["blink_count"], 1)
                self.assertFalse(s["drowsy"])
                self.assertEqual(s["closed_run_max"], 0)

    def test_longer_closure_is_not_blink(self):
        s = run_all(self.closed_frames(frames(2.2), self.MAX_BLINK_FRAMES + 1), 10)[0]["slot"]
        self.assertEqual(s["blink_count"], 0)

    def test_several_blinks_are_summed(self):
        blink_starts = (frames(1.2), frames(3.2), frames(5.2))
        ear_fn = lambda i: CLOSED if any(b <= i < b + 3 for b in blink_starts) else OPEN
        self.assertEqual(run_all(ear_fn, 10)[0]["slot"]["blink_count"], 3)

    def test_blink_counted_in_slot_where_it_ends(self):
        # 9.87초에 감기 시작해 10.13초에 뜸 → 끝난 쪽(슬롯 1)에 센다
        msgs = run_all(self.closed_frames(frames(SLOT_SECONDS) - 2, 4), 20)
        self.assertEqual(msgs[0]["slot"]["blink_count"], 0)
        self.assertEqual(msgs[1]["slot"]["blink_count"], 1)

    def test_closure_interrupted_by_lost_face_is_not_blink(self):
        # 감은 채로 얼굴을 놓치면 그 감김은 버린다
        start = frames(2.2)
        ear_fn = lambda i: CLOSED if start <= i < start + 2 else (None if i == start + 2 else OPEN)
        self.assertEqual(run_all(ear_fn, 10)[0]["slot"]["blink_count"], 0)


class ClosedRunResetTest(unittest.TestCase):
    # ④ 감김 2초 → (끊김 1초) → 감김 2초: 끊기지 않았다면 4초로 졸음이었을 상황

    def test_missing_second_resets_run(self):
        ear_fn = closed_between(2, 6)
        gap = lambda i: SKIP if frames(4) <= i < frames(5) else ear_fn(i)
        during, last = feed(gap, 10)
        msg = (during + last)[0]
        s = msg["slot"]
        self.assertEqual(len(msg["seconds"]), SLOT_SECONDS - 1)   # 빠진 초는 요약이 없다
        self.assertEqual(s["closed_run_max"], 2)
        self.assertFalse(s["drowsy"])

    def test_no_face_second_resets_run(self):
        ear_fn = closed_between(2, 6)
        no_face = lambda i: None if frames(4) <= i < frames(5) else ear_fn(i)
        msg = run_all(no_face, 10)[0]
        s = msg["slot"]
        self.assertFalse(msg["seconds"][4]["face_present"])
        self.assertFalse(msg["seconds"][4]["eyes_closed"])
        self.assertEqual(s["closed_run_max"], 2)
        self.assertFalse(s["drowsy"])
        self.assertTrue(s["face_present"])   # 10초 중 9초 얼굴 있음

    def test_without_gap_would_be_drowsy(self):
        # 위 두 테스트의 대조군
        self.assertTrue(run_all(closed_between(2, 6), 10)[0]["slot"]["drowsy"])


class DrowsyAndAwayTest(unittest.TestCase):
    def test_drowsy_then_face_lost_in_same_slot(self):
        # ⑤ v8 회귀: 감김 4초 뒤 얼굴 없음 6초 → face_present=False, drowsy=True (동시 감점 허용)
        ear_fn = closed_between(0, DROWSY_MIN_RUN_SEC, otherwise=None)
        s = run_all(ear_fn, SLOT_SECONDS)[0]["slot"]
        self.assertLess(DROWSY_MIN_RUN_SEC / SLOT_SECONDS, FACE_PRESENT_MIN_RATIO)
        self.assertIs(s["face_present"], False)
        self.assertTrue(s["drowsy"])
        self.assertEqual(s["closed_run_max"], DROWSY_MIN_RUN_SEC)
        self.assertAlmostEqual(s["ear_mean"], CLOSED)   # 얼굴 있던 초만 평균

    def test_no_face_whole_slot(self):
        s = run_all(lambda i: None, SLOT_SECONDS)[0]["slot"]
        self.assertIs(s["face_present"], False)
        self.assertFalse(s["drowsy"])
        self.assertIsNone(s["ear_mean"])
        self.assertEqual(s["blink_count"], 0)


class FinishTest(unittest.TestCase):
    def test_finish_flushes_short_last_slot(self):
        # ⑥ 23초 세션: 슬롯 0·1은 도중에, 3초짜리 슬롯 2는 finish()에서 나온다
        during, last = feed(lambda i: OPEN, 23)
        self.assertEqual([m["slot"]["slot"] for m in during], [0, 1])
        self.assertEqual([m["slot"]["slot"] for m in last], [2])
        tail = last[0]
        self.assertEqual(tail["source"], "cv")
        self.assertEqual(len(tail["seconds"]), 3)
        self.assertEqual(tail["slot"]["timestamp"], slot_start(2, T0))
        self.assertEqual([s["timestamp"] for s in tail["seconds"]], [T0 + 20, T0 + 21, T0 + 22])

    def test_every_slot_has_ten_seconds_until_last(self):
        during, last = feed(lambda i: OPEN, 23)
        for m in during:
            self.assertEqual(len(m["seconds"]), SLOT_SECONDS)
            self.assertEqual(m["slot"]["timestamp"], slot_start(m["slot"]["slot"], T0))

    def test_finish_on_exact_slot_boundary(self):
        # 20초 딱 맞게 끝나면 슬롯 1이 finish()에서 나오고 빈 슬롯 2는 없다
        during, last = feed(lambda i: OPEN, 20)
        self.assertEqual([m["slot"]["slot"] for m in during], [0])
        self.assertEqual([m["slot"]["slot"] for m in last], [1])
        self.assertEqual(len(last[0]["seconds"]), SLOT_SECONDS)

    def test_finish_without_frames(self):
        self.assertEqual(CvPipeline(T0, BASELINE).finish(), [])

    def test_frames_before_session_start_are_ignored(self):
        p = CvPipeline(T0, BASELINE)
        self.assertEqual(p.add_frame(T0 - 0.5, CLOSED), [])
        self.assertEqual(p.finish(), [])

    def test_skipped_slot_sends_no_message(self):
        # 10~20초 프레임 없음 → 슬롯 1 메시지 없음 (GUI가 종료 시 기본값으로 채움)
        ear_fn = lambda i: SKIP if frames(10) <= i < frames(20) else OPEN
        self.assertEqual(sorted(run_all(ear_fn, 30)), [0, 2])


class SlotLogCompatTest(unittest.TestCase):
    def test_pipeline_messages_merge_into_slot_log(self):
        # CV 메시지를 그대로 GUI 슬롯 로그에 넣어도 병합된다 (창 쪽은 기본값)
        during, last = feed(closed_between(8, 12), 23)
        log = SlotLog(T0)
        for msg in during + last:
            log.add(msg)
        merged = log.finalize()
        self.assertEqual([r["slot"] for r in merged], [0, 1, 2])
        self.assertTrue(merged[1]["drowsy"])
        self.assertEqual(len(log.cv_seconds()), 23)


if __name__ == "__main__":
    unittest.main()
