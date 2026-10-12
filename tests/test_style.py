"""gui/style.py 디자인 시스템 테스트 (화면 없이 offscreen으로 실행)."""
import os
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from gui import style  # noqa: E402
from schemas import FINAL_TAGS, WINDOW_LABELS  # noqa: E402

app = QApplication.instance() or QApplication([])


class ColorTest(unittest.TestCase):
    def test_all_tokens_are_hex(self):
        for name, value in style.COLORS.items():
            self.assertRegex(value, r"^#[0-9a-f]{6}$", name)

    def test_role_maps_use_known_colors(self):
        for mapping in (style.STATUS_COLORS, style.FINAL_TAG_COLORS, style.WINDOW_LABEL_COLORS):
            for color in mapping.values():
                self.assertIn(color, style.COLORS)
        self.assertIn(style.TIMELINE_EMPTY_COLOR, style.COLORS)

    def test_tag_maps_cover_schema_enums(self):
        self.assertEqual(set(style.FINAL_TAG_COLORS), set(FINAL_TAGS))
        self.assertEqual(set(style.WINDOW_LABEL_COLORS), set(WINDOW_LABELS))

    def test_qcolor_and_rgba(self):
        c = style.qcolor("secondary", 0.12)
        self.assertEqual(c.name(), style.COLORS["secondary"])
        self.assertAlmostEqual(c.alphaF(), 0.12, places=2)
        self.assertEqual(style.rgba("secondary", 0.12), "rgba(78, 222, 163, 0.12)")
        self.assertEqual(style.qcolor("#ffffff", 2.0).alphaF(), 1.0)  # hex도 받고 알파는 0~1로 자른다

    def test_unknown_color_raises(self):
        with self.assertRaises(ValueError):
            style.qcolor("no_such_color")


class FontTest(unittest.TestCase):
    def test_font_matches_typography(self):
        for name, spec in style.TYPOGRAPHY.items():
            f = style.font(name)
            self.assertEqual(f.pixelSize(), spec.size, name)
            self.assertEqual(f.weight(), QFont.Weight(spec.weight), name)
            self.assertEqual(f.families()[0], spec.families[0], name)

    def test_size_override_and_letter_spacing(self):
        f = style.font("display_timer", size=100)
        self.assertEqual(f.pixelSize(), 100)
        self.assertAlmostEqual(f.letterSpacing(), -4.0, places=1)  # -0.04em

    def test_small_labels_uppercase(self):
        self.assertEqual(style.font("telemetry_label").capitalization(), QFont.Capitalization.AllUppercase)
        self.assertEqual(style.font("body_md").capitalization(), QFont.Capitalization.MixedCase)

    def test_qss_font(self):
        self.assertIn("JetBrains Mono", style.qss_font("telemetry_metric"))
        self.assertIn("font-size: 12px", style.qss_font("body_sm"))


class ShapeTest(unittest.TestCase):
    def test_spacing_on_4px_grid(self):
        for name, value in style.SPACING.items():
            self.assertEqual(value % 4, 0, name)

    def test_pill_radius(self):
        self.assertEqual(style.pill_radius(22), 11)
        self.assertEqual(style.pill_radius(-1), 0)


class AppStyleTest(unittest.TestCase):
    def test_braces_balanced(self):
        self.assertEqual(style.APP_STYLE.count("{"), style.APP_STYLE.count("}"))

    def test_has_rules_for_every_option(self):
        for variant in ("primary", "secondary", "destructive"):
            self.assertIn(f'QPushButton[variant="{variant}"]', style.APP_STYLE)
        for name in style.TYPOGRAPHY:
            self.assertIn(f'QLabel[textStyle="{name}"]', style.APP_STYLE)
        for label in WINDOW_LABELS:
            self.assertIn(f'QLabel[chip="{label}"]', style.APP_STYLE)

    def test_no_raw_hex_outside_tokens(self):
        hexes = set(re.findall(r"#[0-9a-fA-F]{6}", style.APP_STYLE))
        self.assertTrue(hexes <= set(style.COLORS.values()), hexes - set(style.COLORS.values()))

    def test_set_style_props_applies(self):
        button = QPushButton("시작")
        button.setStyleSheet(style.APP_STYLE)
        button.ensurePolished()
        before = button.palette().buttonText().color().name()
        style.set_style_props(button, variant="primary")
        self.assertEqual(button.property("variant"), "primary")
        self.assertEqual(button.palette().buttonText().color().name(), style.COLORS["on_primary_container"])
        self.assertNotEqual(before, style.COLORS["on_primary_container"])
        label = QLabel("EAR")
        style.set_style_props(label, textStyle="telemetry_label", tone="muted")
        self.assertEqual(label.property("tone"), "muted")
        button.deleteLater()
        label.deleteLater()


if __name__ == "__main__":
    unittest.main()
