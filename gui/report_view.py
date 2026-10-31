"""휴식 리포트 화면 (개발계획서 2.3 출력, 2.4, 2.7, 3-06 디자인).

세션이 끝나면 MainWindow가 show_result()로 SessionResult를 넘기고 이 화면으로 온다.

- 위: 휴식 카운트다운(break_bar, 1-15), 아래: "다음 세션 시작" / "처음으로" 버튼. 둘은 고정하고 가운데만 스크롤한다
- 요약: 총점(원점수), 깜빡임 가점 합, 졸음·자리 이탈·딴짓 슬롯 수, 세션 시작 시각과 길이(정지 시간 제외)
- 안내: 일시정지 횟수·총 시간(schemas.pause_total), 정지와 겹쳐 뺀 슬롯 수, LLM 실패, 저장 실패
- report_text
- 10초 타임라인: 슬롯 하나가 막대 하나, 색은 final_tag. 정지로 점수에서 뺀 슬롯은 빈 칸
- 슬롯별 표: 구간(세션 시작 기준 경과 시간), 창 분류, 상태(final_tag), 깜빡임, 점수.
  표 자체는 스크롤하지 않고 전체 행 높이를 차지한다 (스크롤이 겹치지 않게)
  enum은 schemas.WINDOW_LABEL_KO, FINAL_TAG_KO로 한글 표시

score_total 표시 형식(원점수/백분율/등급)은 팀 미정이라 우선 원점수로 보여준다.

    python -m gui.report_view     # mock 결과로 리포트 화면만 띄워 보기
"""
from __future__ import annotations

import math
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from gui.break_timer import BreakTimerBar
from gui.style import (
    APP_STYLE,
    COLORS,
    FINAL_TAG_COLORS,
    SPACING,
    TIMELINE_EMPTY_ALPHA,
    TIMELINE_EMPTY_COLOR,
    TIMELINE_SLOT_GAP,
    TIMELINE_SLOT_HEIGHT,
    TIMELINE_SLOT_RADIUS,
    TIMELINE_SLOT_WIDTH,
    WINDOW_LABEL_COLORS,
    font,
    load_fonts,
    qcolor,
    rgba,
    set_style_props,
)
from gui.timer_view import format_mmss
from schemas import FINAL_TAG_KO, SLOT_SECONDS, WINDOW_LABEL_KO, SessionResult, SlotScore, pause_total

TITLE_TEXT = "세션 리포트"
SCORE_CAPTION = "총점 (원점수)"
SUMMARY_CAPTION = "요약"
TIMELINE_CAPTION = "10초 타임라인"
TABLE_CAPTION = "구간별 기록"
HOME_BUTTON_TEXT = "처음으로"
NEXT_BUTTON_TEXT = "다음 세션 시작"
LLM_FAILED_NOTICE = "창 분류를 받지 못해 이번 세션은 웹캠 신호만으로 계산했어요 (딴짓 감점 없음)."
SAVE_FAILED_NOTICE = "이번 세션 기록을 저장하지 못했어요."
EMPTY_TABLE_TEXT = "점수를 매긴 구간이 없어요."
PAUSED_SLOT_TEXT = "일시정지"
TABLE_HEADERS = ("구간", "창 분류", "상태", "깜빡임", "점수")

SCORE_FONT_PX = 48
TABLE_ROW_HEIGHT = 30
TABLE_HEADER_HEIGHT = 28
TIMELINE_ROW_GAP = 2 * TIMELINE_SLOT_GAP
SCROLLBAR_WIDTH = 8

STYLE = f"""
#reportBody {{ background: transparent; }}
#reportScroll {{ background: transparent; border: none; }}
#reportFooter {{
    background: {rgba("surface_container_lowest", 0.6)};
    border-top: 1px solid {rgba("outline_variant", 0.2)};
}}
QTableWidget {{
    background: transparent;
    border: none;
    gridline-color: transparent;
    color: {COLORS["on_surface"]};
}}
QTableWidget::item {{ border-bottom: 1px solid {rgba("outline_variant", 0.25)}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {rgba("outline_variant", 0.6)};
    color: {COLORS["outline"]};
}}
QScrollBar:vertical {{ background: transparent; width: {SCROLLBAR_WIDTH}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {rgba("outline_variant", 0.6)}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {COLORS["outline"]}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; height: 0; }}
"""


def format_duration(seconds: float) -> str:
    """정지 시간 같은 길이를 "1분 05초" / "45초"로. 0.5초 미만은 버린다."""
    total = max(int(seconds + 0.5), 0)
    minutes, secs = divmod(total, 60)
    return f"{minutes}분 {secs:02d}초" if minutes else f"{secs}초"


def format_span(start: float, end: float, session_start: float) -> str:
    """슬롯 구간을 세션 시작 기준 경과 시간 "MM:SS–MM:SS"로 (정지 시간 포함)."""
    return f"{format_mmss(round(start - session_start))}–{format_mmss(round(end - session_start))}"


def format_signed(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def format_session_meta(result: SessionResult) -> str:
    """"10월 12일 14:30 · 25분 00초" (세션 시작 로컬 시각 · 정지 시간을 뺀 길이)."""
    started = time.localtime(result["session_start"])
    active = result["session_end"] - result["session_start"] - pause_total(result["pause_intervals"])
    return f"{started.tm_mon}월 {started.tm_mday}일 {started.tm_hour:02d}:{started.tm_min:02d} · {format_duration(active)}"


def timeline_slots(result: SessionResult) -> list[SlotScore | None]:
    """0번부터 세션 끝까지 슬롯별 SlotScore. 정지와 겹쳐 빠진 슬롯은 None."""
    by_slot = {s["slot"]: s for s in result["slot_scores"]}
    count = math.ceil((result["session_end"] - result["session_start"]) / SLOT_SECONDS - 1e-6)
    count = max(count, max(by_slot, default=-1) + 1)
    return [by_slot.get(slot) for slot in range(count)]


def legend_html(slots: list[SlotScore | None]) -> str:
    """타임라인 범례: 나온 태그만 색 사각형 + 이름 + 개수."""
    entries = []
    for tag, color in FINAL_TAG_COLORS.items():
        count = sum(1 for s in slots if s is not None and s["final_tag"] == tag)
        if count:
            entries.append((COLORS[color], FINAL_TAG_KO[tag], count))
    paused = slots.count(None)
    if paused:
        entries.append((COLORS["outline_variant"], PAUSED_SLOT_TEXT, paused))
    return " &nbsp; ".join(
        f'<span style="white-space: nowrap;"><span style="color: {color};">■</span>&nbsp;{name}&nbsp;{count}</span>'
        for color, name, count in entries
    )


class SlotTimeline(QWidget):
    """10초 슬롯 하나를 막대 하나로 그린다. 폭에 다 안 들어가면 다음 줄로 넘긴다. 막대에 마우스를 올리면 구간과 상태."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slots: list[SlotScore | None] = []
        self._session_start = 0.0
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_slots(self, slots: list[SlotScore | None], session_start: float) -> None:
        self._slots = slots
        self._session_start = session_start
        self.updateGeometry()
        self.update()

    @staticmethod
    def _per_row(width: int) -> int:
        return max((width + TIMELINE_SLOT_GAP) // (TIMELINE_SLOT_WIDTH + TIMELINE_SLOT_GAP), 1)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt 이름)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt 이름)
        rows = max(math.ceil(len(self._slots) / self._per_row(width)), 1)
        return rows * TIMELINE_SLOT_HEIGHT + (rows - 1) * TIMELINE_ROW_GAP

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt 이름)
        return QSize(240, self.heightForWidth(240))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt 이름)
        return QSize(TIMELINE_SLOT_WIDTH, TIMELINE_SLOT_HEIGHT)

    def slot_at(self, pos: QPoint) -> int | None:
        """좌표에 있는 막대의 슬롯 번호. 막대 밖이면 None."""
        per_row = self._per_row(self.width())
        column = pos.x() // (TIMELINE_SLOT_WIDTH + TIMELINE_SLOT_GAP)
        row = pos.y() // (TIMELINE_SLOT_HEIGHT + TIMELINE_ROW_GAP)
        if pos.x() < 0 or pos.y() < 0 or column >= per_row:
            return None
        index = row * per_row + column
        return index if index < len(self._slots) else None

    def tooltip_text(self, slot: int) -> str:
        score = self._slots[slot]
        begin = self._session_start + slot * SLOT_SECONDS
        if score is None:
            return f"{format_span(begin, begin + SLOT_SECONDS, self._session_start)} · {PAUSED_SLOT_TEXT} (점수 제외)"
        span = format_span(score["start"], score["end"], self._session_start)
        return f"{span} · {FINAL_TAG_KO.get(score['final_tag'], score['final_tag'])}"

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            slot = self.slot_at(event.pos())
            if slot is None:
                QToolTip.hideText()
            else:
                QToolTip.showText(event.globalPos(), self.tooltip_text(slot), self)
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        per_row = self._per_row(self.width())
        for index, score in enumerate(self._slots):
            row, column = divmod(index, per_row)
            if score is None:
                painter.setBrush(qcolor(TIMELINE_EMPTY_COLOR, TIMELINE_EMPTY_ALPHA))
            else:
                painter.setBrush(qcolor(FINAL_TAG_COLORS.get(score["final_tag"], "outline")))
            rect = QRectF(column * (TIMELINE_SLOT_WIDTH + TIMELINE_SLOT_GAP),
                          row * (TIMELINE_SLOT_HEIGHT + TIMELINE_ROW_GAP),
                          TIMELINE_SLOT_WIDTH, TIMELINE_SLOT_HEIGHT)
            painter.drawRoundedRect(rect, TIMELINE_SLOT_RADIUS, TIMELINE_SLOT_RADIUS)
        painter.end()


def _make_card(*widgets: QWidget) -> QFrame:
    card = QFrame()
    set_style_props(card, surface="card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"])
    layout.setSpacing(SPACING["sm"])
    for widget in widgets:
        layout.addWidget(widget)
    return card


def _make_caption(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setFont(font("telemetry_label"))
    set_style_props(label, tone="muted")
    return label


def _make_notice(kind: str, text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    set_style_props(label, notice=kind)
    return label


class ReportView(QWidget):
    home_requested = Signal()
    next_session_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self._result: SessionResult | None = None

        self.break_bar = BreakTimerBar()

        # 요약: 제목·시각 / 총점 / 항목별 수
        title = QLabel(TITLE_TEXT)
        title.setFont(font("headline_md"))
        set_style_props(title, tone="default")
        self.meta_label = QLabel()
        self.meta_label.setFont(font("body_sm"))
        set_style_props(self.meta_label, tone="muted")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.meta_label)

        self.score_label = QLabel()
        self.score_label.setFont(font("display_timer", SCORE_FONT_PX))
        set_style_props(self.score_label, tone="default")

        stats = QGridLayout()
        stats.setHorizontalSpacing(SPACING["md"])
        stats.setVerticalSpacing(SPACING["xs"])
        self.blink_bonus_label = self._add_stat(stats, 0, "깜빡임 가점")
        self.drowsy_label = self._add_stat(stats, 1, "졸음")
        self.away_label = self._add_stat(stats, 2, "자리 이탈")
        self.distraction_label = self._add_stat(stats, 3, "딴짓")

        summary = _make_card()
        summary_layout = summary.layout()
        summary_layout.addLayout(title_row)
        summary_layout.addWidget(_make_caption(SCORE_CAPTION))
        summary_layout.addWidget(self.score_label)
        summary_layout.addSpacing(SPACING["xs"])
        summary_layout.addLayout(stats)

        # 안내
        self.pause_label = _make_notice("info")
        self.llm_notice_label = _make_notice("warning", LLM_FAILED_NOTICE)
        self.save_notice_label = _make_notice("error", SAVE_FAILED_NOTICE)

        # LLM 리포트 문구
        self.report_text_label = QLabel()
        self.report_text_label.setFont(font("body_md"))
        self.report_text_label.setWordWrap(True)
        self.report_text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_style_props(self.report_text_label, tone="variant")
        self.report_card = _make_card(_make_caption(SUMMARY_CAPTION), self.report_text_label)

        # 10초 타임라인
        self.timeline = SlotTimeline()
        self.legend_label = QLabel()
        self.legend_label.setFont(font("body_sm"))
        self.legend_label.setTextFormat(Qt.TextFormat.RichText)
        self.legend_label.setWordWrap(True)
        set_style_props(self.legend_label, tone="variant")
        self.timeline_card = _make_card(_make_caption(TIMELINE_CAPTION), self.timeline, self.legend_label)

        # 슬롯별 표
        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setFixedHeight(TABLE_HEADER_HEIGHT)
        header.setFont(font("telemetry_label"))
        header.setHighlightSections(False)
        self.empty_table_label = QLabel(EMPTY_TABLE_TEXT)
        self.empty_table_label.setFont(font("body_sm"))
        self.empty_table_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style_props(self.empty_table_label, tone="muted")
        table_card = _make_card(_make_caption(TABLE_CAPTION), self.table, self.empty_table_label)

        # 가운데 스크롤 영역
        body = QWidget()
        body.setObjectName("reportBody")
        body_layout = QVBoxLayout(body)
        # 오른쪽은 스크롤바 폭만큼 줄여 휴식 막대와 카드의 오른쪽 끝을 맞춘다
        body_layout.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"] - SCROLLBAR_WIDTH, SPACING["md"])
        body_layout.setSpacing(SPACING["sm"] + SPACING["xs"])
        for widget in (summary, self.pause_label, self.llm_notice_label, self.save_notice_label,
                       self.report_card, self.timeline_card, table_card):
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
        self.home_button = QPushButton(HOME_BUTTON_TEXT)
        set_style_props(self.home_button, variant="secondary")
        self.home_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_button.clicked.connect(self.home_requested)
        self.next_button = QPushButton(NEXT_BUTTON_TEXT)
        set_style_props(self.next_button, variant="primary")
        self.next_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self.next_session_requested)
        footer = QFrame()
        footer.setObjectName("reportFooter")
        self.buttons = QHBoxLayout(footer)
        self.buttons.setContentsMargins(SPACING["md"], SPACING["sm"] + SPACING["xs"], SPACING["md"], SPACING["sm"] + SPACING["xs"])
        self.buttons.setSpacing(SPACING["sm"])
        self.buttons.addStretch()
        self.buttons.addWidget(self.home_button)
        self.buttons.addWidget(self.next_button)

        top = QVBoxLayout()
        top.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], 0)
        top.addWidget(self.break_bar)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self.scroll_area, stretch=1)
        layout.addWidget(footer)

        self.clear()

    @staticmethod
    def _add_stat(grid: QGridLayout, column: int, caption: str) -> QLabel:
        grid.addWidget(_make_caption(caption), 0, column)
        value = QLabel()
        value.setFont(font("telemetry_metric"))
        grid.addWidget(value, 1, column)
        grid.setColumnStretch(column, 1)
        return value

    @property
    def result(self) -> SessionResult | None:
        return self._result

    def clear(self) -> None:
        self._result = None
        for label in (self.score_label, self.blink_bonus_label, self.drowsy_label, self.away_label,
                      self.distraction_label, self.report_text_label, self.pause_label, self.meta_label,
                      self.legend_label):
            label.clear()
        self.pause_label.hide()
        self.llm_notice_label.hide()
        self.save_notice_label.hide()
        self.report_card.hide()
        self.timeline_card.hide()
        self.timeline.set_slots([], 0.0)
        self.table.setRowCount(0)
        self.empty_table_label.hide()

    def show_result(self, result: SessionResult, *, save_failed: bool = False, excluded_slots: int = 0) -> None:
        """SessionResult를 화면에 채운다. excluded_slots: 정지 구간과 겹쳐 점수에서 뺀 슬롯 수."""
        self.clear()
        self._result = result
        self.score_label.setText(f"{result['score_total']}점")
        self.meta_label.setText(format_session_meta(result))
        self._set_stat(self.blink_bonus_label, format_signed(result["blink_bonus_total"]),
                       "focus" if result["blink_bonus_total"] > 0 else "muted")
        self._set_count(self.drowsy_label, result["drowsy_count"], "warning")
        self._set_count(self.away_label, result["away_count"], "alert")
        self._set_count(self.distraction_label, result["distraction_count"], "alert")
        self.report_text_label.setText(result["report_text"])
        self.report_card.setVisible(bool(result["report_text"]))

        pause_text = self._pause_text(result, excluded_slots)
        if pause_text:
            self.pause_label.setText(pause_text)
            self.pause_label.show()
        self.llm_notice_label.setVisible(result["llm_call_failed"])
        self.save_notice_label.setVisible(save_failed)

        self._fill_timeline(result)
        self._fill_table(result)
        self.scroll_area.verticalScrollBar().setValue(0)

    @staticmethod
    def _set_stat(label: QLabel, text: str, tone: str) -> None:
        label.setText(text)
        set_style_props(label, tone=tone)

    @classmethod
    def _set_count(cls, label: QLabel, count: int, tone: str) -> None:
        cls._set_stat(label, f"{count}구간", tone if count else "muted")

    @staticmethod
    def _pause_text(result: SessionResult, excluded_slots: int) -> str:
        parts = []
        pauses = result["pause_intervals"]
        if pauses:
            parts.append(f"일시정지 {len(pauses)}회 · 총 {format_duration(pause_total(pauses))}")
        if excluded_slots:
            parts.append(f"정지와 겹친 {excluded_slots}구간(10초 단위)은 점수에서 뺐어요")
        return " — ".join(parts)

    def _fill_timeline(self, result: SessionResult) -> None:
        slots = timeline_slots(result)
        self.timeline.set_slots(slots, result["session_start"])
        self.legend_label.setText(legend_html(slots))
        self.timeline_card.setVisible(bool(slots))

    def _fill_table(self, result: SessionResult) -> None:
        rows = result["slot_scores"]
        self.table.setRowCount(len(rows))
        for row, s in enumerate(rows):
            score_color = "secondary" if s["score"] > 0 else "error" if s["score"] < 0 else "outline"
            cells = (
                (format_span(s["start"], s["end"], result["session_start"]), "on_surface_variant", True),
                (WINDOW_LABEL_KO.get(s["window_label"], s["window_label"]),
                 WINDOW_LABEL_COLORS.get(s["window_label"], "outline"), False),
                (FINAL_TAG_KO.get(s["final_tag"], s["final_tag"]),
                 FINAL_TAG_COLORS.get(s["final_tag"], "outline"), False),
                (str(s["blink_count"]), "on_surface_variant", True),
                (format_signed(s["score"]), score_color, True),
            )
            for col, (text, color, mono) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(qcolor(color))
                item.setFont(font("telemetry_metric", 12) if mono else font("body_sm"))
                self.table.setItem(row, col, item)
        # 표는 스크롤하지 않고 바깥 스크롤 영역에 맡긴다
        self.table.setFixedHeight(TABLE_HEADER_HEIGHT + len(rows) * TABLE_ROW_HEIGHT + 2)
        self.table.setVisible(bool(rows))
        self.empty_table_label.setVisible(not rows)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.fillRect(self.rect(), qcolor("background"))
        painter.end()
        super().paintEvent(event)


def main() -> int:
    from core.slot_log import SlotLog
    from mocks.mock_calibration import make_calibration
    from mocks.mock_scoring import MockScorer
    from mocks.mock_session import build_scenario, messages_only
    from core.pomodoro import BreakKind

    app = QApplication(sys.argv)
    load_fonts()
    start = time.time() - 60
    log = SlotLog(start)
    for msg in messages_only(build_scenario("normal", start)):
        log.add(msg)
    result = MockScorer()(log.finalize(start + 60), make_calibration(start - 5, face_detected_ratio=1.0), [],
                          session_id="20261003-100000", session_start=start, session_end=start + 60)
    view = ReportView()
    view.setStyleSheet(APP_STYLE + STYLE)
    view.show_result(result)
    view.break_bar.start(BreakKind.SHORT, 1)
    view.resize(580, 580)
    view.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
