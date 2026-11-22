"""gui/history_view.py 테스트 (offscreen): 목록·요약·그래프 데이터·삭제 확인 (GUI-08)."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.history import summarize  # noqa: E402
from gui.history_view import (  # noqa: E402
    CHART_SESSIONS,
    LEGACY_SCORE_TIP,
    NO_SCORE_TEXT,
    NO_SCORE_TIP,
    HistoryView,
    TrendChart,
    format_date,
    format_minutes,
)
from tests.test_history import START, v3_record, v4_record  # noqa: E402

app = QApplication.instance() or QApplication([])


def summaries(n: int, **overrides):
    return [summarize(v4_record(f"20261003-1{i:05d}", start=START + i * 3600, **overrides)) for i in range(n)]


class FormatTest(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(format_minutes(45), "45초")
        self.assertEqual(format_minutes(1500), "25분")
        self.assertEqual(format_minutes(3900), "1시간 05분")
        self.assertEqual(format_minutes(-3), "0초")

    def test_date_local(self):
        t = time.mktime((2026, 11, 22, 19, 53, 0, 0, 0, -1))
        self.assertEqual(format_date(t), "11.22 (일) 19:53")


class HistoryViewTest(unittest.TestCase):
    def setUp(self):
        self.view = HistoryView()
        self.view.resize(580, 640)

    def tearDown(self):
        self.view.deleteLater()

    def test_empty_state(self):
        self.view.set_sessions([])
        self.assertFalse(self.view.empty_label.isHidden())
        self.assertTrue(self.view.table_card.isHidden())
        self.assertTrue(self.view.chart_card.isHidden())
        self.assertFalse(self.view.delete_button.isEnabled())
        self.assertEqual(self.view.count_label.text(), "0개")
        self.assertEqual(self.view.average_label.text(), NO_SCORE_TEXT)

    def test_table_newest_first(self):
        items = summaries(3)
        self.view.set_sessions(items)
        self.assertEqual(self.view.table.rowCount(), 3)
        self.assertEqual(self.view.table.item(0, 0).text(), format_date(items[-1].session_start))
        self.assertEqual(self.view.table.item(0, 1).text(), "25분")  # 27분 - 정지 2분
        self.assertEqual(self.view.table.item(0, 3).text(), "78")
        self.assertEqual(self.view.table.item(0, 4).text(), "2")  # 졸음 묶음
        self.assertEqual(self.view.table.item(0, 6).text(), "1")  # 정지
        self.assertTrue(self.view.empty_label.isHidden())
        self.assertTrue(self.view.delete_button.isEnabled())

    def test_summary(self):
        items = [summarize(v4_record(focus_score=80)), summarize(v4_record("20261003-110000", focus_score=61))]
        self.view.set_sessions(items)
        self.assertEqual(self.view.count_label.text(), "2개")
        self.assertEqual(self.view.average_label.text(), "70")  # 70.5 → 표시 반올림
        self.assertEqual(self.view.focus_total_label.text(), "35분")

    def test_legacy_and_missing_scores(self):
        items = [summarize(v3_record()), summarize(v4_record(focus_score=None))]
        self.view.set_sessions(items)
        self.assertFalse(self.view.legacy_label.isHidden())
        # 최신순: v4(점수 없음) → v3
        self.assertEqual(self.view.table.item(0, 3).text(), NO_SCORE_TEXT)
        self.assertEqual(self.view.table.item(0, 3).toolTip(), NO_SCORE_TIP)
        self.assertEqual(self.view.table.item(1, 3).toolTip(), LEGACY_SCORE_TIP)

    def test_no_legacy_notice_for_v4_only(self):
        self.view.set_sessions(summaries(2))
        self.assertTrue(self.view.legacy_label.isHidden())

    def test_chart_keeps_recent_sessions(self):
        items = summaries(CHART_SESSIONS + 5)
        self.view.set_sessions(items)
        self.assertEqual(self.view.chart.sessions, items[-CHART_SESSIONS:])
        self.assertEqual(self.view.table.rowCount(), CHART_SESSIONS + 5)  # 목록은 전부

    def test_delete_asks_and_emits_only_on_yes(self):
        self.view.set_sessions(summaries(1))
        emitted = []
        self.view.delete_requested.connect(lambda: emitted.append(True))
        self.view.confirm_delete = lambda: False
        self.view.delete_button.click()
        self.assertEqual(emitted, [])
        self.view.confirm_delete = lambda: True
        self.view.delete_button.click()
        self.assertEqual(emitted, [True])

    def test_load_failed_notice(self):
        self.view.set_sessions(summaries(2))
        self.view.show_load_failed()
        self.assertFalse(self.view.notice_label.isHidden())
        self.assertEqual(self.view.sessions, [])
        self.assertTrue(self.view.delete_button.isEnabled())
        self.view.set_sessions([])  # 다시 읽으면 안내는 지운다
        self.assertTrue(self.view.notice_label.isHidden())

    def test_paints_without_error(self):
        self.view.set_sessions([summarize(v3_record())] + summaries(3))
        self.view.show()
        self.view.grab()
        self.view.hide()


class TrendChartTest(unittest.TestCase):
    def test_session_at_columns(self):
        chart = TrendChart()
        chart.resize(428, 190)  # 그림 영역 = 428 - 28 - 4 = 396 → 칸 4개면 99px
        items = summaries(4)
        chart.set_sessions(items)
        self.assertEqual(chart.session_at(QPoint(30, 50)), 0)
        self.assertEqual(chart.session_at(QPoint(420, 50)), 3)
        self.assertIsNone(chart.session_at(QPoint(5, 50)))  # 눈금 자리
        chart.set_sessions([])
        self.assertIsNone(chart.session_at(QPoint(100, 50)))
        chart.deleteLater()


if __name__ == "__main__":
    unittest.main()
