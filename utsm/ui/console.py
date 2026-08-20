"""The server console: streamed output and a command line.

For a local server this is the child process's own stdout and stdin, so it is
the real console rather than a reconstruction. For a remote server the same
widget carries rcon replies.

Scrollback is capped. A busy server with hit logging enabled produces output
faster than anyone can read it, and an unbounded buffer would grow without limit
over a long session.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.query import strip_colors

#: Lines kept in the console view.
MAX_LINES = 5000

#: Console commands offered for completion. The engine's own list plus the
#: admin commands the gamecode registers, taken from qagame.qvm.
COMMANDS: tuple[str, ...] = (
    # Engine
    "status", "serverinfo", "systeminfo", "dumpuser", "map", "devmap",
    "map_restart", "sectorlist", "kick", "clientkick", "banClient", "banUser",
    "heartbeat", "quit", "exec", "set", "seta", "sets", "setu", "toggle",
    "cvarlist", "cvar_restart", "echo", "vstr", "writeconfig", "killserver",
    "say", "tell", "startserverdemo", "stopserverdemo", "meminfo", "path",
    # Gamecode
    "bigtext", "slap", "nuke", "smite", "mute", "veto", "pause", "reload",
    "cyclemap", "restart", "swapteams", "shuffleteams", "balanceteams",
    "forceteam", "forceall", "swap", "forcecaptain", "forcesub", "forceunsub",
    "forceready", "clientlist", "botlist", "addbot", "gametype",
    "forceteamname", "kickall", "clientkickreason", "addip", "removeip",
    "addipexpire", "auth-ban", "auth-unban", "auth-whois",
)


class ConsolePanel(QWidget):
    """Server output plus a command entry box."""

    #: A command the user wants to run.
    command_entered = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._history: list[str] = []
        self._history_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(MAX_LINES)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setFont(QFont("monospace", 10))
        self._view.setProperty("role", "console")
        layout.addWidget(self._view, 1)

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 8)
        row.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Console command…  (↑ for history)")
        self._input.setFont(QFont("monospace", 10))
        self._input.returnPressed.connect(self._submit)
        self._input.installEventFilter(self)
        row.addWidget(self._input, 1)

        send = QPushButton("Send")
        send.clicked.connect(self._submit)
        row.addWidget(send)

        self._strip_colors = QCheckBox("Strip colours")
        self._strip_colors.setChecked(True)
        self._strip_colors.setToolTip(
            "Remove Quake 3 ^n colour codes from the output so it reads cleanly."
        )
        row.addWidget(self._strip_colors)

        clear = QPushButton("Clear")
        clear.clicked.connect(self._view.clear)
        row.addWidget(clear)

        layout.addWidget(bar)

    # -- Output -------------------------------------------------------------

    def append(self, text: str) -> None:
        """Add server output, following the tail only if already at the bottom."""
        if not text:
            return
        if self._strip_colors.isChecked():
            text = strip_colors(text)

        scrollbar = self._view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4

        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)

        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def note(self, text: str) -> None:
        """Add a line from the manager itself rather than the server."""
        self.append(f"\n[manager] {text}\n")

    def clear(self) -> None:
        self._view.clear()

    # -- Input --------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self._input.setEnabled(enabled)
        self._input.setPlaceholderText(
            "Console command…  (↑ for history)" if enabled
            else "Start the server to use the console"
        )

    def _submit(self) -> None:
        command = self._input.text().strip()
        if not command:
            return
        self._history.append(command)
        self._history_index = len(self._history)
        self._input.clear()
        self.command_entered.emit(command)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if obj is self._input and event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up:
                self._recall(-1)
                return True
            if key == Qt.Key.Key_Down:
                self._recall(1)
                return True
            if key == Qt.Key.Key_Tab:
                self._complete()
                return True
        return super().eventFilter(obj, event)

    def _recall(self, delta: int) -> None:
        if not self._history:
            return
        self._history_index = max(0, min(len(self._history), self._history_index + delta))
        if self._history_index == len(self._history):
            self._input.clear()
        else:
            self._input.setText(self._history[self._history_index])

    def _complete(self) -> None:
        """Complete the first word against the known command list."""
        text = self._input.text()
        if " " in text.strip():
            return
        prefix = text.strip().lower()
        if not prefix:
            return
        matches = [c for c in COMMANDS if c.lower().startswith(prefix)]
        if len(matches) == 1:
            self._input.setText(matches[0] + " ")
        elif matches:
            self.append("\n" + "  ".join(sorted(matches)) + "\n")
