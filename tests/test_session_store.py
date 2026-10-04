"""storage/session_store.py 테스트 (개발계획서 3장, 2.0 폴백)."""
import json
import tempfile
import unittest
from pathlib import Path

from storage.session_store import load_previous_baseline, sessions_path


def session_line(ear_baseline, session_id="20261003-100000") -> str:
    # 폴백에 필요한 필드만 넣은 SessionResult 한 줄
    return json.dumps({"schema_version": 1, "session_id": session_id, "ear_baseline": ear_baseline})


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
        self.write(session_line(0.28), '{"schema_version": 1, "ear_base')
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
        self.write(session_line(0.27), json.dumps({"schema_version": 1}), "[1, 2]")
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
