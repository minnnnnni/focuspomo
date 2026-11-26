"""고개 숙임 소급 판정 테스트 (CV-06, cv_module/head_down.py). 웹캠 없이 만든 1초 요약·프레임으로 확인한다.

    python -m unittest tests.test_head_down -v
"""
from __future__ import annotations

import queue
import threading
import time
import unittest

from config.aggregation import BOWED_EYES_PITCH, DROWSY_MIN_RUN_SEC, NOD_CLOSING_MAX_SEC, SAT_DOWN_PERSON_RATIO
from core.camera_link import BEGIN_SESSION, CALIBRATION_RAW, CAMERA_OPENED, END_SESSION, START_CALIBRATION, CameraLink
from core.slot_log import SlotLog
from cv_module.head_down import resolve_postures, usual_face_y
from cv_module.pipeline import CvPipeline
from cv_module.slot_compressor import build_slot
from cv_module.worker import CameraThread, FakeEarSource, FrameObservation
from schemas import SLOT_SECONDS, raw_tag

T0 = 1_780_000_000.0
BASE = 0.30
FPS = 15


def sec(t: int, face: bool = True, *, ear: float = BASE, pitch: float | None = 0.0, y: float | None = 0.7,
        person: float | None = None, pmax: float | None = None, closed: bool = False) -> dict:
    """1초 요약 하나. face=False면 EAR·각도·얼굴 높이는 없고 사람 영역만 있다."""
    return {
        "timestamp": T0 + t, "face_present": face, "eyes_closed": closed and face, "blink_count": 0,
        "ear_mean": ear if face else None, "gaze_off_screen": None,
        "pitch_rel": pitch if face else None, "face_y": y if face else None,
        "person_ratio": None if face else person, "person_max": None if face else (pmax if pmax is not None else person),
        "posture": None,
    }


def upright(n: int, start: int = 0, **kw) -> list[dict]:
    return [sec(start + k, **kw) for k in range(n)]


def gap(n: int, start: int, person: float | None, **kw) -> list[dict]:
    return [sec(start + k, False, person=person, **kw) for k in range(n)]


class ResolvePosturesTest(unittest.TestCase):
    def postures(self, seconds: list[dict]) -> list:
        return resolve_postures(seconds, BASE)

    def test_writing_with_head_visible_is_head_down(self):
        # 화면 보다가 숙여 쓰기: 각도가 커지다 얼굴을 놓침, 머리·어깨는 보임
        seconds = upright(10) + [sec(10, pitch=18), sec(11, pitch=24)] + gap(8, 12, 0.3) + upright(5, 20)
        p = self.postures(seconds)
        self.assertEqual(p[12:20], ["head_down"] * 8)
        self.assertTrue(all(x is None for x in p[:12] + p[20:]))

    def test_face_rising_out_of_frame_is_left(self):
        # 일어나면 얼굴이 위로 올라가며 사라진다 (측정: 0.80 → 0.25)
        seconds = upright(10, y=0.8) + [sec(10, y=0.5), sec(11, y=0.25)] + gap(10, 12, 0.3)
        self.assertEqual(self.postures(seconds)[12:], ["left"] * 10)

    def test_eyes_closing_then_head_drop_is_nod(self):
        # 고개 든 채 눈이 감기다(4초가 안 돼 졸음으로 안 잡힘) 고개를 떨굼 → 감긴 초까지 nod
        closing = [sec(10 + k, ear=0.07, closed=True) for k in range(3)]
        seconds = upright(10) + closing + gap(5, 13, 0.02) + upright(5, 18)
        p = self.postures(seconds)
        self.assertEqual(p[10:18], ["nod"] * 8)
        self.assertTrue(all(x is None for x in p[:10] + p[18:]))

    def test_nod_marks_at_most_max_closing_seconds(self):
        n = NOD_CLOSING_MAX_SEC + 3
        closing = [sec(5 + k, ear=0.07, closed=True) for k in range(n)]
        seconds = upright(5) + closing + gap(3, 5 + n, 0.02)
        p = self.postures(seconds)
        self.assertEqual(p.count("nod"), NOD_CLOSING_MAX_SEC + 3)
        self.assertIsNone(p[5 + n - NOD_CLOSING_MAX_SEC - 1])

    def test_low_eyes_while_bowing_is_not_nod(self):
        # 숙이는 직전 1초는 눈이 먼저 내려가도 졸음으로 보지 않는다
        seconds = upright(10) + [sec(10, ear=0.07, pitch=10)] + gap(6, 11, 0.3)
        self.assertEqual(self.postures(seconds)[11:], ["head_down"] * 6)

    def test_bowed_eyes_are_ignored_for_nod(self):
        # 이미 숙인 상태(각도 큼)에서 눈 비율이 낮은 건 각도 탓일 수 있어 보지 않는다
        bowed = [sec(10 + k, ear=0.07, pitch=25) for k in range(3)]
        seconds = upright(10) + bowed + gap(6, 13, 0.3)
        self.assertEqual(self.postures(seconds)[13:], ["head_down"] * 6)

    def test_sitting_back_down_makes_gap_left(self):
        # 숙인 채 일어나 나갔다가 돌아와 앉음: 다시 잡히기 직전 사람 영역이 크게 지나간다
        away = gap(10, 12, 0.07) + [sec(22, False, person=0.3, pmax=SAT_DOWN_PERSON_RATIO + 0.05)]
        seconds = upright(10) + [sec(10, pitch=20), sec(11, pitch=26)] + away + upright(5, 23)
        self.assertEqual(self.postures(seconds)[12:23], ["left"] * 11)

    def test_spike_without_face_returning_is_not_sat_down(self):
        # 세션이 얼굴 없이 끝나면 돌아와 앉은 것인지 알 수 없다
        seconds = upright(10) + gap(5, 10, 0.3, pmax=0.6)
        self.assertEqual(self.postures(seconds)[10:], ["head_down"] * 5)

    def test_nothing_visible_is_unseen(self):
        # 깊게 숙여 머리까지 안 보임 (빈자리와 구별 못함) → 판정불가
        seconds = upright(10) + [sec(10, pitch=26)] + gap(20, 11, 0.04) + upright(3, 31)
        self.assertEqual(self.postures(seconds)[11:31], ["unseen"] * 20)

    def test_without_person_data_keeps_old_judgment(self):
        # 사람 영역 모델이 없으면 지금처럼 얼굴 없음 (posture None)
        seconds = upright(10) + gap(5, 10, None) + upright(3, 15)
        self.assertEqual(self.postures(seconds), [None] * 18)

    def test_camera_hole_splits_gaps(self):
        # 사이에 카메라가 끊긴 초(요약 없음)가 있으면 따로 판정한다
        seconds = upright(5, y=0.8) + [sec(5, y=0.3)] + gap(3, 6, 0.3) + gap(3, 12, 0.3)
        p = self.postures(seconds)
        self.assertEqual(p[6:9], ["left"] * 3)
        self.assertEqual(p[9:12], ["head_down"] * 3)

    def test_usual_face_y_ignores_bowed_seconds(self):
        seconds = upright(5, y=0.7) + [sec(5 + k, pitch=25, y=0.95) for k in range(10)]
        self.assertAlmostEqual(usual_face_y(seconds), 0.7)


class BuildSlotTest(unittest.TestCase):
    def build(self, seconds: list[dict], runs: list[int] | None = None) -> dict:
        return build_slot(0, T0, seconds, runs or [0] * len(seconds))["slot"]

    def test_head_down_counts_as_present(self):
        seconds = upright(3) + [dict(s, posture="head_down") for s in gap(7, 3, 0.3)]
        self.assertIs(self.build(seconds)["face_present"], True)

    def test_nod_is_present_and_drowsy(self):
        seconds = upright(3) + [dict(s, posture="nod") for s in gap(7, 3, 0.02)]
        slot = self.build(seconds)
        self.assertIs(slot["face_present"], True)
        self.assertTrue(slot["drowsy"])

    def test_mostly_unseen_is_unknown(self):
        seconds = upright(4) + [dict(s, posture="unseen") for s in gap(6, 4, 0.02)]
        self.assertIsNone(self.build(seconds)["face_present"])

    def test_unseen_is_left_out_of_ratio(self):
        # 5초 판정불가 + 2초 있음 + 3초 자리비움 → 판정한 5초 중 2초만 있음 → 얼굴 없음
        seconds = (upright(2) + [dict(s, posture="unseen") for s in gap(5, 2, 0.02)]
                   + [dict(s, posture="left") for s in gap(3, 7, 0.07)])
        self.assertIs(self.build(seconds)["face_present"], False)

    def test_without_posture_same_as_before(self):
        self.assertIs(self.build(upright(4) + gap(6, 4, None))["face_present"], False)


class PipelineResendTest(unittest.TestCase):
    """프레임 → CvPipeline → finish()가 바뀐 슬롯을 다시 보내고, SlotLog에서 나중 메시지가 남는다."""

    @staticmethod
    def feed(frame_fn, seconds: int, **kw) -> tuple[list[dict], list[dict], SlotLog]:
        """frame_fn(t) → (ear, 추가 값). (세션 중 메시지, finish 메시지, 둘 다 넣은 SlotLog)"""
        p = CvPipeline(T0, BASE, **kw)
        during = []
        for i in range(seconds * FPS):
            ear, extra = frame_fn(i / FPS)
            during += p.add_frame(T0 + i / FPS, ear, **extra)
        last = p.finish()
        log = SlotLog(T0)
        for msg in during + last:
            log.add(msg)
        return during, last, log

    @staticmethod
    def writing(t: float):
        # 0~12초 화면, 12~28초 숙여 필기 (얼굴 놓침, 어깨 보임), 28~30초 화면
        if 12 <= t < 28:
            return None, {"person": 0.3}
        return BASE, {"pitch": -5.0, "face_y": 0.7}

    def test_writing_is_present_after_session_end(self):
        during, _, log = self.feed(self.writing, 30, pitch_baseline=-5.0)
        live = {m["slot"]["slot"]: m["slot"]["face_present"] for m in during}
        self.assertIs(live[1], False)  # 세션 중에는 지금처럼 얼굴 없음
        merged = log.finalize(T0 + 30)
        self.assertEqual([s["face_present"] for s in merged], [True, True, True])
        self.assertEqual({raw_tag(s, "work", "full") for s in merged}, {"focus"})
        postures = [s["posture"] for s in log.cv_seconds()]
        self.assertEqual(postures.count("head_down"), 16)
        self.assertEqual(len(log.cv_seconds()), 30)

    def test_nod_becomes_drowsy(self):
        def nod(t: float):
            if 10 <= t < 13:
                return 0.05, {"pitch": -5.0, "face_y": 0.7}   # 고개 든 채 눈 감김 3초
            if 13 <= t < 20:
                return None, {"person": 0.02}                 # 고개 떨굼
            return BASE, {"pitch": -5.0, "face_y": 0.7}
        _, _, log = self.feed(nod, 30, pitch_baseline=-5.0)
        merged = log.finalize(T0 + 30)
        self.assertTrue(merged[1]["drowsy"])
        self.assertIs(merged[1]["face_present"], True)
        self.assertEqual(raw_tag(merged[1], "work", "full"), "drowsy")

    def test_no_resend_when_face_never_lost(self):
        _, last, _ = self.feed(lambda t: (BASE, {}), 25)
        self.assertEqual([m["slot"]["slot"] for m in last], [2])

    def test_head_down_off_keeps_old_messages(self):
        _, last, _ = self.feed(lambda t: (None, {"person": 0.3}), SLOT_SECONDS, head_down=False)
        self.assertEqual(len(last), 1)
        self.assertIs(last[0]["slot"]["face_present"], False)


class PersonSegmenterTest(unittest.TestCase):
    def test_missing_model_gives_none(self):
        from cv_module.person_segmenter import PersonSegmenter

        seg = PersonSegmenter("models/__missing__.tflite")
        with self.assertLogs("cv_module.person_segmenter", "WARNING"):
            self.assertIsNone(seg.ratio(None))
        self.assertIsNone(seg.ratio(None))  # 경고는 한 번만

    def test_error_turns_model_off(self):
        from cv_module.person_segmenter import PersonSegmenter

        class Broken:
            def segment(self, image):
                raise RuntimeError("boom")

            def close(self):
                pass

        seg = PersonSegmenter("models/__missing__.tflite")
        seg._segmenter = Broken()
        import numpy as np

        with self.assertLogs("cv_module.person_segmenter", "ERROR"):
            self.assertIsNone(seg.ratio(np.zeros((8, 8, 3), dtype=np.uint8)))
        self.assertIsNone(seg.ratio(np.zeros((8, 8, 3), dtype=np.uint8)))


class BowedEyesTest(unittest.TestCase):
    """노트를 내려다보면 얼굴은 잡혀도 눈이 감긴 것처럼 작아진다. 숙인 프레임은 감김으로 세지 않는다."""

    def run_slot(self, pitch_rel: float) -> dict:
        p = CvPipeline(T0, BASE, pitch_baseline=-5.0)
        msgs = []
        for i in range(SLOT_SECONDS * FPS):
            msgs += p.add_frame(T0 + i / FPS, 0.05, pitch=-5.0 + pitch_rel, face_y=0.75)  # 10초 내내 눈이 작음
        msgs += p.finish()
        return msgs[-1]

    def test_low_eyes_while_bowed_is_not_drowsy(self):
        msg = self.run_slot(BOWED_EYES_PITCH + 5)
        self.assertFalse(msg["slot"]["drowsy"])
        self.assertEqual(msg["slot"]["closed_run_max"], 0)
        self.assertIs(msg["slot"]["face_present"], True)

    def test_closed_eyes_while_upright_is_still_drowsy(self):
        msg = self.run_slot(BOWED_EYES_PITCH - 5)
        self.assertTrue(msg["slot"]["drowsy"])
        self.assertGreaterEqual(msg["slot"]["closed_run_max"], DROWSY_MIN_RUN_SEC)

    def test_without_pitch_baseline_same_as_before(self):
        p = CvPipeline(T0, BASE)  # 캘리브레이션 각도가 없으면 각도를 볼 수 없어 예전처럼 판정
        msgs = []
        for i in range(SLOT_SECONDS * FPS):
            msgs += p.add_frame(T0 + i / FPS, 0.05, pitch=30.0)
        msgs += p.finish()
        self.assertTrue(msgs[-1]["slot"]["drowsy"])


class ObserveSource(FakeEarSource):
    """observe()로 각도·얼굴 높이·사람 영역까지 주는 가짜 웹캠."""

    def __init__(self) -> None:
        super().__init__(lambda t: BASE)
        self.with_person: list[bool] = []

    def observe(self, frame, *, with_person: bool = False) -> FrameObservation:
        self.with_person.append(with_person)
        return FrameObservation(BASE, pitch=-4.0, face_y=0.7)


class QuickCalibrator:
    def __init__(self, started_at: float) -> None:
        self.started_at, self.ended_at, self.pitches = started_at, None, []

    @property
    def done(self) -> bool:
        return self.ended_at is not None

    @property
    def pitch_baseline(self) -> float | None:
        return sum(self.pitches) / len(self.pitches) if self.pitches else None

    def add_frame(self, ts: float, ear: float | None, pitch: float | None = None) -> None:
        if ts - self.started_at >= 0.3:
            self.ended_at = ts
        elif pitch is not None:
            self.pitches.append(pitch)

    def finish(self) -> dict:
        return {"ear_baseline": BASE, "valid_frames": 1, "face_detected_ratio": 1.0,
                "started_at": self.started_at, "ended_at": self.ended_at, "fallback_used": False}


class WorkerObserveTest(unittest.TestCase):
    def test_pitch_baseline_and_extras_reach_pipeline(self):
        link, slot_q = CameraLink(), queue.Queue()
        made: list[dict] = []

        class RecordingPipeline:
            def __init__(self, start: float, base: float, **kw) -> None:
                made.append(kw)
                self.extras: list[dict] = []

            def add_frame(self, ts: float, ear: float | None, **kw) -> list:
                self.extras.append(kw)
                return []

            def finish(self) -> list:
                return [{"source": "cv", "extras": self.extras}]

        source = ObserveSource()
        cam = CameraThread(link, source=source, lost_sec=0.3, reopen_sec=0.2,
                           calibrator_factory=QuickCalibrator, pipeline_factory=RecordingPipeline)
        cam.start()
        self.addCleanup(lambda: (link.stop(), cam.join(2)))

        def wait_event(kind: str) -> dict:
            end = time.time() + 2
            while time.time() < end:
                try:
                    event = link.events.get(timeout=0.05)
                except queue.Empty:
                    continue
                if event["type"] == kind:
                    return event
            self.fail(f"{kind} 이벤트가 오지 않음")

        wait_event(CAMERA_OPENED)
        link.commands.put({"type": START_CALIBRATION, "calib_id": "c1", "attempt": 1})
        wait_event(CALIBRATION_RAW)
        done = threading.Event()
        link.commands.put({"type": BEGIN_SESSION, "session_start": time.time(), "ear_baseline": BASE,
                           "slot_queue": slot_q, "done": done})
        time.sleep(0.4)
        link.commands.put({"type": END_SESSION, "done": done})
        self.assertTrue(done.wait(2))
        self.assertEqual(made, [{"pitch_baseline": -4.0}])
        extras = slot_q.get_nowait()["extras"]
        self.assertTrue(extras)
        self.assertEqual(extras[0], {"pitch": -4.0, "face_y": 0.7})
        self.assertIn(True, source.with_person)   # 세션 중에는 사람 영역도 재도록 요청


if __name__ == "__main__":
    unittest.main()
