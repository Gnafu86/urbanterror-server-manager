"""Application bootstrap."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from . import paths
from .ui.mainwindow import MainWindow
from .ui.theme import stylesheet


def _ask_for_install() -> object | None:
    """Let the user point at the game when it is not in a standard location."""
    QMessageBox.information(
        None,
        "Urban Terror not found",
        "The Urban Terror installation could not be found automatically.\n\n"
        "Choose the folder containing the 'q3ut4' directory and the dedicated "
        "server binary.",
    )
    chosen = QFileDialog.getExistingDirectory(None, "Select the Urban Terror folder")
    if not chosen:
        return None
    install = paths.find_install(chosen)
    if install is None:
        QMessageBox.critical(
            None,
            "Not a valid installation",
            f"No dedicated server binary was found under {chosen}.\n\n"
            "Expected a 'q3ut4' folder alongside a binary such as "
            "'urbanterror-ded' or 'Quake3-UrT-Ded.x86_64'.",
        )
    return install


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    app = QApplication(argv)
    app.setApplicationName("Urban Terror Server Manager")
    app.setApplicationDisplayName("Urban Terror Server Manager")
    app.setStyleSheet(stylesheet(app.palette()))

    paths.ensure_dirs()

    install = paths.find_install()
    if install is None:
        install = _ask_for_install()
        if install is None:
            return 1

    window = MainWindow(install)
    window.show()
    return app.exec()
