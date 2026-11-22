"""위쪽 내비게이션 바: 타이머(대기) · 기록 · 설정 화면을 오간다.

MainWindow가 화면 위에 붙이고, 세션 흐름 밖의 화면(대기·기록·설정)에서만 보여 준다.
캘리브레이션·타이머·리포트(휴식) 중에는 숨겨 세션 도중 다른 화면으로 가지 않게 한다.
탭을 누르면 navigate(탭 키)를 보내고, 실제 전환과 선택 표시는 MainWindow가 set_current()로 맞춘다.

    python -m gui.nav_bar     # 바만 띄워 보기
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QButtonGroup, QFrame, QHBoxLayout, QPushButton, QWidget

from gui.style import COLORS, SANS, load_fonts, rgba

TAB_TIMER = "timer"
TAB_HISTORY = "history"
TAB_SETTINGS = "settings"
TABS: tuple[tuple[str, str], ...] = (
    (TAB_TIMER, "타이머"),
    (TAB_HISTORY, "기록"),
    (TAB_SETTINGS, "설정"),
)
NAV_HEIGHT = 40

STYLE = f"""
#navBar {{
    background: {COLORS["surface_container_lowest"]};
    border-bottom: 1px solid {rgba("outline_variant", 0.35)};
}}
#navTab {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {COLORS["outline"]};
    font-family: {SANS};
    font-size: 13px;
    font-weight: 600;
    padding: 0 14px;
}}
#navTab:hover {{ color: {COLORS["on_surface"]}; }}
#navTab:checked {{ color: {COLORS["on_surface"]}; border-bottom-color: {COLORS["primary"]}; }}
"""


class NavBar(QFrame):
    navigate = Signal(str)  # TAB_* 키

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navBar")
        self.setStyleSheet(STYLE)
        self.setFixedHeight(NAV_HEIGHT)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(2)
        for key, text in TABS:
            button = QPushButton(text)
            button.setObjectName("navTab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setFixedHeight(NAV_HEIGHT)
            button.clicked.connect(lambda _checked=False, k=key: self.navigate.emit(k))
            self._group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button)
        layout.addStretch()
        self.set_current(TAB_TIMER)

    @property
    def current(self) -> str | None:
        for key, button in self.buttons.items():
            if button.isChecked():
                return key
        return None

    def set_current(self, key: str) -> None:
        """선택 표시만 바꾼다 (navigate를 보내지 않는다)."""
        self.buttons[key].setChecked(True)


def main() -> int:
    app = QApplication(sys.argv)
    load_fonts()
    bar = NavBar()
    bar.navigate.connect(bar.set_current)
    bar.resize(580, NAV_HEIGHT)
    bar.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
