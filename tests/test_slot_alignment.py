"""슬롯 번호 정합성 (INT-06, 개인 2-06): CV와 창의 같은 slot이 같은 10초 구간인지.

웹캠·Windows API 없이 실제 CvPipeline(CV 쪽 슬롯 압축)과 WindowTracker(창 쪽)를 같은 session_start로 돌려
나온 메시지를 비교한다. 두 모듈 모두 schemas.slot_index()를 쓰므로 번호는 같아야 하고,
session_start가 소수점 있는 실제 epoch 값이어도 경계(정확히 +10초)에서 어긋나지 않아야 한다.
"""
import queue
import unittest

from core.slot_log import SlotLog
from cv_module.pipeline import CvPipeline
from schemas import SLOT_SECONDS, slot_index, slot_start
from window_tracker.tracker import WindowTracker

# 실제 time.time()처럼 소수점이 있는 값. 마지막 것은 float 오차가 나기 쉬운 끝자리
SESSION_STARTS = (1_762_675_200.0, 1_762_675_200.123456, 1_762_675_213.987654321, 1_762_675_299.9999999)
FPS = 15
EAR_OPEN = 0.30
BASELINE = 0.30


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def run_cv(session_start: float, duration: float) -> list[dict]:
    """session_start부터 duration초 동안 FPS로 프레임을 넣고 마지막 슬롯까지 마감한 메시지."""
    pipeline = CvPipeline(session_start, BASELINE)
    out: list[dict] = []
    for k in range(int(duration * FPS)):
        out += pipeline.add_frame(session_start + k / FPS, EAR_OPEN)
    return out + pipeline.finish()


def run_window(session_start: float, duration: float) -> tuple[list[dict], list[float]]:
    """가짜 시계로 트래커를 이 스레드에서 돌린다. 앱 이름에 '세션 시작 후 몇 번째 초'를 넣어 둔다.

    돌려주는 것: (큐 메시지, 조회한 시각 목록)
    """
    q: queue.Queue = queue.Queue()
    clock = FakeClock(session_start)
    sampled: list[float] = []

    def sample(ts: float) -> dict:
        sampled.append(ts)
        return {"timestamp": ts, "app_name": f"s{int(ts - session_start)}", "domain": None,
                "video_category": None, "media_playing": None, "background_media": False,
                "background_category": None}

    def wait(delay: float) -> bool:
        if clock.now + delay >= session_start + duration:
            return True  # 세션 종료: 남은 조회로 마지막 슬롯 flush
        clock.now += delay
        return False

    WindowTracker(q, session_start, sample_fn=sample, clock=clock, wait_fn=wait).run()
    return [q.get_nowait() for _ in range(q.qsize())], sampled


class SlotAlignmentTest(unittest.TestCase):
    DURATION = 35.0  # 10초 슬롯 3개 + 5초짜리 마지막 슬롯

    def test_same_slot_numbers(self):
        for s in SESSION_STARTS:
            with self.subTest(session_start=s):
                cv = [m["slot"]["slot"] for m in run_cv(s, self.DURATION)]
                win = [m["slot"]["slot"] for m in run_window(s, self.DURATION)[0]]
                self.assertEqual(cv, [0, 1, 2, 3])
                self.assertEqual(win, cv)

    def test_cv_seconds_inside_slot_interval(self):
        for s in SESSION_STARTS:
            with self.subTest(session_start=s):
                for msg in run_cv(s, self.DURATION):
                    k = msg["slot"]["slot"]
                    start = slot_start(k, s)
                    self.assertEqual(msg["slot"]["timestamp"], start)
                    times = [sec["timestamp"] for sec in msg["seconds"]]
                    self.assertEqual(len(times), 5 if k == 3 else SLOT_SECONDS)
                    self.assertTrue(all(start <= t < start + SLOT_SECONDS for t in times), times)
                    self.assertTrue(all(slot_index(t, s) == k for t in times))

    def test_window_samples_inside_slot_interval(self):
        for s in SESSION_STARTS:
            with self.subTest(session_start=s):
                messages, sampled = run_window(s, self.DURATION)
                for msg in messages:
                    k = msg["slot"]["slot"]
                    start = slot_start(k, s)
                    in_slot = [t for t in sampled if slot_index(t, s) == k]
                    self.assertEqual(len(in_slot), 5 if k == 3 else SLOT_SECONDS)
                    self.assertTrue(all(start <= t < start + SLOT_SECONDS for t in in_slot))
                    # 창 슬롯 timestamp는 첫 조회 시각 = 슬롯 시작 시각, 대표 창도 그 슬롯의 첫 초
                    self.assertEqual(msg["slot"]["timestamp"], start)
                    self.assertEqual(msg["slot"]["app_name"], f"s{k * SLOT_SECONDS}")

    def test_exact_boundary_belongs_to_next_slot_in_both(self):
        for s in SESSION_STARTS:
            with self.subTest(session_start=s):
                pipeline = CvPipeline(s, BASELINE)
                cv = pipeline.add_frame(s + 9.5, EAR_OPEN) + pipeline.add_frame(s + 10.0, EAR_OPEN)
                cv += pipeline.finish()
                self.assertEqual([m["slot"]["slot"] for m in cv], [0, 1])
                # 창: 10번째 조회(정확히 +10초)가 slot 1의 첫 조회
                messages, sampled = run_window(s, 10.5)
                self.assertEqual(sampled[10] - s, 10.0)
                self.assertEqual([m["slot"]["slot"] for m in messages], [0, 1])
                self.assertEqual(messages[1]["slot"]["app_name"], "s10")

    def test_merged_slots_have_both_parts(self):
        """두 모듈 메시지를 섞어 넣어도 모든 슬롯이 CV·창 값을 함께 갖는다 (한쪽 기본값으로 채워지지 않음)."""
        for s in SESSION_STARTS:
            with self.subTest(session_start=s):
                log = SlotLog(s)
                cv = run_cv(s, self.DURATION)
                win = run_window(s, self.DURATION)[0]
                for msg in [m for pair in zip(win, cv) for m in pair]:  # 창이 먼저 오는 순서
                    log.add(msg)
                merged = log.finalize(s + self.DURATION)
                self.assertEqual([m["slot"] for m in merged], [0, 1, 2, 3])
                for m in merged:
                    self.assertIs(m["face_present"], True)
                    self.assertEqual(m["app_name"], f"s{m['slot'] * SLOT_SECONDS}")
                    self.assertEqual(m["timestamp"], slot_start(m["slot"], s))
                self.assertEqual(len(log.cv_seconds(s + self.DURATION)), int(self.DURATION))


if __name__ == "__main__":
    unittest.main()
