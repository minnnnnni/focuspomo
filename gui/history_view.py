"""통계/기록 화면 (GUI-08: 3-01 세션 목록, 3-02 추이 그래프, 3-04 기록 삭제).

내비게이션 바의 "기록"으로 온다. MainWindow가 storage.session_store.load_sessions()로 읽고
core.history.summarize_all()로 줄인 SessionSummary 목록을 set_sessions()로 넘긴다 (화면은 파일을 읽지 않는다).

- 요약: 세션 수, 평균 집중도(점수가 있는 세션만), 총 집중 시간
- 추이 그래프: 최근 CHART_SESSIONS개 세션. 위는 집중도 점수(0~100) 선, 아래는 세션별 졸음·딴짓 횟수 막대.
  점수가 없는 세션(v3 이하 기록·판정 부족)은 점을 찍지 않고 선을 끊는다. 마우스를 올리면 그 세션 값
- 세션 목록: 최신순. 날짜, 실제 시간(정지 제외), 집중 시간, 점수, 졸음·딴짓 횟수, 정지 횟수.
  표 자체는 스크롤하지 않고 바깥 스크롤 영역에 맡긴다 (리포트 화면과 같음)
- 아래: "기록 삭제"(확인 대화상자, 기본은 취소). 실제 삭제는 MainWindow가 한다 (delete_requested).
  다른 화면으로는 내비게이션 바로 간다

횟수는 이어지는 같은 태그 슬롯 묶음 수 (core.history.tag_episodes).

    python -m gui.history_view                  # data/sessions.jsonl로 띄워 보기
    python -m gui.history_view --data-dir C:\\temp\\fp-data
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from core.history import SessionSummary, average_score, total_focus_seconds
from gui.report_view import STYLE as REPORT_STYLE
from gui.report_view import SCROLLBAR_WIDTH, TABLE_HEADER_HEIGHT, TABLE_ROW_HEIGHT
from gui.report_view import _make_card, _make_caption, _make_notice
from gui.style import APP_STYLE, COLORS, FINAL_TAG_COLORS, SPACING, font, load_fonts, qcolor, set_style_props

TITLE_TEXT = "집중 기록"
SUMMARY_SESSIONS = "세션"
SUMMARY_AVERAGE = "평균 집중도"
SUMMARY_FOCUS = "총 집중 시간"
CHART_CAPTION = "최근 추이"
TABLE_CAPTION = "세션 목록"
EMPTY_TEXT = "아직 저장된 기록이 없어요.\n세션을 마치면 여기에 쌓여요."
LOAD_FAILED_NOTICE = "기록 파일을 읽지 못했어요. 파일이 다른 프로그램에서 열려 있는지 확인해 주세요."
DELETE_FAILED_NOTICE = "기록을 모두 지우지 못했어요. 파일이 다른 프로그램에서 열려 있는지 확인해 주세요."
DELETED_NOTICE = "기록을 모두 삭제했어요."
DELETE_BUTTON_TEXT = "기록 삭제"
DELETE_CONFIRM_TITLE = "기록 삭제"
DELETE_CONFIRM_TEXT = (
    "저장된 세션 기록(sessions.jsonl)과 원본 기록(focus_log)을 모두 삭제할까요?\n"
    "삭제한 기록은 되돌릴 수 없어요."
)
DELETE_CONFIRM_YES = "삭제"
DELETE_CONFIRM_NO = "취소"
NO_SCORE_TEXT = "—"
LEGACY_SCORE_TIP = "이전 점수 방식(v3 이하)으로 저장된 기록이라 점수를 표시하지 않아요"
NO_SCORE_TIP = "판정된 시간이 부족해 점수를 계산하지 못했어요"
TABLE_HEADERS = ("날짜", "시간", "집중", "점수", "졸음", "딴짓", "정지")
TABLE_DATE_COLUMN = 0  # 글자 길이만큼, 나머지 열이 남는 폭을 나눠 가진다
WEEKDAYS_KO = "월화수목금토일"

CHART_SESSIONS = 20  # 그래프에 그리는 최근 세션 수
CHART_HEIGHT = 190
CHART_AXIS_WIDTH = 28  # 왼쪽 눈금 글자 자리
CHART_SCORE_RATIO = 0.6  # 위 점수 영역이 차지하는 높이 비율 (나머지는 횟수 막대)
CHART_SECTION_GAP = 24
CHART_POINT_RADIUS = 3.0
CHART_BAR_MAX_WIDTH = 8
SCORE_COLOR = "primary"
DROWSY_COLOR = FINAL_TAG_COLORS["drowsy"]
DISTRACTION_COLOR = FINAL_TAG_COLORS["distraction"]


def format_date(epoch: float) -> str:
    """"11.22 (일) 19:53" (로컬 시각)."""
    t = time.localtime(epoch)
    return f"{t.tm_mon}.{t.tm_mday} ({WEEKDAYS_KO[t.tm_wday]}) {t.tm_hour:02d}:{t.tm_min:02d}"


def format_minutes(seconds: float) -> str:
    """"12분" / "45초"(1분 미만) / "1시간 05분"."""
    total = max(int(seconds + 0.5), 0)
    if total < 60:
        return f"{total}초"
    minutes = total // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes:02d}분" if hours else f"{minutes}분"


def format_summary_score(summary: SessionSummary) -> str:
    return NO_SCORE_TEXT if summary.focus_score is None else str(summary.focus_score)


def score_tooltip(summary: SessionSummary) -> str:
    if summary.legacy:
        return LEGACY_SCORE_TIP
    if summary.focus_score is None:
        return NO_SCORE_TIP
    judged = "" if summary.judged_seconds is None else f" (판정 {format_minutes(summary.judged_seconds)})"
    return f"집중도 {summary.focus_score}{judged}"


def session_tooltip(summary: SessionSummary) -> str:
    score = NO_SCORE_TEXT if summary.focus_score is None else f"{summary.focus_score}점"
    return (
        f"{format_date(summary.session_start)}\n"
        f"집중도 {score} · 집중 {format_minutes(summary.focus_seconds)} / {format_minutes(summary.active_seconds)}\n"
        f"졸음 {summary.drowsy_count}회 · 딴짓 {summary.distraction_count}회 · 자리비움 {summary.away_count}회"
    )


class TrendChart(QWidget):
    """최근 세션의 집중도 선 + 졸음·딴짓 횟수 막대. 세션 하나가 가로 칸 하나 (오래된 것이 왼쪽)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sessions: list[SessionSummary] = []
        self.setMinimumHeight(CHART_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    @property
    def sessions(self) -> list[SessionSummary]:
        return list(self._sessions)

    def set_sessions(self, summaries: list[SessionSummary]) -> None:
        """오래된 것부터 정렬된 목록에서 최근 CHART_SESSIONS개만 그린다."""
        self._sessions = list(summaries[-CHART_SESSIONS:])
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt 이름)
        return QSize(400, CHART_HEIGHT)

    # 영역 계산 (paintEvent·툴팁 공통)
    def _plot_rect(self) -> QRectF:
        return QRectF(CHART_AXIS_WIDTH, 6, max(self.width() - CHART_AXIS_WIDTH - 4, 1), max(self.height() - 12, 1))

    def _areas(self) -> tuple[QRectF, QRectF]:
        plot = self._plot_rect()
        score_h = (plot.height() - CHART_SECTION_GAP) * CHART_SCORE_RATIO
        score = QRectF(plot.left(), plot.top(), plot.width(), score_h)
        counts = QRectF(plot.left(), score.bottom() + CHART_SECTION_GAP, plot.width(),
                        plot.bottom() - score.bottom() - CHART_SECTION_GAP)
        return score, counts

    def _column_width(self) -> float:
        return self._plot_rect().width() / max(len(self._sessions), 1)

    def _column_center(self, index: int) -> float:
        return self._plot_rect().left() + self._column_width() * (index + 0.5)

    def session_at(self, pos: QPoint) -> int | None:
        """pos가 가리키는 세션 칸 번호 (self.sessions 기준). 없으면 None."""
        plot = self._plot_rect()
        if not self._sessions or not plot.contains(QPointF(pos)):
            return None
        index = int((pos.x() - plot.left()) // self._column_width())
        return index if 0 <= index < len(self._sessions) else None

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self.session_at(event.pos())
            if index is None:
                QToolTip.hideText()
            else:
                QToolTip.showText(event.globalPos(), session_tooltip(self._sessions[index]), self)
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        score_area, count_area = self._areas()
        painter.setFont(font("telemetry_label"))

        # 점수 눈금 (0·50·100)
        for value in (0, 50, 100):
            y = score_area.bottom() - score_area.height() * value / 100
            painter.setPen(QPen(qcolor("outline_variant", 0.35), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(score_area.left(), y), QPointF(score_area.right(), y))
            painter.setPen(qcolor("outline"))
            painter.drawText(QRectF(0, y - 7, CHART_AXIS_WIDTH - 6, 14),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(value))

        # 횟수 영역 바닥선과 최댓값 눈금
        max_count = max([max(s.drowsy_count, s.distraction_count) for s in self._sessions] + [1])
        painter.setPen(QPen(qcolor("outline_variant", 0.6), 1))
        painter.drawLine(QPointF(count_area.left(), count_area.bottom()),
                         QPointF(count_area.right(), count_area.bottom()))
        painter.setPen(qcolor("outline"))
        painter.drawText(QRectF(0, count_area.top() - 7, CHART_AXIS_WIDTH - 6, 14),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{max_count}회")

        if not self._sessions:
            painter.end()
            return

        # 졸음·딴짓 막대 (칸 가운데에 나란히)
        bar_w = min(CHART_BAR_MAX_WIDTH, max(self._column_width() / 3, 2))
        painter.setPen(Qt.PenStyle.NoPen)
        for i, s in enumerate(self._sessions):
            cx = self._column_center(i)
            for count, color, x in ((s.drowsy_count, DROWSY_COLOR, cx - bar_w - 1),
                                    (s.distraction_count, DISTRACTION_COLOR, cx + 1)):
                if count <= 0:
                    continue
                h = count_area.height() * count / max_count
                painter.setBrush(qcolor(color, 0.85))
                painter.drawRoundedRect(QRectF(x, count_area.bottom() - h, bar_w, h), 1, 1)

        # 집중도 선: 점수가 없는 세션에서 끊는다
        points: list[QPointF | None] = []
        for i, s in enumerate(self._sessions):
            if s.focus_score is None:
                points.append(None)
                continue
            y = score_area.bottom() - score_area.height() * min(max(s.focus_score, 0), 100) / 100
            points.append(QPointF(self._column_center(i), y))
        path = QPainterPath()
        drawing = False
        for p in points:
            if p is None:
                drawing = False
            elif drawing:
                path.lineTo(p)
            else:
                path.moveTo(p)
                drawing = True
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(qcolor(SCORE_COLOR), 2))
        painter.drawPath(path)
        painter.setPen(QPen(qcolor("background"), 1.5))
        painter.setBrush(qcolor(SCORE_COLOR))
        for p in points:
            if p is not None:
                painter.drawEllipse(p, CHART_POINT_RADIUS, CHART_POINT_RADIUS)
        painter.end()


def legend_html() -> str:
    items = ((SCORE_COLOR, "집중도"), (DROWSY_COLOR, "졸음 횟수"), (DISTRACTION_COLOR, "딴짓 횟수"))
    return "&nbsp;&nbsp;&nbsp;".join(
        f'<span style="color:{COLORS[color]}">■</span> {text}' for color, text in items)


class HistoryView(QWidget):
    delete_requested = Signal()  # 확인을 받은 뒤 보낸다. 실제 삭제·다시 읽기는 MainWindow

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(REPORT_STYLE)
        self._sessions: list[SessionSummary] = []

        # 제목·요약
        title = QLabel(TITLE_TEXT)
        title.setFont(font("headline_md"))
        set_style_props(title, tone="default")
        stats = QGridLayout()
        stats.setHorizontalSpacing(SPACING["md"])
        stats.setVerticalSpacing(SPACING["xs"])
        self.count_label = self._add_stat(stats, 0, SUMMARY_SESSIONS)
        self.average_label = self._add_stat(stats, 1, SUMMARY_AVERAGE)
        self.focus_total_label = self._add_stat(stats, 2, SUMMARY_FOCUS)
        self.summary_card = _make_card(title)
        self.summary_card.layout().addSpacing(SPACING["xs"])
        self.summary_card.layout().addLayout(stats)

        self.notice_label = _make_notice("info")

        self.empty_label = QLabel(EMPTY_TEXT)
        self.empty_label.setFont(font("body_md"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style_props(self.empty_label, tone="muted")

        # 추이 그래프
        self.chart = TrendChart()
        self.legend_label = QLabel(legend_html())
        self.legend_label.setFont(font("body_sm"))
        self.legend_label.setTextFormat(Qt.TextFormat.RichText)
        set_style_props(self.legend_label, tone="variant")
        self.chart_caption = _make_caption(CHART_CAPTION)
        self.chart_card = _make_card(self.chart_caption, self.chart, self.legend_label)

        # 세션 목록
        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.horizontalHeaderItem(TABLE_DATE_COLUMN).setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)  # 숫자 열이 오른쪽에 몰리지 않게 고르게
        header.setSectionResizeMode(TABLE_DATE_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        header.setFixedHeight(TABLE_HEADER_HEIGHT)
        header.setFont(font("telemetry_label"))
        header.setHighlightSections(False)
        self.table_card = _make_card(_make_caption(TABLE_CAPTION), self.table)

        body = QWidget()
        body.setObjectName("reportBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"] - SCROLLBAR_WIDTH, SPACING["md"])
        body_layout.setSpacing(SPACING["sm"] + SPACING["xs"])
        for widget in (self.summary_card, self.notice_label, self.empty_label,
                       self.chart_card, self.table_card):
            body_layout.addWidget(widget)
        body_layout.addStretch()

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("reportScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll_area.viewport().setAutoFillBackground(False)
        self.scroll_area.setWidget(body)

        # 아래 버튼
        self.delete_button = QPushButton(DELETE_BUTTON_TEXT)
        set_style_props(self.delete_button, variant="destructive")
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        footer = QFrame()
        footer.setObjectName("reportFooter")
        buttons = QHBoxLayout(footer)
        margin_v = SPACING["sm"] + SPACING["xs"]
        buttons.setContentsMargins(SPACING["md"], margin_v, SPACING["md"], margin_v)
        buttons.setSpacing(SPACING["sm"])
        buttons.addStretch()
        buttons.addWidget(self.delete_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.scroll_area, stretch=1)
        layout.addWidget(footer)

        self.set_sessions([])

    @staticmethod
    def _add_stat(grid: QGridLayout, column: int, caption: str) -> QLabel:
        grid.addWidget(_make_caption(caption), 0, column)
        value = QLabel()
        value.setFont(font("telemetry_metric"))
        set_style_props(value, tone="default")
        grid.addWidget(value, 1, column)
        return value

    @property
    def sessions(self) -> list[SessionSummary]:
        return list(self._sessions)

    def set_sessions(self, summaries: list[SessionSummary]) -> None:
        """SessionSummary 목록(오래된 것 먼저, core.history.summarize_all)으로 화면을 채운다. 안내 문구는 지운다."""
        self._sessions = list(summaries)
        self.clear_notice()
        has = bool(self._sessions)
        average = average_score(self._sessions)
        self.count_label.setText(f"{len(self._sessions)}개")
        self.average_label.setText(NO_SCORE_TEXT if average is None else f"{average:.0f}")
        self.focus_total_label.setText(format_minutes(total_focus_seconds(self._sessions)))
        self.empty_label.setVisible(not has)
        self.chart_card.setVisible(has)
        self.table_card.setVisible(has)
        self.delete_button.setEnabled(has)
        self.chart.set_sessions(self._sessions)
        self.chart_caption.setText(f"{CHART_CAPTION} ({len(self.chart.sessions)}개 세션)")
        self._fill_table()

    def show_notice(self, kind: str, text: str) -> None:
        """kind: "error" / "warning" / "info"."""
        set_style_props(self.notice_label, notice=kind)
        self.notice_label.setText(text)
        self.notice_label.show()

    def clear_notice(self) -> None:
        self.notice_label.clear()
        self.notice_label.hide()

    def show_load_failed(self) -> None:
        """기록 파일을 읽지 못함: 목록을 비우고 안내한다. 지울 수도 있게 삭제 버튼은 켠다."""
        self.set_sessions([])
        self.empty_label.hide()
        self.delete_button.setEnabled(True)
        self.show_notice("error", LOAD_FAILED_NOTICE)

    def confirm_delete(self) -> bool:
        """삭제 확인 대화상자. 기본 버튼은 취소. 테스트에서는 이 메서드를 바꿔 끼운다."""
        box = QMessageBox(QMessageBox.Icon.Warning, DELETE_CONFIRM_TITLE, DELETE_CONFIRM_TEXT, parent=self)
        yes = box.addButton(DELETE_CONFIRM_YES, QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton(DELETE_CONFIRM_NO, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.setEscapeButton(no)
        box.exec()
        return box.clickedButton() is yes

    def _on_delete_clicked(self) -> None:
        if self.confirm_delete():
            self.delete_requested.emit()

    def _fill_table(self) -> None:
        rows = list(reversed(self._sessions))  # 최신순
        self.table.setRowCount(len(rows))
        for row, s in enumerate(rows):
            score_color = "outline" if s.focus_score is None else "on_surface"
            cells = (
                (format_date(s.session_start), "on_surface", False, session_tooltip(s)),
                (format_minutes(s.active_seconds), "on_surface_variant", True, None),
                (format_minutes(s.focus_seconds), FINAL_TAG_COLORS["focus"], True, None),
                (format_summary_score(s), score_color, True, score_tooltip(s)),
                (str(s.drowsy_count), DROWSY_COLOR if s.drowsy_count else "outline", True, None),
                (str(s.distraction_count), DISTRACTION_COLOR if s.distraction_count else "outline", True, None),
                (str(s.pause_count), "on_surface_variant" if s.pause_count else "outline", True,
                 f"정지 {format_minutes(s.pause_seconds)}" if s.pause_count else None),
            )
            for col, (text, color, mono, tip) in enumerate(cells):
                item = QTableWidgetItem(text)
                align = Qt.AlignmentFlag.AlignLeft if col == TABLE_DATE_COLUMN else Qt.AlignmentFlag.AlignHCenter
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                item.setForeground(qcolor(color))
                item.setFont(font("telemetry_metric", 12) if mono else font("body_sm"))
                if tip:
                    item.setToolTip(tip)
                self.table.setItem(row, col, item)
        self.table.setFixedHeight(TABLE_HEADER_HEIGHT + len(rows) * TABLE_ROW_HEIGHT + 2)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.fillRect(self.rect(), qcolor("background"))
        painter.end()
        super().paintEvent(event)


def main() -> int:
    from core.history import summarize_all
    from storage import session_store

    parser = argparse.ArgumentParser(description="FocusPomo 기록 화면만 띄워 보기")
    parser.add_argument("--data-dir", type=Path, default=session_store.DATA_DIR)
    args = parser.parse_args()

    app = QApplication(sys.argv)
    load_fonts()
    view = HistoryView()
    view.setStyleSheet(APP_STYLE + REPORT_STYLE)
    try:
        view.set_sessions(summarize_all(session_store.load_sessions(args.data_dir)))
    except OSError:
        view.show_load_failed()
    view.delete_requested.connect(lambda: view.show_notice("info", "(미리보기: 실제로 지우지 않음)"))
    view.resize(580, 640)
    view.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
