"""FocusPomo 디자인 시스템 (DESIGN.md "Focus Precision System", 3-06).

모든 화면이 색·글꼴·여백·모서리를 여기서 가져온다. 화면 파일에 hex 색이나 글꼴 이름을 새로 쓰지 않는다.

기준
- 색 값은 DESIGN.md 앞부분 토큰(colors)을 쓴다. 본문의 Tailwind hex(#6366F1, #10B981, #EF4444 등)는
  토큰과 값이 달라서, 역할 배정(무엇을 어느 색으로)과 알파값만 따른다.
    primary(보라)    = 타이머 조작·CV 추적·주요 버튼
    secondary(초록)  = 집중 상태, 업무 창
    tertiary(주황)   = 졸음·경고
    error(분홍빛 빨강) = 자리 이탈·딴짓·카메라 문제
- Qt 스타일시트에는 backdrop-filter(blur)와 box-shadow가 없다. "유리" 표면은 반투명 채우기 + 1px 테두리로만,
  빛무리가 꼭 필요하면 QPainter(QRadialGradient)로 그린다.
- Qt 스타일시트의 border-radius는 위젯 높이의 절반보다 크면 깨지므로 알약 모양은 pill_radius(높이)를 쓴다.
- Qt 스타일시트는 letter-spacing·text-transform을 무시한다. 자간·대문자·고정폭 숫자가 필요한 글자는 font()로 만든
  QFont를 setFont()로 준다.

쓰는 법
- 색: COLORS["secondary"], QPainter용은 qcolor("secondary", 0.12), 스타일시트용은 rgba("secondary", 0.12)
- 글꼴: font("telemetry_metric") (QFont) 또는 스타일시트 안에서 qss_font("body_sm")
- 공통 컴포넌트: MainWindow가 APP_STYLE을 깔고, 위젯은 동적 속성으로 고른다.
      set_style_props(button, variant="primary")          # primary / secondary / destructive / ghost
      set_style_props(frame, surface="card")              # card / overlay
      set_style_props(label, textStyle="telemetry_label", tone="muted")
      set_style_props(label, chip="work")                 # schemas.WINDOW_LABELS
      set_style_props(label, notice="error")              # error / warning / info
  속성을 지정한 위젯만 바뀌므로 아직 디자인을 안 바꾼 화면에는 영향이 없다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QWidget


logger = logging.getLogger(__name__)

# ── 색 (DESIGN.md colors, 이름은 kebab → snake) ─────────────────
COLORS: dict[str, str] = {
    "background": "#0b1326",
    "on_background": "#dae2fd",
    "surface": "#0b1326",
    "surface_dim": "#0b1326",
    "surface_bright": "#31394d",
    "surface_container_lowest": "#060e20",
    "surface_container_low": "#131b2e",
    "surface_container": "#171f33",
    "surface_container_high": "#222a3d",
    "surface_container_highest": "#2d3449",
    "surface_variant": "#2d3449",
    "surface_tint": "#c0c1ff",
    "on_surface": "#dae2fd",
    "on_surface_variant": "#c7c4d7",
    "inverse_surface": "#dae2fd",
    "inverse_on_surface": "#283044",
    "outline": "#908fa0",
    "outline_variant": "#464554",
    "primary": "#c0c1ff",
    "on_primary": "#1000a9",
    "primary_container": "#8083ff",
    "on_primary_container": "#0d0096",
    "inverse_primary": "#494bd6",
    "primary_fixed": "#e1e0ff",
    "primary_fixed_dim": "#c0c1ff",
    "on_primary_fixed": "#07006c",
    "on_primary_fixed_variant": "#2f2ebe",
    "secondary": "#4edea3",
    "on_secondary": "#003824",
    "secondary_container": "#00a572",
    "on_secondary_container": "#00311f",
    "secondary_fixed": "#6ffbbe",
    "secondary_fixed_dim": "#4edea3",
    "on_secondary_fixed": "#002113",
    "on_secondary_fixed_variant": "#005236",
    "tertiary": "#ffb95f",
    "on_tertiary": "#472a00",
    "tertiary_container": "#ca8100",
    "on_tertiary_container": "#3e2400",
    "tertiary_fixed": "#ffddb8",
    "tertiary_fixed_dim": "#ffb95f",
    "on_tertiary_fixed": "#2a1700",
    "on_tertiary_fixed_variant": "#653e00",
    "error": "#ffb4ab",
    "on_error": "#690005",
    "error_container": "#93000a",
    "on_error_container": "#ffdad6",
}


def qcolor(name: str, alpha: float = 1.0) -> QColor:
    """COLORS 이름(또는 #hex)으로 QColor를 만든다. QPainter용."""
    color = QColor(COLORS.get(name, name))
    if not color.isValid():
        raise ValueError(f"알 수 없는 색: {name}")
    color.setAlphaF(min(max(alpha, 0.0), 1.0))
    return color


def rgba(name: str, alpha: float = 1.0) -> str:
    """스타일시트용 "rgba(r, g, b, a)" 문자열."""
    c = qcolor(name, alpha)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alphaF():.2f})"


# ── 상태색 (DESIGN.md "Functional Role Mapping", "Telemetry Intensity Tiers") ──
STATUS_COLORS: dict[str, str] = {
    "focus": "secondary",
    "warning": "tertiary",
    "alert": "error",
    "info": "primary",
    "neutral": "outline",
}
STATUS_FILL_ALPHA = 0.12  # 상태색 바탕 (은은한 주변 신호)
STATUS_BORDER_ALPHA = 0.25  # 상태색 테두리

# 슬롯 태그 → 색 이름 (10초 타임라인·리포트 표). unknown은 판정 불가라 중립색
FINAL_TAG_COLORS: dict[str, str] = {
    "focus": "secondary",
    "drowsy": "tertiary",
    "away": "error",
    "distraction": "error",
    "unknown": "outline",
}
# 창 분류 → 색 이름 (창 태그 칩)
WINDOW_LABEL_COLORS: dict[str, str] = {
    "work": "secondary",
    "distraction": "error",
    "unknown": "outline",
}


# ── 글꼴 (DESIGN.md typography) ─────────────────────────────────
# Inter·JetBrains Mono는 assets/fonts/의 파일(SIL OFL 1.1)을 load_fonts()로 앱에 등록해 쓴다.
# 두 글꼴에 한글이 없어 한글 글자는 맑은 고딕으로, 등록에 실패하면 Windows 기본 글꼴로 대신한다.
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_loaded_families: set[str] | None = None


def load_fonts(font_dir: Path | None = None) -> set[str]:
    """assets/fonts/*.ttf를 앱 글꼴로 등록하고 등록된 글꼴 이름을 돌려준다.

    QApplication을 만든 뒤, 화면을 만들기 전에 한 번 부른다. 다시 불러도 처음 결과를 돌려준다.
    파일이 없거나 등록에 실패하면 경고만 남기고 대체 글꼴로 계속한다.
    """
    global _loaded_families
    if _loaded_families is not None and font_dir is None:
        return _loaded_families
    families: set[str] = set()
    paths = sorted((font_dir or FONT_DIR).glob("*.ttf"))
    if not paths:
        logger.warning("글꼴 파일이 없어 기본 글꼴로 표시합니다: %s", font_dir or FONT_DIR)
    for path in paths:
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            logger.warning("글꼴을 등록하지 못했습니다: %s", path.name)
            continue
        families.update(QFontDatabase.applicationFontFamilies(font_id))
    if font_dir is None:
        _loaded_families = families
    return families


SANS_FAMILIES: tuple[str, ...] = ("Inter", "Segoe UI", "Malgun Gothic")
MONO_FAMILIES: tuple[str, ...] = ("JetBrains Mono", "Consolas", "Malgun Gothic")
SANS = ", ".join(f'"{f}"' for f in SANS_FAMILIES)  # 스타일시트 font-family 값
MONO = ", ".join(f'"{f}"' for f in MONO_FAMILIES)


@dataclass(frozen=True)
class TextStyle:
    families: tuple[str, ...]
    size: int  # px
    weight: int  # 400 / 500 / 600 / 700
    line_height: int  # px (QLabel은 줄 간격을 직접 못 정해 참고값)
    letter_spacing: float = 0.0  # em
    uppercase: bool = False  # 12px 미만 라벨은 대문자 + 넓은 자간 (한글에는 영향 없음)
    tabular: bool = False  # 숫자 폭 고정 (카운트다운이 좌우로 흔들리지 않게)


TYPOGRAPHY: dict[str, TextStyle] = {
    "display_timer": TextStyle(SANS_FAMILIES, 64, 600, 72, -0.04, tabular=True),
    "headline_lg": TextStyle(SANS_FAMILIES, 30, 600, 38, -0.02),
    "headline_md": TextStyle(SANS_FAMILIES, 22, 600, 28, -0.015),
    "headline_sm": TextStyle(SANS_FAMILIES, 18, 500, 24, -0.01),
    "body_lg": TextStyle(SANS_FAMILIES, 16, 400, 24),
    "body_md": TextStyle(SANS_FAMILIES, 14, 400, 20),
    "body_sm": TextStyle(SANS_FAMILIES, 12, 400, 16, 0.01),
    "label_md": TextStyle(SANS_FAMILIES, 13, 500, 18, 0.01),
    "label_sm": TextStyle(SANS_FAMILIES, 11, 600, 14, 0.04, uppercase=True),
    "telemetry_metric": TextStyle(MONO_FAMILIES, 18, 600, 22, -0.02, tabular=True),
    "telemetry_label": TextStyle(MONO_FAMILIES, 11, 500, 14, 0.05, uppercase=True),
}


def font(name: str, size: int | None = None) -> QFont:
    """TYPOGRAPHY 이름으로 QFont를 만든다. size를 주면 크기만 바꾼다 (창 크기에 맞춰 키우는 타이머 등)."""
    style = TYPOGRAPHY[name]
    px = style.size if size is None else size
    f = QFont()
    f.setFamilies(list(style.families))
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(style.weight))
    if style.letter_spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, style.letter_spacing * px)
    if style.uppercase:
        f.setCapitalization(QFont.Capitalization.AllUppercase)
    if style.tabular:
        f.setFeature(QFont.Tag("tnum"), 1)
    return f


def qss_font(name: str) -> str:
    """스타일시트용 글꼴 선언 (family·size·weight만. 자간·대문자는 font()로)."""
    style = TYPOGRAPHY[name]
    family = SANS if style.families == SANS_FAMILIES else MONO
    return f"font-family: {family}; font-size: {style.size}px; font-weight: {style.weight};"


# ── 모서리·여백 (DESIGN.md rounded, spacing, Shapes) ─────────────
RADIUS: dict[str, int] = {"sm": 2, "default": 4, "md": 6, "lg": 8, "xl": 12}  # px (1rem = 16px)
RADIUS_BADGE = RADIUS["sm"]  # 작은 표시·배지
RADIUS_CONTROL = RADIUS["default"]  # 버튼·입력·칩 (본문 "Standard Controls 4px")
RADIUS_CARD = RADIUS["lg"]  # 카드·카메라 미리보기·타임라인 틀
RADIUS_WINDOW = RADIUS["xl"]  # 창·떠 있는 대화상자


def pill_radius(height: int) -> int:
    """알약 모양(rounded full) 반지름. Qt는 높이의 절반을 넘으면 모서리가 깨진다."""
    return max(height // 2, 0)


SPACING: dict[str, int] = {  # px, 4px 격자
    "xs": 4,
    "sm": 8,
    "md": 16,
    "lg": 24,
    "xl": 32,
    "gutter": 16,
    "gutter_compact": 8,
    "margin": 24,
    "margin_window": 12,
}

# ── 10초 슬롯 타임라인 (DESIGN.md "10-Second Quantum Timeline") ──
TIMELINE_SLOT_WIDTH = 4
TIMELINE_SLOT_HEIGHT = 16
TIMELINE_SLOT_GAP = 2
TIMELINE_SLOT_RADIUS = 1
TIMELINE_EMPTY_COLOR = "surface_container_highest"  # 아직 안 온 슬롯
TIMELINE_EMPTY_ALPHA = 0.4

# ── 공통 컴포넌트 스타일시트 ──────────────────────────────────────
CHIP_HEIGHT = 22


def _text_style_rules() -> str:
    return "\n".join(f'QLabel[textStyle="{name}"] {{ {qss_font(name)} }}' for name in TYPOGRAPHY)


def _tone_rules() -> str:
    tones = {"default": "on_surface", "variant": "on_surface_variant", "muted": "outline",
             **STATUS_COLORS}
    return "\n".join(f'QLabel[tone="{tone}"] {{ color: {COLORS[color]}; }}' for tone, color in tones.items())


def _chip_rules() -> str:
    rules = []
    for label, color in WINDOW_LABEL_COLORS.items():
        rules.append(
            f'QLabel[chip="{label}"] {{ background: {rgba(color, 0.1)}; '
            f"border: 1px solid {rgba(color, STATUS_BORDER_ALPHA)}; color: {COLORS[color]}; "
            f"border-radius: {RADIUS_CONTROL}px; padding: 0 8px; "
            f"min-height: {CHIP_HEIGHT - 2}px; max-height: {CHIP_HEIGHT - 2}px; {qss_font('label_sm')} }}"
        )
    return "\n".join(rules)


def _notice_rules() -> str:
    notices = {"error": "error", "warning": "tertiary", "info": "primary"}
    return "\n".join(
        f'QLabel[notice="{name}"] {{ background: {rgba(color, STATUS_FILL_ALPHA)}; '
        f"border: 1px solid {rgba(color, 0.3)}; border-radius: {RADIUS['md']}px; "
        f"color: {COLORS[color]}; padding: 8px 12px; {qss_font('body_sm')} }}"
        for name, color in notices.items()
    )


APP_STYLE = f"""
/* 버튼 (DESIGN.md Buttons & Action Triggers) */
QPushButton[variant="primary"] {{
    background: {COLORS["primary_container"]};
    border: none;
    border-top: 1px solid {rgba("primary_fixed", 0.35)};
    border-radius: {RADIUS_CONTROL}px;
    color: {COLORS["on_primary_container"]};
    {qss_font("label_md")}
    font-weight: 600;
    padding: 10px 20px;
}}
QPushButton[variant="primary"]:hover {{ background: {COLORS["primary"]}; }}
QPushButton[variant="primary"]:pressed {{ background: {COLORS["primary_fixed"]}; }}
QPushButton[variant="secondary"] {{
    background: {rgba("surface_container", 0.8)};
    border: 1px solid {COLORS["outline_variant"]};
    border-radius: {RADIUS_CONTROL}px;
    color: {COLORS["on_surface"]};
    {qss_font("label_md")}
    padding: 10px 20px;
}}
QPushButton[variant="secondary"]:hover {{
    background: {rgba("surface_container_highest", 0.5)};
    border-color: {rgba("primary", 0.5)};
}}
QPushButton[variant="secondary"]:pressed {{ background: {COLORS["surface_container_highest"]}; }}
QPushButton[variant="destructive"] {{
    background: {rgba("error", 0.1)};
    border: 1px solid {rgba("error", 0.3)};
    border-radius: {RADIUS_CONTROL}px;
    color: {COLORS["error"]};
    {qss_font("label_md")}
    padding: 10px 20px;
}}
QPushButton[variant="destructive"]:hover {{ background: {rgba("error", 0.2)}; }}
QPushButton[variant="destructive"]:pressed {{ background: {rgba("error_container", 0.5)}; }}
QPushButton[variant="ghost"] {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_CONTROL}px;
    color: {COLORS["outline"]};
    {qss_font("body_sm")}
    padding: 6px 16px;
}}
QPushButton[variant="ghost"]:hover {{ background: {rgba("surface_container_high", 0.4)}; color: {COLORS["on_surface"]}; }}
QPushButton[variant="ghost"]:pressed {{ background: {rgba("surface_container_high", 0.7)}; }}
QPushButton[variant="primary"]:disabled,
QPushButton[variant="secondary"]:disabled,
QPushButton[variant="destructive"]:disabled {{
    background: {COLORS["surface_container_low"]};
    border: 1px solid {rgba("outline_variant", 0.4)};
    color: {COLORS["outline_variant"]};
}}

/* 표면 (DESIGN.md Elevation & Depth: blur 없이 반투명 + 1px 테두리) */
QFrame[surface="card"] {{
    background: {rgba("surface_container", 0.7)};
    border: 1px solid {rgba("outline_variant", 0.6)};
    border-radius: {RADIUS_CARD}px;
}}
QFrame[surface="overlay"] {{
    background: {rgba("surface_container", 0.95)};
    border: 1px solid {rgba("outline_variant", 0.6)};
    border-radius: {RADIUS_WINDOW}px;
}}

/* 글자 크기·색 */
{_text_style_rules()}
{_tone_rules()}

/* 창 태그 칩 (DESIGN.md Chips & Monitored Application Tags) */
{_chip_rules()}

/* 안내 상자 */
{_notice_rules()}
"""


def set_style_props(widget: QWidget, **props: str) -> None:
    """APP_STYLE의 동적 속성을 정한다. 이미 보이는 위젯이어도 바로 다시 그려지게 다시 polish한다."""
    for key, value in props.items():
        widget.setProperty(key, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
