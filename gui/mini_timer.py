"""미니 모드: 항상 위에 떠 있는 작은 타이머 캡슐 (3-06, 디자인 "Micro Floating Capsule HUD").

타이머 화면(TimerView)의 "미니 모드" 버튼을 누르면 MainWindow가 메인 창을 숨기고 이 창을 띄운다.
타이머 계산은 계속 TimerView가 하고, 이 창은 TimerView.refreshed를 받아 남은 시간·진행 막대·정지 상태만 그린다.
그래서 미니 모드에서 일시 정지해도 정지 구간(pause_intervals)이 그대로 기록된다.

- 왼쪽 점 손잡이(또는 빈 곳)를 끌면 창이 움직인다.
- 일시 정지 / 다시 시작 버튼, Space 키 → TimerView.toggle_pause()
- 전체 창 버튼, 캡슐 더블클릭, Alt+M, 창 닫기(Alt+F4) → restore_requested (MainWindow가 메인 창으로 돌아간다)
- 고정 핀 버튼: 항상 위에 두기 켜기/끄기 (기본 켜짐)
- 세션이 끝나면(타이머 종료·결과 정리 중) MainWindow가 메인 창으로 돌려 보낸다.

HTML의 주변(가짜 데스크톱, 안내 문구, EAR 태그, 상태 막대)은 시연용 배경이라 넣지 않았다.
단축키는 앱 창에 포커스가 있을 때만 동작한다 (전역 단축키는 OS 후킹이 필요해서 뺐다).

    python -m gui.mini_timer     # 미니 모드 창만 띄워 보기 (60배속)
"""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gui.style import COLORS, font, load_fonts, qcolor, rgba
from gui.timer_view import TimerView, format_mmss
from gui.widgets import line_icon

CAPSULE_SIZE = (296, 68)  # HTML 캡슐 크기
SHADOW_MARGIN = 14  # 캡슐 바깥 그림자·빛무리를 그릴 여백 (창은 이만큼 더 크다)
CAPSULE_RADIUS = 12
SCREEN_MARGIN = 24  # 처음 띄울 때 화면 오른쪽 위에서 떨어지는 거리
PROGRESS_HEIGHT = 3.5
TOGGLE_SHORTCUT = "Alt+M"  # 메인 창 ↔ 미니 모드

PAUSE_TIP = "일시 정지 (Space)"
RESUME_TIP = "다시 시작 (Space)"
RESTORE_TIP = "전체 창으로 돌아가기 (더블클릭, Alt+M)"
PIN_ON_TIP = "항상 위에 두기: 켜짐"
PIN_OFF_TIP = "항상 위에 두기: 꺼짐"
GRIP_TIP = "끌어서 창 위치 이동"

STYLE = f"""
#miniTime {{ color: {COLORS["on_surface"]}; }}
#miniTime[paused="true"] {{ color: {rgba("on_surface", 0.5)}; }}
#miniToggle, #miniRestore, #miniPin {{
    border-radius: 8px;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
    padding: 0;
}}
#miniToggle {{
    background: {rgba("surface_container_highest", 0.6)};
    border: 1px solid {rgba("outline_variant", 0.3)};
}}
#miniToggle:hover {{ background: {COLORS["primary"]}; border-color: {COLORS["primary"]}; }}
#miniRestore {{ background: {rgba("surface_container_highest", 0.3)}; border: 1px solid transparent; }}
#miniRestore:hover {{ background: {COLORS["surface_container_highest"]}; border-color: {rgba("outline_variant", 0.4)}; }}
#miniPin {{ background: transparent; border: 1px solid {rgba("outline_variant", 0.3)}; }}
#miniPin:hover {{ background: {rgba("surface_container_highest", 0.5)}; }}
#miniPin:checked {{ background: {rgba("primary", 0.1)}; border-color: {rgba("primary", 0.2)}; }}
#miniPin:checked:hover {{ background: {rgba("primary", 0.2)}; }}
#miniToggle:disabled {{ background: {COLORS["surface_container_low"]}; border-color: {rgba("outline_variant", 0.2)}; }}
"""


class Grip(QWidget):
    """2×3 점 손잡이. 누르고 끌면 창을 옮긴다 (창 전체 끌기는 MiniTimerWindow가 처리)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 20)
        self.setToolTip(GRIP_TIP)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._hover = False

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._hover = False
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("on_surface", 0.8) if self._hover else qcolor("outline_variant", 0.9))
        for row in range(3):
            for col in range(2):
                painter.drawEllipse(QPointF(4 + col * 5, 5 + row * 5), 1.5, 1.5)


class MiniProgress(QWidget):
    """캡슐 아래 얇은 진행 막대. 경과 비율만큼 초록 → 보라, 끝에 밝은 머리."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(4)
        self._progress = 0.0

    @property
    def progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = min(max(value, 0.0), 1.0)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        h = PROGRESS_HEIGHT
        top = (self.height() - h) / 2
        track = QRectF(0, top, self.width(), h)
        painter.setBrush(qcolor("surface_container_highest", 0.6))
        painter.drawRoundedRect(track, h / 2, h / 2)
        filled = self.width() * self._progress
        if filled <= 0:
            return
        gradient = QLinearGradient(QPointF(0, 0), QPointF(filled, 0))  # 채워진 부분 안에서 초록 → 보라
        gradient.setColorAt(0.0, qcolor("secondary"))
        gradient.setColorAt(1.0, qcolor("primary"))
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(0, top, filled, h), h / 2, h / 2)
        head = min(8.0, filled)
        painter.setBrush(qcolor("#ffffff", 0.6))
        painter.drawRoundedRect(QRectF(filled - head, top, head, h), h / 2, h / 2)


class MiniTimerWindow(QWidget):
    """항상 위에 떠 있는 테두리 없는 캡슐 창. TimerView의 상태를 따라 그린다."""

    restore_requested = Signal()

    def __init__(self, timer_view: TimerView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._timer_view = timer_view
        self._hover = False
        self._press_pos: QPoint | None = None
        self._drag_offset: QPoint | None = None
        self._placed = False  # 처음 띄울 때만 화면 오른쪽 위로 옮긴다 (그 뒤엔 사용자가 둔 자리)

        self.setWindowTitle("FocusPomo 미니 모드")
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(CAPSULE_SIZE[0] + 2 * SHADOW_MARGIN, CAPSULE_SIZE[1] + 2 * SHADOW_MARGIN)
        self.setStyleSheet(STYLE)

        self.grip = Grip()

        self.time_label = QLabel(format_mmss(timer_view.remaining))
        self.time_label.setObjectName("miniTime")
        time_font = font("telemetry_metric", 23)
        time_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.05 * 23)  # HTML tracking-tighter
        self.time_label.setFont(time_font)

        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName("miniToggle")
        self.toggle_button.setIconSize(QSize(16, 16))
        self.toggle_button.clicked.connect(timer_view.toggle_pause)
        self.toggle_button.installEventFilter(self)  # 마우스를 올리면 바탕이 연보라라 아이콘을 진하게 바꾼다

        self.restore_button = QPushButton()
        self.restore_button.setObjectName("miniRestore")
        self.restore_button.setIcon(line_icon("expand", "on_surface_variant", 15))
        self.restore_button.setIconSize(QSize(15, 15))
        self.restore_button.setToolTip(RESTORE_TIP)
        self.restore_button.clicked.connect(self.restore_requested)

        self.pin_button = QPushButton()
        self.pin_button.setObjectName("miniPin")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(True)
        self.pin_button.setIconSize(QSize(14, 14))
        self.pin_button.toggled.connect(self.set_always_on_top)

        for button in (self.toggle_button, self.restore_button, self.pin_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space가 버튼 클릭이 아니라 정지 단축키로 가게

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.grip, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(6)
        row.addWidget(self.time_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addStretch()
        row.addWidget(self.toggle_button)
        row.addWidget(self.restore_button)
        row.addWidget(self.pin_button)

        self.progress_bar = MiniProgress()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SHADOW_MARGIN + 12, SHADOW_MARGIN + 9, SHADOW_MARGIN + 10, SHADOW_MARGIN + 6)
        layout.setSpacing(4)
        layout.addLayout(row, 1)
        layout.addWidget(self.progress_bar)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._toggle_pause_if_running)
        QShortcut(QKeySequence(TOGGLE_SHORTCUT), self, activated=self.restore_requested)

        timer_view.refreshed.connect(self.sync)
        self._update_pin_look()
        self.sync()

    # ── 상태 ────────────────────────────────────────────────────
    @property
    def always_on_top(self) -> bool:
        return self.pin_button.isChecked()

    def sync(self) -> None:
        """TimerView의 남은 시간·진행·정지 상태를 따라 그린다."""
        view = self._timer_view
        self.time_label.setText(format_mmss(view.remaining))
        self.progress_bar.set_progress(view.ring.progress)
        paused = view.is_paused
        if self.time_label.property("paused") != paused:
            self.time_label.setProperty("paused", paused)
            self.time_label.style().unpolish(self.time_label)
            self.time_label.style().polish(self.time_label)
        self._update_toggle_icon()
        self.toggle_button.setToolTip(RESUME_TIP if paused else PAUSE_TIP)
        self.toggle_button.setEnabled(not view.is_finishing)

    def _update_toggle_icon(self) -> None:
        hovered = self.toggle_button.underMouse() and self.toggle_button.isEnabled()
        kind = "play" if self._timer_view.is_paused else "pause"
        self.toggle_button.setIcon(line_icon(kind, "on_primary" if hovered else "on_surface", 16))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt 이름)
        if watched is self.toggle_button and event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self._update_toggle_icon()
        return super().eventFilter(watched, event)

    def set_always_on_top(self, on: bool) -> None:
        if self.pin_button.isChecked() != on:
            self.pin_button.setChecked(on)  # toggled로 다시 들어온다
            return
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if visible:
            self.show()  # 창 플래그를 바꾸면 창이 숨겨지므로 다시 띄운다
        self._update_pin_look()

    def _update_pin_look(self) -> None:
        on = self.pin_button.isChecked()
        self.pin_button.setIcon(line_icon("pin", "primary" if on else "on_surface_variant", 14))
        self.pin_button.setToolTip(PIN_ON_TIP if on else PIN_OFF_TIP)

    def _toggle_pause_if_running(self) -> None:
        if not self._timer_view.is_finishing:
            self._timer_view.toggle_pause()

    def place_near(self, anchor: QWidget | None) -> None:
        """처음 한 번만, anchor가 있는 화면의 오른쪽 위에 둔다."""
        if self._placed:
            return
        # MainWindow.screen은 화면 종류(Screen) 속성이라 QWidget.screen()을 못 쓴다 → 창 가운데 위치로 모니터를 찾는다
        screen = QApplication.screenAt(anchor.frameGeometry().center()) if anchor is not None else None
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - SCREEN_MARGIN + SHADOW_MARGIN, area.top() + SCREEN_MARGIN - SHADOW_MARGIN)
        self._placed = True

    # ── 마우스: 끌어서 옮기기, 더블클릭으로 복귀 ─────────────────
    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        # 실제로 끌었을 때만 옮긴다 (그냥 클릭·더블클릭은 이동으로 보지 않음)
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            pos = event.globalPosition().toPoint()
            if self._drag_offset is None:
                if (pos - self._press_pos).manhattanLength() < QApplication.startDragDistance():
                    return
                handle = self.windowHandle()
                if handle is not None and handle.startSystemMove():  # OS가 창을 옮긴다 (여러 모니터·스냅 지원)
                    self._press_pos = None
                    return
                self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self.move(pos - self._drag_offset)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._press_pos = None
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        if event.button() == Qt.MouseButton.LeftButton:
            self.restore_requested.emit()
            return
        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        self._hover = False
        self.update()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        # Alt+F4 등으로 닫아도 세션은 계속 돌고 있으니 메인 창으로 돌아간다
        event.ignore()
        self.restore_requested.emit()

    # ── 그리기 ──────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 이름)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = SHADOW_MARGIN
        capsule = QRectF(m, m, CAPSULE_SIZE[0], CAPSULE_SIZE[1])

        # 그림자(아래로 진하게) + 보랏빛 빛무리: 바깥으로 갈수록 옅은 둥근 사각형을 겹친다
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(m, 0, -2):
            t = 1 - i / m
            painter.setBrush(qcolor("#000000", 0.05 * t))
            painter.drawRoundedRect(capsule.adjusted(-i, -i + 4, i, i + 4), CAPSULE_RADIUS + i, CAPSULE_RADIUS + i)
            painter.setBrush(qcolor("primary_container", 0.03 * t))
            painter.drawRoundedRect(capsule.adjusted(-i, -i, i, i), CAPSULE_RADIUS + i, CAPSULE_RADIUS + i)

        # 캡슐 본체 + 테두리 (마우스를 올리면 보라)
        border = qcolor("primary", 0.5) if self._hover else qcolor("outline_variant", 0.4)
        painter.setPen(QPen(border, 1))
        painter.setBrush(qcolor("surface_container_low", 0.95))
        painter.drawRoundedRect(capsule.adjusted(0.5, 0.5, -0.5, -0.5), CAPSULE_RADIUS, CAPSULE_RADIUS)

        # 위쪽 가장자리 하이라이트 (가운데만 밝은 1px 선)
        shine = QLinearGradient(capsule.topLeft(), capsule.topRight())
        shine.setColorAt(0.0, qcolor("#ffffff", 0.0))
        shine.setColorAt(0.5, qcolor("#ffffff", 0.2))
        shine.setColorAt(1.0, qcolor("#ffffff", 0.0))
        clip = QPainterPath()
        clip.addRoundedRect(capsule, CAPSULE_RADIUS, CAPSULE_RADIUS)
        painter.setClipPath(clip)
        painter.setPen(QPen(shine, 1))
        painter.drawLine(QPointF(capsule.left(), capsule.top() + 1), QPointF(capsule.right(), capsule.top() + 1))


def main() -> int:
    app = QApplication(sys.argv)
    load_fonts()
    start = time.time()
    view = TimerView(clock=lambda: start + (time.time() - start) * 60)  # 60배속
    mini = MiniTimerWindow(view)
    mini.restore_requested.connect(app.quit)
    view.start(start)
    mini.place_near(None)
    mini.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
