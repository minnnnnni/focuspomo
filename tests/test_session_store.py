"""storage/session_store.py 테스트 (개발계획서 3장, 2.0 폴백)."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from schemas import SCHEMA_VERSION, FocusLogFile, SessionResult, make_session_id
from storage.session_store import (
    append_session,
    check_session_id,
    delete_all_records,
    ensure_data_dir,
    focus_log_dir,
    focus_log_path,
    load_previous_baseline,
    save_focus_log,
    sessions_path,
)

SESSION_START = 1_790_753_400.0


def make_result(session_id="20261003-100000", **overrides) -> SessionResult:
    result: SessionResult = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "session_start": SESSION_START,
        "session_end": SESSION_START + 1620.0,
        "ear_baseline": 0.29,
        "pause_intervals": [{"start": SESSION_START + 600.0, "end": SESSION_START + 720.0}],
        "slot_scores": [{"slot": 0, "start": SESSION_START, "end": SESSION_START + 10.0,
                         "window_label": "work", "final_tag": "focus", "blink_count": 2, "score": 2}],
        "score_total": 2,
        "blink_bonus_total": 2,
        "drowsy_count": 0,
        "away_count": 0,
        "distraction_count": 0,
        "report_text": "집중을 잘 유지했어요",
        "llm_call_failed": False,
    }
    result.update(overrides)  # type: ignore[typeddict-item]
    return result


def make_focus_log(session_id="20261003-100000") -> FocusLogFile:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "session_start": SESSION_START,
        "calibration": {"ear_baseline": 0.29, "valid_frames": 75, "face_detected_ratio": 1.0,
                        "started_at": SESSION_START - 5.0, "ended_at": SESSION_START, "fallback_used": False},
        "pause_intervals": [{"start": SESSION_START + 600.0, "end": SESSION_START + 720.0}],
        "cv_seconds": [{"timestamp": SESSION_START, "face_present": True, "eyes_closed": False,
                        "blink_count": 1, "ear_mean": 0.3, "gaze_off_screen": None}],
        "merged_slots": [{"slot": 0, "timestamp": SESSION_START, "face_present": None, "drowsy": False,
                          "closed_run_max": 0, "blink_count": 0, "ear_mean": None, "gaze_off_screen": None,
                          "app_name": None, "domain": None}],
    }


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"  # 아직 없는 폴더

    def tearDown(self):
        self._tmp.cleanup()

    def read_lines(self) -> list[dict]:
        text = sessions_path(self.data_dir).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]


class SessionIdTest(unittest.TestCase):
    """1-19: session_id는 로컬 시각 YYYYMMDD-HHMMSS (콜론 없음)."""

    def test_make_session_id_local_time(self):
        start = time.mktime((2026, 10, 3, 9, 5, 7, 0, 0, -1))
        self.assertEqual(make_session_id(start), "20261003-090507")

    def test_make_session_id_is_valid_filename(self):
        sid = make_session_id(time.time())
        self.assertEqual(check_session_id(sid), sid)
        for bad in ':\\/*?"<>|':
            self.assertNotIn(bad, sid)

    def test_check_rejects_other_formats(self):
        for bad in ("2026-10-03 09:05:07", "20261003-0905", "../20261003-090507", "20261003-090507.json",
                    "", None, 20261003090507):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    check_session_id(bad)


class EnsureDataDirTest(TempDataDirTest):
    """1-16: 폴더가 없으면 만든다."""

    def test_creates_missing_dirs(self):
        ensure_data_dir(self.data_dir)
        self.assertTrue(focus_log_dir(self.data_dir).is_dir())

    def test_existing_dir_ok(self):
        ensure_data_dir(self.data_dir)
        (self.data_dir / "keep.txt").write_text("x", encoding="utf-8")
        ensure_data_dir(self.data_dir)
        self.assertTrue((self.data_dir / "keep.txt").exists())


class AppendSessionTest(TempDataDirTest):
    """1-17: sessions.jsonl에 SessionResult 한 줄씩 추가."""

    def test_creates_dir_and_writes_one_line(self):
        path = append_session(make_result(), self.data_dir)
        self.assertEqual(path, sessions_path(self.data_dir))
        self.assertEqual(self.read_lines(), [make_result()])

    def test_appends_in_order(self):
        append_session(make_result("20261003-100000"), self.data_dir)
        append_session(make_result("20261003-103000"), self.data_dir)
        self.assertEqual([r["session_id"] for r in self.read_lines()], ["20261003-100000", "20261003-103000"])

    def test_keeps_pause_intervals_and_version(self):
        append_session(make_result(), self.data_dir)
        record = self.read_lines()[0]
        self.assertEqual(record["schema_version"], SCHEMA_VERSION)
        self.assertEqual(record["pause_intervals"], make_result()["pause_intervals"])

    def test_korean_stored_as_utf8_text(self):
        append_session(make_result(), self.data_dir)
        raw = sessions_path(self.data_dir).read_bytes()
        self.assertIn("집중을 잘 유지했어요".encode("utf-8"), raw)
        self.assertNotIn(b"\r\n", raw)

    def test_readable_by_previous_baseline(self):
        append_session(make_result(ear_baseline=0.31), self.data_dir)
        self.assertEqual(load_previous_baseline(self.data_dir), 0.31)

    def test_after_broken_last_line_new_record_is_intact(self):
        ensure_data_dir(self.data_dir)
        sessions_path(self.data_dir).write_text('{"schema_version": 2, "ear_base', encoding="utf-8")
        append_session(make_result(ear_baseline=0.33), self.data_dir)
        lines = sessions_path(self.data_dir).read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[-1])["ear_baseline"], 0.33)
        self.assertEqual(load_previous_baseline(self.data_dir), 0.33)

    def test_rejects_nan_and_bad_id_without_writing(self):
        for result in (make_result(ear_baseline=float("nan")), make_result("2026-10-03 10:00:00")):
            with self.assertRaises(ValueError):
                append_session(result, self.data_dir)
        self.assertFalse(sessions_path(self.data_dir).exists())


class SaveFocusLogTest(TempDataDirTest):
    """1-18: focus_log/{session_id}.json."""

    def test_writes_file_named_by_session_id(self):
        path = save_focus_log(make_focus_log(), self.data_dir)
        self.assertEqual(path, focus_log_dir(self.data_dir) / "20261003-100000.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), make_focus_log())

    def test_none_values_kept(self):
        path = save_focus_log(make_focus_log(), self.data_dir)
        slot = json.loads(path.read_text(encoding="utf-8"))["merged_slots"][0]
        self.assertIn("face_present", slot)
        self.assertIsNone(slot["face_present"])

    def test_does_not_overwrite(self):
        save_focus_log(make_focus_log(), self.data_dir)
        other = make_focus_log()
        other["cv_seconds"] = []
        with self.assertRaises(FileExistsError):
            save_focus_log(other, self.data_dir)
        saved = json.loads(focus_log_path("20261003-100000", self.data_dir).read_text(encoding="utf-8"))
        self.assertEqual(len(saved["cv_seconds"]), 1)

    def test_bad_session_id_rejected(self):
        with self.assertRaises(ValueError):
            save_focus_log(make_focus_log("../evil"), self.data_dir)
        self.assertFalse((self.root / "evil.json").exists())

    def test_failed_write_leaves_no_file(self):
        with patch("storage.session_store.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                save_focus_log(make_focus_log(), self.data_dir)
        self.assertEqual(list(focus_log_dir(self.data_dir).iterdir()), [])


class DeleteAllRecordsTest(TempDataDirTest):
    """1-21: sessions.jsonl + focus_log/ 전체 삭제."""

    def test_deletes_both(self):
        append_session(make_result(), self.data_dir)
        save_focus_log(make_focus_log(), self.data_dir)
        delete_all_records(self.data_dir)
        self.assertFalse(sessions_path(self.data_dir).exists())
        self.assertFalse(focus_log_dir(self.data_dir).exists())
        self.assertIsNone(load_previous_baseline(self.data_dir))

    def test_nothing_to_delete_is_ok(self):
        delete_all_records(self.data_dir)
        delete_all_records(self.root)

    def test_keeps_other_files(self):
        append_session(make_result(), self.data_dir)
        other = self.data_dir / "settings.json"
        other.write_text("{}", encoding="utf-8")
        delete_all_records(self.data_dir)
        self.assertTrue(other.exists())

    def test_can_save_again_after_delete(self):
        save_focus_log(make_focus_log(), self.data_dir)
        delete_all_records(self.data_dir)
        save_focus_log(make_focus_log(), self.data_dir)
        self.assertTrue(focus_log_path("20261003-100000", self.data_dir).exists())


def session_line(ear_baseline, session_id="20261003-100000") -> str:
    # 폴백에 필요한 필드만 넣은 SessionResult 한 줄
    return json.dumps({"schema_version": SCHEMA_VERSION, "session_id": session_id, "ear_baseline": ear_baseline})


class LoadPreviousBaselineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, *lines: str) -> None:
        sessions_path(self.data_dir).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_no_data_dir(self):
        self.assertIsNone(load_previous_baseline(self.data_dir / "missing"))

    def test_no_sessions_file(self):
        self.assertIsNone(load_previous_baseline(self.data_dir))

    def test_empty_file(self):
        sessions_path(self.data_dir).write_text("", encoding="utf-8")
        self.assertIsNone(load_previous_baseline(self.data_dir))

    def test_returns_last_session(self):
        self.write(session_line(0.25, "20261003-100000"), session_line(0.31, "20261003-103000"))
        self.assertEqual(load_previous_baseline(self.data_dir), 0.31)

    def test_int_value_returned_as_float(self):
        self.write(session_line(1))
        result = load_previous_baseline(self.data_dir)
        self.assertIsInstance(result, float)
        self.assertEqual(result, 1.0)

    def test_skips_broken_last_line(self):
        # 마지막 줄을 쓰다가 앱이 꺼진 경우
        self.write(session_line(0.28), '{"schema_version": 2, "ear_base')
        self.assertEqual(load_previous_baseline(self.data_dir), 0.28)

    def test_skips_blank_lines(self):
        self.write(session_line(0.28), "", "   ")
        self.assertEqual(load_previous_baseline(self.data_dir), 0.28)

    def test_skips_invalid_values(self):
        for bad in (0.0, -0.2, None, "0.3", True, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                self.write(session_line(0.27), session_line(bad))
                self.assertEqual(load_previous_baseline(self.data_dir), 0.27)

    def test_skips_missing_key_and_non_object(self):
        self.write(session_line(0.27), json.dumps({"schema_version": SCHEMA_VERSION}), "[1, 2]")
        self.assertEqual(load_previous_baseline(self.data_dir), 0.27)

    def test_all_invalid(self):
        self.write(session_line(0.0), "not json")
        self.assertIsNone(load_previous_baseline(self.data_dir))

    def test_invalid_utf8_line_skipped(self):
        path = sessions_path(self.data_dir)
        path.write_bytes(session_line(0.26).encode("utf-8") + b"\n\xff\xfe broken\n")
        self.assertEqual(load_previous_baseline(self.data_dir), 0.26)

    def test_korean_report_text(self):
        line = json.dumps({"ear_baseline": 0.3, "report_text": "집중을 잘 유지했어요"}, ensure_ascii=False)
        self.write(line)
        self.assertEqual(load_previous_baseline(self.data_dir), 0.3)


if __name__ == "__main__":
    unittest.main()
