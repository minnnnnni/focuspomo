"""gui/report_view.py 테스트 (화면 없이 offscreen으로 실행, 1-14)."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication  # noqa: E402

from core.slot_log import SlotLog  # noqa: E402
from gui.report_view import (  # noqa: E402
    LLM_FAILED_NOTICE,
    TABLE_HEADER_HEIGHT,
    TABLE_HEADERS,
    TABLE_ROW_HEIGHT,
    TIMELINE_ROW_GAP,
    ReportView,
    SlotTimeline,
    format_duration,
    format_span,
    legend_html,
    timeline_slots,
)
from gui.style import TIMELINE_SLOT_GAP, TIMELINE_SLOT_HEIGHT, TIMELINE_SLOT_WIDTH  # noqa: E402
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

    def test_design_props(self):
        self.view.show_result(normal_result())
        self.assertEqual(self.view.next_button.property("variant"), "primary")
        self.assertEqual(self.view.home_button.property("variant"), "secondary")
        self.assertEqual(self.view.drowsy_label.property("tone"), "warning")
        self.assertEqual(self.view.away_label.property("tone"), "alert")
        self.assertEqual(self.view.blink_bonus_label.property("tone"), "focus")

    def test_zero_counts_are_muted(self):
        result = normal_result()
        result["drowsy_count"] = 0
        self.view.show_result(result)
        self.assertEqual(self.view.drowsy_label.property("tone"), "muted")

    def test_table_does_not_scroll_itself(self):
        self.view.show_result(normal_result())
        self.assertEqual(self.view.table.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(self.view.table.height(), TABLE_HEADER_HEIGHT + 6 * TABLE_ROW_HEIGHT + 2)

    def test_meta_excludes_pause_time(self):
        pauses = [{"start": START + 10, "end": START + 20}]
        self.view.show_result(normal_result(pauses=pauses))
        started = time.localtime(START)
        self.assertEqual(self.view.meta_label.text(),
                         f"{started.tm_mon}월 {started.tm_mday}일 {started.tm_hour:02d}:{started.tm_min:02d} · 50초")

    def test_empty_report_text_hides_card(self):
        result = normal_result()
        result["report_text"] = ""
        self.view.show_result(result)
        self.assertTrue(self.view.report_card.isHidden())

    def test_empty_session_hides_timeline(self):
        result = MockScorer()([], make_calibration(START - 5, face_detected_ratio=1.0), [],
                              session_id="20261003-100000", session_start=START, session_end=START)
        self.view.show_result(result)
        self.assertTrue(self.view.timeline_card.isHidden())

    def test_timeline_filled_and_cleared(self):
        self.view.show_result(normal_result())
        self.assertFalse(self.view.timeline_card.isHidden())
        self.assertIn("집중", self.view.legend_label.text())
        self.view.clear()
        self.assertTrue(self.view.timeline_card.isHidden())
        self.assertEqual(self.view.legend_label.text(), "")


def score_row(slot, tag="focus"):
    return {"slot": slot, "start": START + slot * 10, "end": START + (slot + 1) * 10,
            "window_label": "work", "final_tag": tag, "blink_count": 0, "score": 0}


class TimelineTest(unittest.TestCase):
    def test_paused_slots_are_none(self):
        result = normal_result()
        result["slot_scores"] = [score_row(0), score_row(2)]
        self.assertEqual([s and s["slot"] for s in timeline_slots(result)], [0, None, 2, None, None, None])

    def test_short_last_slot_is_counted(self):
        result = normal_result(session_end=START + 41)
        result["slot_scores"] = []
        self.assertEqual(len(timeline_slots(result)), 5)

    def test_legend_counts_only_present_tags(self):
        text = legend_html([score_row(0), score_row(1), score_row(2, "away"), None])
        self.assertIn("집중&nbsp;2", text)
        self.assertIn("이탈(자리비움)&nbsp;1", text)
        self.assertIn("일시정지&nbsp;1", text)
        self.assertNotIn("이탈(졸음)", text)

    def test_wraps_to_fit_width(self):
        timeline = SlotTimeline()
        self.addCleanup(timeline.deleteLater)
        timeline.set_slots([score_row(i) for i in range(150)], START)
        step = TIMELINE_SLOT_WIDTH + TIMELINE_SLOT_GAP
        one_row = 150 * step
        self.assertEqual(timeline.heightForWidth(one_row), TIMELINE_SLOT_HEIGHT)
        self.assertEqual(timeline.heightForWidth(75 * step), 2 * TIMELINE_SLOT_HEIGHT + TIMELINE_ROW_GAP)

    def test_slot_at_and_tooltip(self):
        timeline = SlotTimeline()
        self.addCleanup(timeline.deleteLater)
        step = TIMELINE_SLOT_WIDTH + TIMELINE_SLOT_GAP
        timeline.resize(10 * step, 100)
        timeline.set_slots([score_row(i) for i in range(11)] + [None], START)
        self.assertEqual(timeline.slot_at(QPoint(0, 0)), 0)
        self.assertEqual(timeline.slot_at(QPoint(3 * step, 0)), 3)
        self.assertEqual(timeline.slot_at(QPoint(step, TIMELINE_SLOT_HEIGHT + TIMELINE_ROW_GAP)), 11)
        self.assertIsNone(timeline.slot_at(QPoint(5 * step, TIMELINE_SLOT_HEIGHT + TIMELINE_ROW_GAP)))
        self.assertEqual(timeline.tooltip_text(3), "00:30–00:40 · 집중")
        self.assertEqual(timeline.tooltip_text(11), "01:50–02:00 · 일시정지 (점수 제외)")


if __name__ == "__main__":
    unittest.main()
