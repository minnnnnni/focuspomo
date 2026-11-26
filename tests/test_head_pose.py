"""고개 각도 계산·측정 기록 분석 테스트 (cv_module/head_pose.py, analyze_head_pose.py). 웹캠 없이 합성 기록으로."""
import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from cv_module.analyze_head_pose import (
    FLOAT_FIELDS,
    analyze,
    face_loss_events,
    label_stats,
    onset_events,
    parse_row,
    pitch_bins,
    simulate_current_rule,
)
from cv_module.head_pose import head_angles


def rx(deg):  # x축 회전 — 얼굴 앞쪽 (0,0,1)이 (0,-sin,cos)로, 즉 아래로
    a = math.radians(deg)
    return [[1, 0, 0, 0], [0, math.cos(a), -math.sin(a), 0], [0, math.sin(a), math.cos(a), 0], [0, 0, 0, 1]]


def ry(deg):
    a = math.radians(deg)
    return [[math.cos(a), 0, math.sin(a), 0], [0, 1, 0, 0], [-math.sin(a), 0, math.cos(a), 0], [0, 0, 0, 1]]


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


class HeadAnglesTest(unittest.TestCase):
    def test_frontal(self):
        self.assertEqual(tuple(round(v, 6) for v in head_angles(rx(0))), (0, 0, 0))

    def test_pitch_down_is_positive(self):
        self.assertAlmostEqual(head_angles(rx(30))[0], 30, places=6)
        self.assertAlmostEqual(head_angles(rx(-15))[0], -15, places=6)

    def test_yaw_does_not_change_pitch(self):
        pitch, yaw, _ = head_angles(matmul(ry(40), rx(25)))
        self.assertAlmostEqual(pitch, 25, places=6)
        self.assertAlmostEqual(yaw, 40, places=6)


FPS = 10


def frames(label, seconds, start, *, face=True, pitch=0.0, ear_ratio=1.0, person=0.3, cx=0.5, cy=0.5,
           ramp=None):
    """합성 프레임. ramp(i) -> (pitch, ear_ratio)를 주면 프레임마다 바뀐다."""
    out = []
    for i in range(int(seconds * FPS)):
        p, e = ramp(i / FPS) if ramp else (pitch, ear_ratio)
        out.append({
            "t": start + i / FPS, "label": label, "face": face,
            "ear": 0.3 * e if face else None, "ear_base": 0.3, "ear_ratio": e if face else None,
            "closed": face and e < 0.6, "pitch": p + 5 if face else None, "pitch_base": 5.0,
            "pitch_rel": p if face else None, "yaw": 0.0 if face else None, "roll": 0.0 if face else None,
            "blink": (1 - e) if face else None, "look_down": min(p / 40, 1) if face else None, "look_up": 0.0,
            "face_cx": cx if face else None, "face_cy": cy if face else None, "face_w": 0.3 if face else None,
            "person": person, "person_center": person * 2,
        })
    return out


def session():
    """화면 10초 → 필기 20초(숙이면 눈 비율 0.5 → 지금 규칙상 감김) → 화면 5초 → 졸다 고개 떨굼 10초 → 나감 10초."""
    rows = frames("screen", 10, 0)
    rows += frames("write", 20, 10, ramp=lambda t: (0.0, 1.0) if t < 3 else (30.0, 0.5))
    rows += frames("screen", 5, 30)
    # 눈이 먼저 감기고(3초부터 0.3) 6초에 고개가 떨어짐
    rows += frames("nod", 10, 35, ramp=lambda t: (0.0, 1.0) if t < 3 else (0.0, 0.3) if t < 6 else (35.0, 0.2))
    rows += frames("leave", 3, 45, pitch=0.0)
    rows += frames("leave", 7, 48, face=False, person=0.02)
    return rows


class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        self.rows = session()
        self.seconds = simulate_current_rule(self.rows)
        self.stats = label_stats(self.rows, self.seconds)

    def test_current_rule_flags_writing_as_drowsy(self):
        """지금 규칙이면 필기 17초(숙인 동안) 중 4초째부터 졸음 → 14초."""
        self.assertEqual(self.stats["write"]["drowsy_sec"], 14)
        self.assertEqual(self.stats["screen"]["drowsy_sec"], 0)
        self.assertEqual(self.stats["leave"]["away_sec"], 7)

    def test_label_stats(self):
        write = self.stats["write"]
        self.assertEqual(write["seconds"], 20)
        self.assertAlmostEqual(write["pitch_p50"], 30)
        self.assertAlmostEqual(write["face_rate"], 1.0)
        self.assertAlmostEqual(self.stats["leave"]["person_missing"], 0.02)

    def test_pitch_bins(self):
        bins = {b["lo"]: b for b in pitch_bins(self.rows)}
        self.assertAlmostEqual(bins[0]["ear_ratio"], 1.0)
        self.assertAlmostEqual(bins[30]["ear_ratio"], 0.5)
        self.assertEqual(bins[30]["closed_rate"], 1.0)

    def test_onsets_show_eyes_before_head(self):
        """필기는 숙이기 전 눈 비율이 정상, 졸다 고개 떨굼은 숙이기 전에 이미 작다."""
        events = {e["label"]: e for e in onset_events(self.rows)}
        self.assertAlmostEqual(events["write"]["ear_before"], 1.0)
        self.assertLess(events["nod"]["ear_before"], 0.6)
        self.assertAlmostEqual(events["write"]["ear_after"], 0.5)

    def test_face_loss(self):
        [loss] = face_loss_events(self.rows)
        self.assertEqual(loss["label"], "leave")
        self.assertAlmostEqual(loss["last_pitch"], 0.0)
        self.assertAlmostEqual(loss["person"], 0.02)

    def test_analyze_writes_summary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "head_pose_x.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["label", "face", "closed", *FLOAT_FIELDS])
                writer.writeheader()
                for r in self.rows:
                    writer.writerow({**{k: "" if r[k] is None else r[k] for k in FLOAT_FIELDS},
                                     "label": r["label"], "face": int(r["face"]), "closed": int(r["closed"])})
            path.with_suffix(".json").write_text(json.dumps({"calibrations": [{"ear_baseline": 0.3,
                                                                               "pitch_baseline": 5.0}]}))
            with self.assertNoLogs(level="WARNING"):
                out = analyze(path)
            text = out.read_text(encoding="utf-8")
        self.assertIn("| 필기 | 20 | 100% | +0 / +30 / +30 |", text)
        self.assertIn("14초 / 0초", text)
        self.assertNotIn("부호가 반대", text)

    def test_parse_row(self):
        row = parse_row({"t": "1.5", "label": "", "face": "0", "closed": "0", "ear": ""})
        self.assertEqual((row["t"], row["face"], row["ear"], row["label"]), (1.5, False, None, ""))


if __name__ == "__main__":
    unittest.main()
