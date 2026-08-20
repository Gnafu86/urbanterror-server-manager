#!/usr/bin/env python3
"""Generate the application icon.

Drawn rather than committed as a binary so there is no opaque blob in the repo
and the icon can be regenerated at any size. Qt is already a dependency, so this
needs nothing extra.

    python packaging/make_icon.py output.png [size]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

BACKGROUND_TOP = "#2b3444"
BACKGROUND_BOTTOM = "#161b24"
ACCENT = "#e8a33d"
TEXT = "#f2f4f8"


def render(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Rounded background.
    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0.0, QColor(BACKGROUND_TOP))
    gradient.setColorAt(1.0, QColor(BACKGROUND_BOTTOM))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # Three stacked bars, a server rack seen head on.
    bar_height = size * 0.11
    bar_width = size * 0.46
    left = size * 0.27
    for index in range(3):
        top = size * 0.26 + index * (bar_height + size * 0.075)
        painter.setBrush(QColor(TEXT if index else ACCENT))
        painter.drawRoundedRect(
            QRectF(left, top, bar_width, bar_height),
            bar_height / 2, bar_height / 2,
        )
        # Status light on each bar.
        painter.setBrush(QColor(ACCENT if index else TEXT))
        dot = bar_height * 0.42
        painter.drawEllipse(
            QRectF(left + bar_width - dot * 1.7, top + (bar_height - dot) / 2, dot, dot)
        )

    # "UrT" wordmark under the rack.
    font = QFont()
    font.setPointSizeF(size * 0.13)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QPen(QColor(ACCENT)))
    painter.drawText(
        QRectF(0, size * 0.70, size, size * 0.22),
        Qt.AlignmentFlag.AlignCenter,
        "UrT",
    )

    painter.end()
    return pixmap


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    target = Path(argv[1])
    size = int(argv[2]) if len(argv) > 2 else 256

    # Qt needs an application object before it will paint, but no display.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])

    target.parent.mkdir(parents=True, exist_ok=True)
    if not render(size).save(str(target), "PNG"):
        print(f"error: could not write {target}", file=sys.stderr)
        return 1
    print(f"wrote {target} ({size}x{size})")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
