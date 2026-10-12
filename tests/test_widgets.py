"""gui/widgets.py 공용 아이콘·상태 점 테스트 (화면 없이 offscreen으로 실행)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.style import COLORS  # noqa: E402
from gui.widgets import ICON_KINDS, LineIcon, PingDot, draw_icon, line_icon  # noqa: E402

app = QApplication.instance() or QApplication([])


class IconTest(unittest.TestCase):
    def test_every_kind_draws_something(self):
        for kind in ICON_KINDS:
            image = QImage(32, 32, QImage.Format.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            draw_icon(painter, kind, 32, "primary")
            painter.end()
            drawn = any(image.pixelColor(x, y).alpha() > 0 for x in range(32) for y in range(32))
            self.assertTrue(drawn, kind)

    def test_line_icon_not_null(self):
        self.assertFalse(line_icon("pause", "primary", 17).isNull())

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            LineIcon("no_such_icon", "primary")
        with self.assertRaises(ValueError):
            line_icon("no_such_icon", "primary")


class PingDotTest(unittest.TestCase):
    def test_state(self):
        dot = PingDot(COLORS["secondary"])
        self.assertTrue(dot.is_pinging)
        dot.set_state(COLORS["tertiary"], pinging=False)
        self.assertFalse(dot.is_pinging)
        self.assertFalse(dot.grab().isNull())
        dot.deleteLater()


if __name__ == "__main__":
    unittest.main()
