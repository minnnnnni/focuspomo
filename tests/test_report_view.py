"""gui/report_view.py 테스트 (화면 없이 offscreen으로 실행, 1-14)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QAbstractItemView, QApplication  # noqa: E402

from core.slot_log import SlotLog  # noqa: E402
from gui.report_view import (  # noqa: E402
    LLM_FAILED_NOTICE,
    TABLE_HEADERS,
    ReportView,
    format_duration,
    format_span,
)
from mocks.mock_calibration import make_calibration  # noqa: E402
from mocks.mock_scoring import MockScorer  # noqa: E402
from mocks.mock_session import build_scenario, messages_only  # noqa: E402

app = QApplication.instance() or QApplication([])

START = 1_790_753_400.0


def normal_result(llm_fail=False, pauses=None, session_end=START + 60):
    log = SlotLog(START)
    for msg in messages_only(build_scenario("normal", START)):
        log.add(msg)
    return MockScorer(llm_fail)(log.finalize(session_end), make_calibration(START - 5, face_detected_ratio=1.0),
                                pauses or [], session_id="20261003-100000", session_start=START,
                                session_end=session_end)


class FormatTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(format_duration(0), "0초")
        self.assertEqual(format_duration(45.2), "45초")
        self.assertEqual(format_duration(65), "1분 05초")
        self.assertEqual(format_duration(59.6), "1분 00초")
        self.assertEqual(format_duration(-1), "0초")

    def test_span(self):
        self.assertEqual(format_span(START + 70, START + 80, START), "01:10–01:20")
        self.assertEqual(format_span(START + 1490, START + 1493.4, START), "24:50–24:53")


class ReportViewTest(unittest.TestCase):
    def setUp(self):
        self.view = ReportView()
        self.addCleanup(self.view.deleteLater)

    def cell(self, row, col):
        return self.view.table.item(row, col).text()

    def test_summary(self):
        self.view.show_result(normal_result())
        self.assertEqual(self.view.score_label.text(), "-3점")
        self.assertEqual(self.view.blink_bonus_label.text(), "+3")
        self.assertEqual(self.view.drowsy_label.text(), "1구간")
        self.assertEqual(self.view.away_label.text(), "1구간")
        self.assertEqual(self.view.distraction_label.text(), "2구간")
        self.assertTrue(self.view.report_text_label.text().startswith("(mock)"))
        self.assertTrue(self.view.llm_notice_label.isHidden())
        self.assertTrue(self.view.save_notice_label.isHidden())
        self.assertTrue(self.view.pause_label.isHidden())

    def test_table_rows_in_korean(self):
        self.view.show_result(normal_result())
        table = self.view.table
        self.assertEqual(table.rowCount(), 6)
        self.assertEqual([table.horizontalHeaderItem(i).text() for i in range(table.columnCount())],
                         list(TABLE_HEADERS))
        self.assertEqual([self.cell(2, c) for c in range(5)], ["00:20–00:30", "딴짓", "이탈(자리비움)", "0", "-3"])
        self.assertEqual([self.cell(1, c) for c in range(5)], ["00:10–00:20", "작업", "집중", "2", "+2"])
        self.assertEqual(self.cell(3, 2), "이탈(졸음)")
        self.assertFalse(self.view.table.isHidden())
        self.assertTrue(self.view.empty_table_label.isHidden())

    def test_llm_failed_notice(self):
        self.view.show_result(normal_result(llm_fail=True))
        self.assertFalse(self.view.llm_notice_label.isHidden())
        self.assertEqual(self.view.llm_notice_label.text(), LLM_FAILED_NOTICE)
        self.assertEqual({self.cell(r, 1) for r in range(6)}, {"미분류"})

    def test_pause_summary(self):
        pauses = [{"start": START + 12, "end": START + 20}, {"start": START + 31, "end": START + 88}]
        self.view.show_result(normal_result(pauses=pauses), excluded_slots=3)
        self.assertFalse(self.view.pause_label.isHidden())
        text = self.view.pause_label.text()
        self.assertIn("일시정지 2회", text)
        self.assertIn("1분 05초", text)
        self.assertIn("3구간", text)

    def test_save_failed_notice(self):
        self.view.show_result(normal_result(), save_failed=True)
        self.assertFalse(self.view.save_notice_label.isHidden())

    def test_empty_session(self):
        result = MockScorer()([], make_calibration(START - 5, face_detected_ratio=1.0), [],
                              session_id="20261003-100000", session_start=START, session_end=START)
        self.view.show_result(result)
        self.assertEqual(self.view.score_label.text(), "0점")
        self.assertEqual(self.view.table.rowCount(), 0)
        self.assertTrue(self.view.table.isHidden())
        self.assertFalse(self.view.empty_table_label.isHidden())

    def test_new_result_replaces_old(self):
        self.view.show_result(normal_result(llm_fail=True), save_failed=True,
                              excluded_slots=1)
        self.view.show_result(normal_result())
        self.assertTrue(self.view.llm_notice_label.isHidden())
        self.assertTrue(self.view.save_notice_label.isHidden())
        self.assertTrue(self.view.pause_label.isHidden())
        self.assertEqual(self.view.table.rowCount(), 6)

    def test_clear(self):
        self.view.show_result(normal_result())
        self.view.clear()
        self.assertIsNone(self.view.result)
        self.assertEqual(self.view.score_label.text(), "")
        self.assertEqual(self.view.table.rowCount(), 0)

    def test_table_read_only(self):
        self.view.show_result(normal_result())
        self.assertEqual(self.view.table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)

    def test_home_button(self):
        seen = []
        self.view.home_requested.connect(lambda: seen.append(True))
        self.view.home_button.click()
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
