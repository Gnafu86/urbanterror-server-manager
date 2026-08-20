"""Shared test fixtures.

Qt allows exactly one application object per process, and it must be a
``QApplication`` rather than a bare ``QCoreApplication`` for any test that
creates a widget. Since the supervisor tests only need an event loop while the
UI tests need widgets, both share the single ``QApplication`` created here --
otherwise whichever ran first would decide, and the widget tests would abort.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Must be set before Qt initialises, so the tests need no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])
