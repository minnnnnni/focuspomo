"""core/calibration.py 판정 테스트 (GUI 없음)."""
import unittest

from config.aggregation import CALIBRATION_MIN_FACE_RATIO
from core.calibration import MAX_ATTEMPTS, Outcome, decide, is_successful
from mocks.mock_calibration import BAD_FACE_RATIO, GOOD_FACE_RATIO, make_calibration

T0 = 1_700_000_000.0
PREVIOUS = 0.28


class DecideTest(unittest.TestCase):
    def test_success_keeps_measurement(self):
        c = make_calibration(T0, GOOD_FACE_RATIO)
        d = decide(c, 1, None)
        self.assertEqual(d.outcome, Outcome.SUCCESS)
        self.assertIs(d.calibration, c)
        self.assertTrue(d.starts_session)

    def test_borderline_is_success(self):
        c = make_calibration(T0, CALIBRATION_MIN_FACE_RATIO)
        self.assertTrue(is_successful(c))
        self.assertEqual(decide(c, 1, None).outcome, Outcome.SUCCESS)

    def test_success_on_retry(self):
        d = decide(make_calibration(T0, GOOD_FACE_RATIO), MAX_ATTEMPTS, None)
        self.assertEqual(d.outcome, Outcome.SUCCESS)

    def test_first_failure_retries(self):
        d = decide(make_calibration(T0, BAD_FACE_RATIO), 1, PREVIOUS)
        self.assertEqual(d.outcome, Outcome.RETRY)
        self.assertIsNone(d.calibration)
        self.assertFalse(d.starts_session)

    def test_second_failure_falls_back(self):
        c = make_calibration(T0, BAD_FACE_RATIO)
        d = decide(c, MAX_ATTEMPTS, PREVIOUS)
        self.assertEqual(d.outcome, Outcome.FALLBACK)
        self.assertTrue(d.starts_session)
        self.assertEqual(d.calibration["ear_baseline"], PREVIOUS)
        self.assertTrue(d.calibration["fallback_used"])
        # 기준값 외에는 마지막 시도 값 그대로, 원본은 바뀌지 않음
        for key in ("valid_frames", "face_detected_ratio", "started_at", "ended_at"):
            self.assertEqual(d.calibration[key], c[key])
        self.assertFalse(c["fallback_used"])

    def test_no_face_falls_back(self):
        d = decide(make_calibration(T0, 0.0), MAX_ATTEMPTS, PREVIOUS)
        self.assertEqual(d.outcome, Outcome.FALLBACK)
        self.assertEqual(d.calibration["ear_baseline"], PREVIOUS)

    def test_second_failure_without_previous_refuses(self):
        c = make_calibration(T0, BAD_FACE_RATIO)
        for previous in (None, 0.0, -1.0):
            with self.subTest(previous=previous):
                d = decide(c, MAX_ATTEMPTS, previous)
                self.assertEqual(d.outcome, Outcome.REFUSED)
                self.assertIsNone(d.calibration)

    def test_zero_baseline_is_failure_even_with_full_ratio(self):
        # CV는 측정 원본만 준다: 검출 100%여도 EAR이 전부 0이면 기준값 0.0 → 실패로 봐야 함
        c = make_calibration(T0, 1.0, ear_baseline=0.0)
        self.assertFalse(is_successful(c))
        self.assertEqual(decide(c, 1, PREVIOUS).outcome, Outcome.RETRY)

        d = decide(c, MAX_ATTEMPTS, PREVIOUS)
        self.assertEqual(d.outcome, Outcome.FALLBACK)
        self.assertEqual(d.calibration["ear_baseline"], PREVIOUS)
        self.assertTrue(d.calibration["fallback_used"])

        self.assertEqual(decide(c, MAX_ATTEMPTS, None).outcome, Outcome.REFUSED)

    def test_negative_or_nan_baseline_is_failure(self):
        for baseline in (-0.1, float("nan")):
            with self.subTest(baseline=baseline):
                self.assertFalse(is_successful(make_calibration(T0, GOOD_FACE_RATIO, ear_baseline=baseline)))

    def test_attempt_must_start_at_one(self):
        with self.assertRaises(ValueError):
            decide(make_calibration(T0, GOOD_FACE_RATIO), 0, None)


if __name__ == "__main__":
    unittest.main()
