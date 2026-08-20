"""Styling for the semantic roles the widgets set via ``setProperty("role", …)``.

Colours are taken from the active palette rather than hard-coded, so the app
follows a light or dark system theme instead of fighting it.
"""

from __future__ import annotations

from PySide6.QtGui import QPalette


def stylesheet(palette: QPalette) -> str:
    text = palette.color(QPalette.ColorRole.WindowText)
    base = palette.color(QPalette.ColorRole.Base)
    highlight = palette.color(QPalette.ColorRole.Highlight)

    # A muted version of the foreground, for secondary text.
    muted = f"rgba({text.red()}, {text.green()}, {text.blue()}, 0.62)"
    badge_bg = f"rgba({highlight.red()}, {highlight.green()}, {highlight.blue()}, 0.18)"
    dark = base.lightness() < 128
    console_bg = "#11141a" if dark else "#fbfbfd"
    console_fg = "#d6dae2" if dark else "#1d2128"

    return f"""
    QLabel[role="heading"] {{
        font-size: 15px;
        font-weight: 600;
    }}
    QLabel[role="hint"] {{
        color: {muted};
        font-size: 11px;
    }}
    QLabel[role="blurb"] {{
        color: {muted};
        font-size: 12px;
        padding-bottom: 4px;
    }}
    QLabel[role="badge"] {{
        color: {highlight.name()};
        background: {badge_bg};
        border-radius: 7px;
        padding: 2px 9px;
        font-size: 11px;
        font-weight: 600;
    }}
    QFrame[role="toolbar"] {{
        background: {badge_bg};
        border-radius: 8px;
    }}
    QPlainTextEdit[role="console"] {{
        background: {console_bg};
        color: {console_fg};
        border: none;
        selection-background-color: {highlight.name()};
    }}
    QGroupBox {{
        font-weight: 600;
        margin-top: 10px;
        border: 1px solid {muted};
        border-radius: 6px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 5px;
    }}
    """
