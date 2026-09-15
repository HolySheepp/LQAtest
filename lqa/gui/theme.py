"""主題與樣式。

色票沿用 AudioForge 的設計語彙：深淺兩套 token，副色可換，
每個副色在深淺主題各有一個變體（深色主題用高明度版、淺色主題用低明度版），
確保任一副色與整體一致。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface_2: str
    text: str
    text_dim: str
    border: str
    danger: str
    success: str
    warning: str
    accent: str


DARK = Palette(
    bg="#16181d", surface="#1e2128", surface_2="#262a33",
    text="#e8eaf0", text_dim="#9aa0ad", border="#333845",
    danger="#ff5c5c", success="#3ecf8e", warning="#ffb648", accent="#4f8cff",
)

LIGHT = Palette(
    bg="#f4f5f8", surface="#ffffff", surface_2="#eceef3",
    text="#23262e", text_dim="#6a7080", border="#d8dbe4",
    danger="#d94040", success="#199a64", warning="#cf8a1d", accent="#2f6fe0",
)

# 副色：(深色主題用, 淺色主題用)
ACCENTS: dict[str, tuple[str, str]] = {
    "blue": ("#4f8cff", "#2f6fe0"),
    "green": ("#42d65a", "#42d65a"),
    "purple": ("#ac86f9", "#7342d7"),
    "teal": ("#30c9e8", "#1395ae"),
    "amber": ("#faad42", "#c67a10"),
    "rose": ("#f76495", "#d92662"),
}

ACCENT_LABELS = {
    "blue": "藍", "green": "綠", "purple": "紫",
    "teal": "青", "amber": "琥珀", "rose": "玫瑰",
}


CUSTOM = "custom"


def palette_for(dark: bool, accent: str, custom: str = "") -> Palette:
    base = DARK if dark else LIGHT
    if accent == CUSTOM and custom:
        chosen = custom
    else:
        pair = ACCENTS.get(accent, ACCENTS["blue"])
        chosen = pair[0] if dark else pair[1]
    return Palette(**{**base.__dict__, "accent": chosen})


def swatch(accent: str, dark: bool, custom: str = "") -> str:
    """副色圓圈要顯示的顏色。"""
    if accent == CUSTOM:
        return custom or "#4f8cff"
    pair = ACCENTS.get(accent, ACCENTS["blue"])
    return pair[0] if dark else pair[1]


def mix(color: str, other: str, ratio: float) -> str:
    """把兩個 #rrggbb 依比例混合。ratio 是 color 佔的比重。"""
    def parts(value: str) -> tuple[int, int, int]:
        v = value.lstrip("#")
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)

    a, b = parts(color), parts(other)
    blended = tuple(round(x * ratio + y * (1 - ratio)) for x, y in zip(a, b))
    return "#{:02x}{:02x}{:02x}".format(*blended)


def build_qss(p: Palette) -> str:
    accent_soft = mix(p.accent, p.surface, 0.18)
    accent_hover = mix(p.accent, p.text, 0.85)
    return f"""
QWidget {{
    background: {p.bg};
    color: {p.text};
    font-size: 13px;
}}
QLabel[role="title"] {{ font-size: 19px; font-weight: 600; }}
QLabel[role="section"] {{ font-size: 12px; color: {p.text_dim}; font-weight: 600; }}
QLabel[role="dim"] {{ color: {p.text_dim}; }}
QLabel[role="hint"] {{ color: {p.text_dim}; font-size: 12px; }}

QFrame[role="card"] {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
}}

QPushButton {{
    background: {p.surface_2};
    border: 1px solid {p.border};
    border-radius: 7px;
    padding: 7px 14px;
    color: {p.text};
}}
QPushButton:hover {{ border-color: {p.accent}; }}
QPushButton:disabled {{ color: {p.text_dim}; border-color: {p.border}; }}
QPushButton[role="primary"] {{
    background: {p.accent};
    border-color: {p.accent};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{ background: {accent_hover}; }}
QPushButton[role="primary"]:disabled {{
    background: {mix(p.accent, p.surface, 0.35)};
    border-color: transparent;
    color: {p.surface};
}}
QPushButton[role="danger"] {{ border-color: {p.danger}; color: {p.danger}; }}
QPushButton[role="chip"] {{ padding: 4px 10px; border-radius: 12px; }}
QPushButton[role="chip"]:checked {{
    background: {accent_soft};
    border-color: {p.accent};
    color: {p.accent};
}}

QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit, QTextEdit {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    outline: none;
}}
QListWidget::item, QTreeWidget::item {{ padding: 5px 8px; border-radius: 5px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {accent_soft};
    color: {p.text};
}}
QHeaderView::section {{
    background: {p.surface_2};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 6px 8px;
    color: {p.text_dim};
}}

QLineEdit, QComboBox, QSpinBox {{
    background: {p.surface_2};
    border: 1px solid {p.border};
    border-radius: 7px;
    padding: 6px 9px;
    selection-background-color: {p.accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p.accent}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    selection-background-color: {accent_soft};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {p.border};
    border-radius: 4px;
    background: {p.surface_2};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
}}

QProgressBar {{
    background: {p.surface_2};
    border: none;
    border-radius: 4px;
    height: 7px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}

QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {p.text_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; min-width: 28px; }}

QSplitter::handle {{ background: {p.border}; }}
QToolTip {{
    background: {p.surface_2};
    color: {p.text};
    border: 1px solid {p.border};
    padding: 4px 7px;
}}
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 8px; }}
QTabBar::tab {{
    background: transparent;
    padding: 7px 14px;
    color: {p.text_dim};
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {p.text}; border-bottom-color: {p.accent}; }}
"""
