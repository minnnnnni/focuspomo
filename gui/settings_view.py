"""세션 설정 화면: 집중 시간, 짧은·긴 휴식 시간, 긴 휴식 주기(몇 세션마다)를 바꾼다.

내비게이션 바의 "설정"으로 온다. 값은 core.pomodoro.SETTING_LIMITS 범위 안에서 고르고,
"저장"을 누르면 save_requested(PomodoroSettings)를 보낸다. 파일 저장은 MainWindow가 하고(storage.settings_store)
결과를 show_saved() / show_notice()로 알려 준다. 바뀐 값은 다음 세션부터 쓴다.
저장하지 않고 다른 화면으로 갔다 오면 MainWindow가 set_settings()로 저장된 값으로 되돌린다.

    python -m gui.settings_view     # 설정 화면만 띄워 보기
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.pomodoro import DEFAULT_SETTINGS, SETTING_LIMITS, PomodoroSettings
from gui.report_view import STYLE as REPORT_STYLE
from gui.report_view import _make_card, _make_caption, _make_notice
from gui.style import APP_STYLE, COLORS, MONO, RADIUS_CONTROL, SPACING, font, load_fonts, qcolor, rgba, set_style_props

TITLE_TEXT = "세션 설정"
SUBTITLE_TEXT = "바꾼 값은 다음 세션부터 적용돼요."
CYCLE_CAPTION = "한 사이클"
SAVE_BUTTON_TEXT = "저장"
RESET_BUTTON_TEXT = "기본값으로"
SAVED_NOTICE = "설정을 저장했어요. 다음 세션부터 적용돼요."
SAVE_FAILED_NOTICE = "설정을 저장하지 못했어요. 폴더 권한을 확인해 주세요."

# (필드, 이름, 설명, 단위)
FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("focus_min", "집중 시간", "한 세션 동안 집중하는 시간", "분"),
    ("short_break_min", "짧은 휴식", "세션이 끝날 때마다 쉬는 시간", "분"),
    ("long_break_min", "긴 휴식", "주기를 다 채우면 쉬는 시간", "분"),
    ("long_break_every", "긴 휴식 주기", "끝까지 마친 세션이 이만큼 쌓이면 긴 휴식", "세션"),
)

STEPPER_HEIGHT = 32
STEPPER_VALUE_WIDTH = 72

STYLE = REPORT_STYLE + f"""
#stepButton {{
    background: {rgba("surface_container_high", 0.8)};
    border: 1px solid {rgba("outline_variant", 0.6)};
    border-radius: {RADIUS_CONTROL}px;
    color: {COLORS["on_surface"]};
    font-size: 16px;
    font-weight: 600;
    padding: 0;
}}
#stepButton:hover {{ border-color: {rgba("primary", 0.5)}; background: {COLORS["surface_container_highest"]}; }}
#stepButton:pressed {{ background: {COLORS["surface_container_highest"]}; }}
#stepButton:disabled {{ color: {COLORS["outline_variant"]}; border-color: {rgba("outline_variant", 0.3)}; }}
#stepValue {{
    background: {rgba("surface_container_lowest", 0.6)};
    border: 1px solid {rgba("outline_variant", 0.6)};
    border-radius: {RADIUS_CONTROL}px;
    color: {COLORS["on_surface"]};
    font-family: {MONO};
    font-size: 14px;
    font-weight: 600;
    padding: 0 6px;
    selection-background-color: {rgba("primary_container", 0.6)};
}}
#stepValue:focus {{ border-color: {COLORS["primary"]}; }}
"""


def format_total(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}시간 {mins}분"
    return f"{hours}시간" if hours else f"{mins}분"


def cycle_text(settings: PomodoroSettings) -> str:
    """한 사이클(긴 휴식까지) 요약. 예: "집중 25분 × 4 → 짧은 휴식 5분 × 3 → 긴 휴식 15분 (총 2시간 10분)"."""
    every = settings.long_break_every
    total = settings.focus_min * every + settings.short_break_min * (every - 1) + settings.long_break_min
    parts = [f"집중 {settings.focus_min}분 × {every}"]
    if every > 1:
        parts.append(f"짧은 휴식 {settings.short_break_min}분 × {every - 1}")
    parts.append(f"긴 휴식 {settings.long_break_min}분")
    return " → ".join(parts) + f"  (총 {format_total(total)})"


class Stepper(QWidget):
    """− [값] + 숫자 입력. 값은 직접 입력하거나 버튼·방향키·휠로 바꾼다."""

    def __init__(self, minimum: int, maximum: int, suffix: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spin = QSpinBox()
        self.spin.setObjectName("stepValue")
        self.spin.setRange(minimum, maximum)
        self.spin.setSuffix(f" {suffix}")
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin.setFixedSize(STEPPER_VALUE_WIDTH, STEPPER_HEIGHT)
        self.minus_button = self._make_button("−", self.spin.stepDown)
        self.plus_button = self._make_button("+", self.spin.stepUp)
        self.spin.valueChanged.connect(self._update_buttons)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["xs"])
        layout.addWidget(self.minus_button)
        layout.addWidget(self.spin)
        layout.addWidget(self.plus_button)
        self._update_buttons()

    def _make_button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("stepButton")
        button.setFixedSize(STEPPER_HEIGHT, STEPPER_HEIGHT)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoRepeat(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        return button

    def _update_buttons(self) -> None:
        self.minus_button.setEnabled(self.spin.value() > self.spin.minimum())
        self.plus_button.setEnabled(self.spin.value() < self.spin.maximum())

    def value(self) -> int:
        return self.spin.value()

    def set_value(self, value: int) -> None:
        self.spin.setValue(value)


class SettingsView(QWidget):
    save_requested = Signal(object)  # PomodoroSettings

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self._saved = DEFAULT_SETTINGS

        title = QLabel(TITLE_TEXT)
        title.setFont(font("headline_md"))
        set_style_props(title, tone="default")
        subtitle = QLabel(SUBTITLE_TEXT)
        subtitle.setFont(font("body_sm"))
        set_style_props(subtitle, tone="muted")

        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACING["md"])
        grid.setVerticalSpacing(SPACING["md"])
        grid.setColumnStretch(0, 1)
        self.steppers: dict[str, Stepper] = {}
        for row, (name, label_text, help_text, unit) in enumerate(FIELDS):
            label = QLabel(label_text)
            label.setFont(font("label_md"))
            set_style_props(label, tone="default")
            help_label = QLabel(help_text)
            help_label.setFont(font("body_sm"))
            help_label.setWordWrap(True)
            set_style_props(help_label, tone="muted")
            texts = QVBoxLayout()
            texts.setSpacing(2)
            texts.addWidget(label)
            texts.addWidget(help_label)
            stepper = Stepper(*SETTING_LIMITS[name], unit)
            stepper.spin.valueChanged.connect(self._on_changed)
            self.steppers[name] = stepper
            grid.addLayout(texts, row, 0)
            grid.addWidget(stepper, row, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.form_card = _make_card(title, subtitle)
        self.form_card.layout().addSpacing(SPACING["xs"])
        self.form_card.layout().addLayout(grid)

        self.cycle_label = QLabel()
        self.cycle_label.setFont(font("body_md"))
        self.cycle_label.setWordWrap(True)
        set_style_props(self.cycle_label, tone="variant")
        self.cycle_card = _make_card(_make_caption(CYCLE_CAPTION), self.cycle_label)

        self.notice_label = _make_notice("info")
        self.notice_label.hide()

        body = QWidget()
        body.setObjectName("reportBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"])
        body_layout.setSpacing(SPACING["sm"] + SPACING["xs"])
        for widget in (self.form_card, self.cycle_card, self.notice_label):
            body_layout.addWidget(widget)
        body_layout.addStretch()

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("reportScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.viewport().setAutoFillBackground(False)
        self.scroll_area.setWidget(body)

        self.reset_button = QPushButton(RESET_BUTTON_TEXT)
        set_style_props(self.reset_button, variant="ghost")
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.clicked.connect(lambda: self._fill(DEFAULT_SETTINGS))
        self.save_button = QPushButton(SAVE_BUTTON_TEXT)
        set_style_props(self.save_button, variant="primary")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.current()))
        footer = QFrame()
        footer.setObjectName("reportFooter")
        buttons = QHBoxLayout(footer)
        margin_v = SPACING["sm"] + SPACING["xs"]
        buttons.setContentsMargins(SPACING["md"], margin_v, SPACING["md"], margin_v)
        buttons.setSpacing(SPACING["sm"])
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.scroll_area, stretch=1)
        layout.addWidget(footer)

        self.set_settings(DEFAULT_SETTINGS)

    def current(self) -> PomodoroSettings:
        """지금 화면에 입력된 값."""
        return PomodoroSettings(**{name: s.value() for name, s in self.steppers.items()})

    @property
    def is_dirty(self) -> bool:
        """저장된 값과 다른 입력이 있는지."""
        return self.current() != self._saved

    def set_settings(self, settings: PomodoroSettings) -> None:
        """저장된 설정으로 화면을 채운다 (입력하던 값은 버린다)."""
        self._saved = settings
        self.clear_notice()
        self._fill(settings)

    def show_saved(self, settings: PomodoroSettings) -> None:
        self._saved = settings
        self._fill(settings)
        self.show_notice("info", SAVED_NOTICE)

    def show_notice(self, kind: str, text: str) -> None:
        set_style_props(self.notice_label, notice=kind)
        self.notice_label.setText(text)
        self.notice_label.show()

    def clear_notice(self) -> None:
        self.notice_label.clear()
        self.notice_label.hide()

    def _fill(self, settings: PomodoroSettings) -> None:
        for name, stepper in self.steppers.items():
            stepper.set_value(getattr(settings, name))
        self._on_changed()

    def _on_changed(self) -> None:
        current = self.current()
        self.cycle_label.setText(cycle_text(current))
        self.save_button.setEnabled(current != self._saved)
        self.reset_button.setEnabled(current != DEFAULT_SETTINGS)
        if current != self._saved:
            self.clear_notice()  # "저장했어요"가 새 입력 위에 남지 않게

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.fillRect(self.rect(), qcolor("background"))
        painter.end()
        super().paintEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    load_fonts()
    view = SettingsView()
    view.setStyleSheet(APP_STYLE + STYLE)
    view.save_requested.connect(view.show_saved)
    view.resize(580, 580)
    view.setWindowTitle("FocusPomo")
    view.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
