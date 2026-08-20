"""Live player list with admin actions.

Two sources are combined. ``getstatus`` over UDP always works and gives names,
scores and pings. The ``status`` console command additionally gives the client
*number*, which every admin command needs to address a player, so it is issued
on a timer and its output parsed off the console stream.

Actions that remove a player from the server ask for confirmation first: a
misfired kick on a busy server is disruptive and cannot be undone.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from ..core import query
from ..core.channel import ChannelError, ControlChannel

#: How often to refresh the table, in milliseconds.
REFRESH_MS = 4000


class PlayersPanel(QWidget):
    """A table of connected players and what an admin can do to them."""

    #: Ask the owner to run a console command (it owns the channel).
    command_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._host = "127.0.0.1"
        self._port = 27960
        self._channel: ControlChannel | None = None
        self._numbers: dict[str, int] = {}   # stripped name -> client number
        self._active = False
        self._visible = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._summary = QLabel("Not connected")
        bar.addWidget(self._summary, 1)

        for text, handler, tip in (
            ("Refresh", self.refresh, "Query the server now"),
            ("Say…", self._say, "Send a message to everyone"),
            ("Big text…", self._bigtext, "Show a large message on screen"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            bar.addWidget(b)
        layout.addLayout(bar)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["#", "Player", "Score", "Ping"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_menu)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        hint = QLabel("Right-click a player for admin actions.")
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    # -- Wiring -------------------------------------------------------------

    def attach(self, host: str, port: int, channel: ControlChannel | None) -> None:
        """Point the panel at a server and start polling."""
        self._host, self._port, self._channel = host, int(port), channel
        self._active = True
        self._sync_timer()
        if self._visible:
            self.refresh()

    def detach(self) -> None:
        self._active = False
        self._timer.stop()
        self._channel = None
        self._numbers.clear()
        self._table.setRowCount(0)
        self._summary.setText("Not connected")

    def set_visible_to_user(self, visible: bool) -> None:
        """Poll only while this panel is the tab on screen.

        Refreshing means asking the server for its ``status``, whose reply is
        printed on the server console. Polling in the background would fill the
        console tab with output nobody asked for.
        """
        self._visible = visible
        self._sync_timer()
        if visible and self._active:
            self.refresh()

    def _sync_timer(self) -> None:
        if self._active and self._visible:
            self._timer.start(REFRESH_MS)
        else:
            self._timer.stop()

    def consume_output(self, text: str) -> None:
        """Watch console output for ``status`` blocks to learn client numbers."""
        for client in query.parse_status(text):
            self._numbers[client.display_name] = client.number

    # -- Refresh ------------------------------------------------------------

    def refresh(self) -> None:
        if not self._active:
            return
        try:
            status = query.get_status(self._host, self._port, timeout=0.8)
        except query.QueryError:
            self._summary.setText("Server is not responding")
            self._table.setRowCount(0)
            return

        self._populate(status)

        # Ask for client numbers only when some player on screen lacks one.
        # The reply is parsed either from the returned text (rcon) or off the
        # console stream (local stdin).
        unknown = [p for p in status.players if p.display_name not in self._numbers]
        if unknown and self._channel is not None and self._channel.available:
            try:
                reply = self._channel.send("status", quiet=True)
            except ChannelError:
                reply = None
            if reply:
                self.consume_output(reply)
                self._populate(status)

    def _populate(self, status: query.ServerStatus) -> None:
        self._table.setRowCount(len(status.players))
        for row, player in enumerate(status.players):
            number = self._numbers.get(player.display_name)
            cells = [
                "" if number is None else str(number),
                player.display_name,
                str(player.score),
                "connecting" if player.is_connecting else str(player.ping),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setToolTip(player.name)   # the raw name, colour codes and all
                self._table.setItem(row, col, item)

        self._summary.setText(
            f"{status.player_count} / {status.max_clients} players"
            f"  ·  {status.mapname or 'no map'}"
            f"  ·  {status.hostname}"
        )

    # -- Actions ------------------------------------------------------------

    def _selected(self) -> tuple[int | None, str]:
        row = self._table.currentRow()
        if row < 0:
            return None, ""
        name = self._table.item(row, 1).text()
        number_text = self._table.item(row, 0).text()
        return (int(number_text) if number_text else None), name

    def _show_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        self._table.selectRow(row)
        number, name = self._selected()

        menu = QMenu(self)
        if number is None:
            menu.addAction(
                "Client number unknown — run 'status' in the console"
            ).setEnabled(False)
            menu.exec(self._table.viewport().mapToGlobal(pos))
            return

        menu.addAction(f"Message {name}…", lambda: self._tell(number, name))
        menu.addSeparator()
        menu.addAction("Slap", lambda: self._run(f"slap {number}"))
        menu.addAction("Smite", lambda: self._run(f"smite {number}"))
        menu.addAction("Nuke", lambda: self._run(f"nuke {number}"))
        menu.addSeparator()
        menu.addAction("Mute", lambda: self._run(f"mute {number}"))
        team_menu = menu.addMenu("Force to team")
        for label, team in (("Red", "red"), ("Blue", "blue"),
                            ("Spectator", "spectator"), ("Free", "free")):
            team_menu.addAction(label, lambda t=team: self._run(f"forceteam {number} {t}"))
        menu.addSeparator()
        menu.addAction("Kick…", lambda: self._kick(number, name))
        menu.addAction("Ban…", lambda: self._ban(number, name))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _run(self, command: str) -> None:
        self.command_requested.emit(command)

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _kick(self, number: int, name: str) -> None:
        reason, ok = QInputDialog.getText(self, "Kick player", f"Reason for kicking {name}:")
        if not ok:
            return
        if not self._confirm("Kick player", f"Kick {name} from the server?"):
            return
        reason = reason.strip().replace('"', "'").replace(";", ",")
        self._run(f'clientkickreason {number} "{reason}"' if reason else f"clientkick {number}")

    def _ban(self, number: int, name: str) -> None:
        minutes, ok = QInputDialog.getInt(
            self, "Ban player", f"Ban {name} for how many minutes?  (0 = permanent)",
            60, 0, 60 * 24 * 365,
        )
        if not ok:
            return
        span = "permanently" if minutes == 0 else f"for {minutes} minutes"
        if not self._confirm("Ban player", f"Ban {name} {span}?"):
            return
        self._run(f"addip {number}" if minutes == 0 else f"addipexpire {number} {minutes}")

    def _tell(self, number: int, name: str) -> None:
        text, ok = QInputDialog.getText(self, "Message player", f"Message to {name}:")
        if ok and text.strip():
            clean = text.replace('"', "'").replace(";", ",")
            self._run(f'tell {number} "{clean}"')

    def _say(self) -> None:
        text, ok = QInputDialog.getText(self, "Say", "Message to everyone:")
        if ok and text.strip():
            clean = text.replace('"', "'").replace(";", ",")
            self._run(f'say "{clean}"')

    def _bigtext(self) -> None:
        text, ok = QInputDialog.getText(self, "Big text", "Message to show on screen:")
        if ok and text.strip():
            clean = text.replace('"', "'").replace(";", ",")
            self._run(f'bigtext "{clean}"')
