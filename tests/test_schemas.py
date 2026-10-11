"""schemas.py 일시정지 헬퍼 테스트 (schema_version 2)."""
import unittest

from schemas import SLOT_SECONDS, slot_overlaps_pause, pause_total

START = 1_000_000.0


def pause(start: float, end: float) -> dict:
    return {"start": START + start, "end": START + end}


class SlotOverlapsPauseTest(unittest.TestCase):
    def test_no_pause(self):
        self.assertFalse(slot_overlaps_pause(0, START, []))

    def test_pause_inside_slot(self):
        self.assertTrue(slot_overlaps_pause(1, START, [pause(12, 15)]))

    def test_slightly_overlapping_is_excluded(self):
        # 슬롯 0(0~10초)의 마지막 0.1초만 겹쳐도 겹친 것으로 본다
        self.assertTrue(slot_overlaps_pause(0, START, [pause(9.9, 30)]))
        self.assertTrue(slot_overlaps_pause(3, START, [pause(9.9, 30.1)]))

    def test_long_pause_covers_many_slots(self):
        intervals = [pause(15, 42)]
        self.assertEqual(
            [s for s in range(6) if slot_overlaps_pause(s, START, intervals)],
            [1, 2, 3, 4],
        )

    def test_touching_boundary_does_not_overlap(self):
        # 10초에 정지, 20초에 재개 → 슬롯 1만 겹치고 슬롯 0·2는 맞닿기만 한다
        intervals = [pause(SLOT_SECONDS, 2 * SLOT_SECONDS)]
        self.assertFalse(slot_overlaps_pause(0, START, intervals))
        self.assertTrue(slot_overlaps_pause(1, START, intervals))
        self.assertFalse(slot_overlaps_pause(2, START, intervals))

    def test_any_of_several_pauses(self):
        intervals = [pause(3, 4), pause(51, 52)]
        self.assertTrue(slot_overlaps_pause(0, START, intervals))
        self.assertFalse(slot_overlaps_pause(2, START, intervals))
        self.assertTrue(slot_overlaps_pause(5, START, intervals))


class PauseTotalTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(pause_total([]), 0)

    def test_sum(self):
        self.assertAlmostEqual(pause_total([pause(10, 15), pause(35, 42.5)]), 12.5)


if __name__ == "__main__":
    unittest.main()
