"""휴식 리포트 화면 (개발계획서 2.3 출력, 2.4, 2.7).

세션이 끝나면 MainWindow가 show_result()로 SessionResult를 넘기고 이 화면으로 온다.

- 총점(원점수), 깜빡임 가점 합, 졸음·자리 이탈·딴짓 슬롯 수
- 일시정지 횟수·총 시간 (schemas.pause_total), 정지 구간과 겹쳐 점수에서 뺀 슬롯 수
- report_text, llm_call_failed=True면 "웹캠 신호만으로 계산" 안내
- 슬롯별 표: 구간(세션 시작 기준 경과 시간), 창 분류, 상태(final_tag), 깜빡임, 점수
  enum은 schemas.WINDOW_LABEL_KO, FINAL_TAG_KO로 한글 표시

score_total 표시 형식(원점수/백분율/등급)은 팀 미정이라 우선 원점수로 보여준다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.timer_view import format_mmss
from schemas import FINAL_TAG_KO, WINDOW_LABEL_KO, SessionResult, pause_total

TITLE_TEXT = "세션 리포트"
HOME_BUTTON_TEXT = "처음으로"
LLM_FAILED_NOTICE = "창 분류를 받지 못해 이번 세션은 웹캠 신호만으로 계산했어요 (딴짓 감점 없음)."
SAVE_FAILED_NOTICE = "이번 세션 기록을 저장하지 못했어요."
EMPTY_TABLE_TEXT = "점수를 매긴 구간이 없어요."
TABLE_HEADERS = ("구간", "창 분류", "상태", "깜빡임", "점수")

STYLE = """
#reportTitle { font-size: 22px; font-weight: bold; }
#scoreLabel { font-size: 40px; font-weight: bold; }
#scoreCaption, #statCaption { color: #666666; font-size: 12px; }
#statValue { font-size: 18px; font-weight: 600; }
#reportText { font-size: 14px; }
#warningLabel { color: #c0392b; }
#infoLabel { color: #666666; }
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


class ReportView(QWidget):
    home_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self._result: SessionResult | None = None

        title = QLabel(TITLE_TEXT)
        title.setObjectName("reportTitle")

        self.score_label = QLabel()
        self.score_label.setObjectName("scoreLabel")
        score_caption = QLabel("총점 (원점수)")
        score_caption.setObjectName("scoreCaption")
        score_box = QVBoxLayout()
        score_box.setSpacing(0)
        score_box.addWidget(score_caption)
        score_box.addWidget(self.score_label)

        stats = QGridLayout()
        stats.setHorizontalSpacing(24)
        self.blink_bonus_label = self._add_stat(stats, 0, "깜빡임 가점")
        self.drowsy_label = self._add_stat(stats, 1, "졸음")
        self.away_label = self._add_stat(stats, 2, "자리 이탈")
        self.distraction_label = self._add_stat(stats, 3, "딴짓")

        header = QHBoxLayout()
        header.addLayout(score_box)
        header.addSpacing(32)
        header.addLayout(stats)
        header.addStretch()

        self.pause_label = QLabel()
        self.pause_label.setObjectName("infoLabel")
        self.llm_notice_label = QLabel(LLM_FAILED_NOTICE)
        self.llm_notice_label.setObjectName("warningLabel")
        self.llm_notice_label.setWordWrap(True)
        self.save_notice_label = QLabel(SAVE_FAILED_NOTICE)
        self.save_notice_label.setObjectName("warningLabel")

        self.report_text_label = QLabel()
        self.report_text_label.setObjectName("reportText")
        self.report_text_label.setWordWrap(True)
        self.report_text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.empty_table_label = QLabel(EMPTY_TABLE_TEXT)
        self.empty_table_label.setObjectName("infoLabel")
        self.empty_table_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.home_button = QPushButton(HOME_BUTTON_TEXT)
        self.home_button.clicked.connect(self.home_requested)
        self.buttons = QHBoxLayout()
        self.buttons.addStretch()
        self.buttons.addWidget(self.home_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.pause_label)
        layout.addWidget(self.llm_notice_label)
        layout.addWidget(self.save_notice_label)
        layout.addSpacing(8)
        layout.addWidget(self.report_text_label)
        layout.addSpacing(8)
        layout.addWidget(self.table, stretch=1)
        layout.addWidget(self.empty_table_label)
        layout.addLayout(self.buttons)

        self.clear()

    @staticmethod
    def _add_stat(grid: QGridLayout, column: int, caption: str) -> QLabel:
        caption_label = QLabel(caption)
        caption_label.setObjectName("statCaption")
        value = QLabel()
        value.setObjectName("statValue")
        grid.addWidget(caption_label, 0, column)
        grid.addWidget(value, 1, column)
        return value

    @property
    def result(self) -> SessionResult | None:
        return self._result

    def clear(self) -> None:
        self._result = None
        for label in (self.score_label, self.blink_bonus_label, self.drowsy_label, self.away_label,
                      self.distraction_label, self.report_text_label, self.pause_label):
            label.clear()
        self.pause_label.hide()
        self.llm_notice_label.hide()
        self.save_notice_label.hide()
        self.table.setRowCount(0)
        self.empty_table_label.hide()

    def show_result(self, result: SessionResult, *, save_failed: bool = False, excluded_slots: int = 0) -> None:
        """SessionResult를 화면에 채운다. excluded_slots: 정지 구간과 겹쳐 점수에서 뺀 슬롯 수."""
        self.clear()
        self._result = result
        self.score_label.setText(f"{result['score_total']}점")
        self.blink_bonus_label.setText(format_signed(result["blink_bonus_total"]))
        self.drowsy_label.setText(f"{result['drowsy_count']}구간")
        self.away_label.setText(f"{result['away_count']}구간")
        self.distraction_label.setText(f"{result['distraction_count']}구간")
        self.report_text_label.setText(result["report_text"])

        pause_text = self._pause_text(result, excluded_slots)
        if pause_text:
            self.pause_label.setText(pause_text)
            self.pause_label.show()
        self.llm_notice_label.setVisible(result["llm_call_failed"])
        self.save_notice_label.setVisible(save_failed)

        self._fill_table(result)

    @staticmethod
    def _pause_text(result: SessionResult, excluded_slots: int) -> str:
        parts = []
        pauses = result["pause_intervals"]
        if pauses:
            parts.append(f"일시정지 {len(pauses)}회 · 총 {format_duration(pause_total(pauses))}")
        if excluded_slots:
            parts.append(f"정지와 겹친 {excluded_slots}구간(10초 단위)은 점수에서 뺐어요")
        return " — ".join(parts)

    def _fill_table(self, result: SessionResult) -> None:
        rows = result["slot_scores"]
        self.table.setRowCount(len(rows))
        for row, s in enumerate(rows):
            values = (
                format_span(s["start"], s["end"], result["session_start"]),
                WINDOW_LABEL_KO.get(s["window_label"], s["window_label"]),
                FINAL_TAG_KO.get(s["final_tag"], s["final_tag"]),
                str(s["blink_count"]),
                format_signed(s["score"]),
            )
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        self.table.setVisible(bool(rows))
        self.empty_table_label.setVisible(not rows)
