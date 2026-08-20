"""Custom maps and the HTTP server that hands them to joining players.

A player who joins a server running a map they do not have will download it from
``sv_dlURL`` if the server sets one. This page manages both halves: the ``.pk3``
files a profile carries, and the built-in HTTP server that offers them.

Adding a ``.pk3`` here copies it into the profile's own ``q3ut4`` directory,
which is both where the game server loads maps from and where the download
server serves them, so the two can never drift apart.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import httpd
from ..model import maps
from ..model.profile import Profile


class DownloadPage(QWidget):
    """Custom .pk3 management plus the map download server."""

    #: A profile field changed (dl_enabled, dl_port, dl_host).
    settings_changed = Signal()
    #: The user asked to start or stop the download server.
    server_toggled = Signal(bool)
    #: The set of installed .pk3 files changed.
    maps_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._profile: Profile | None = None
        self._mod_dir: Path | None = None
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 14, 16, 18)
        layout.setSpacing(14)
        scroll.setWidget(body)

        layout.addWidget(self._build_maps_group(), 1)
        layout.addWidget(self._build_server_group())
        layout.addWidget(self._build_activity_group())

    # -- Construction -------------------------------------------------------

    def _build_maps_group(self) -> QGroupBox:
        box = QGroupBox("Custom maps")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        blurb = QLabel(
            "Maps added here are copied into this server's own map folder, so the "
            "server can load them and joining players can download them."
        )
        blurb.setWordWrap(True)
        blurb.setProperty("role", "blurb")
        layout.addWidget(blurb)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["File", "Size", "Maps inside"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setMinimumHeight(180)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        add = QPushButton("Add .pk3…")
        add.setToolTip("Copy one or more map packs into this server")
        add.clicked.connect(self._add_maps)
        buttons.addWidget(add)

        remove = QPushButton("Remove")
        remove.setToolTip("Delete the selected map pack from this server")
        remove.clicked.connect(self._remove_map)
        buttons.addWidget(remove)

        reveal = QPushButton("Open folder")
        reveal.setToolTip("Show the folder these files live in")
        reveal.clicked.connect(self._open_folder)
        buttons.addWidget(reveal)

        buttons.addStretch(1)
        self._maps_summary = QLabel()
        self._maps_summary.setProperty("role", "hint")
        buttons.addWidget(self._maps_summary)
        layout.addLayout(buttons)
        return box

    def _build_server_group(self) -> QGroupBox:
        box = QGroupBox("Map download server")
        grid = QGridLayout(box)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self._enabled = QCheckBox("Serve custom maps over HTTP")
        self._enabled.setToolTip(
            "Runs a small web server that offers only .pk3 files from this "
            "server's map folder, and points sv_dlURL at it."
        )
        self._enabled.toggled.connect(self._on_enabled_toggled)
        grid.addWidget(self._enabled, 0, 0, 1, 3)

        grid.addWidget(QLabel("Port:"), 1, 0)
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(httpd.DEFAULT_PORT)
        self._port.valueChanged.connect(self._on_field_changed)
        grid.addWidget(self._port, 1, 1)
        port_hint = QLabel("TCP. Must be reachable by players, so open it in your firewall.")
        port_hint.setProperty("role", "hint")
        port_hint.setWordWrap(True)
        grid.addWidget(port_hint, 1, 2)

        grid.addWidget(QLabel("Address:"), 2, 0)
        self._host = QLineEdit()
        self._host.setPlaceholderText(f"detected automatically ({httpd.local_ip()})")
        self._host.editingFinished.connect(self._on_field_changed)
        grid.addWidget(self._host, 2, 1)
        host_hint = QLabel(
            "The address players use to reach you. Leave empty on a LAN. Behind a "
            "router, enter your public address and forward the port."
        )
        host_hint.setProperty("role", "hint")
        host_hint.setWordWrap(True)
        grid.addWidget(host_hint, 2, 2)

        grid.addWidget(QLabel("Download URL:"), 3, 0)
        self._url = QLineEdit()
        self._url.setReadOnly(True)
        self._url.setToolTip("This is written to sv_dlURL when the server starts.")
        grid.addWidget(self._url, 3, 1, 1, 2)

        self._status = QLabel()
        self._status.setProperty("role", "hint")
        self._status.setWordWrap(True)
        grid.addWidget(self._status, 4, 0, 1, 3)

        note = QLabel(
            "Only .pk3 files are served. Nothing else in the folder is reachable — "
            "the generated server config lives there too and contains your rcon "
            "password.\n"
            "To use a web host you already have instead, leave this off and set "
            "“Download URL” under Network & Slots. Files must be reachable at "
            "<your URL>/q3ut4/<pack>.pk3"
        )
        note.setWordWrap(True)
        note.setProperty("role", "hint")
        # Plain text, or Qt's rich-text heuristic eats the angle brackets in the
        # example URL and shows the markup instead of rendering it.
        note.setTextFormat(Qt.TextFormat.PlainText)
        grid.addWidget(note, 5, 0, 1, 3)
        return box

    def _build_activity_group(self) -> QGroupBox:
        box = QGroupBox("Download activity")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 12, 14, 12)

        self._activity = QPlainTextEdit()
        self._activity.setReadOnly(True)
        self._activity.setMaximumBlockCount(500)
        self._activity.setMinimumHeight(90)
        self._activity.setPlaceholderText("Requests from players appear here.")
        layout.addWidget(self._activity)
        return box

    # -- Population ---------------------------------------------------------

    def set_profile(self, profile: Profile, mod_dir: Path) -> None:
        self._loading = True
        try:
            self._profile = profile
            self._mod_dir = Path(mod_dir)
            self._enabled.setChecked(profile.dl_enabled)
            self._port.setValue(profile.dl_port)
            self._host.setText(profile.dl_host)
            self._refresh_url()
            self.refresh_maps()
        finally:
            self._loading = False

    def refresh_maps(self) -> None:
        if self._mod_dir is None:
            return
        packs = maps.list_custom(self._mod_dir)
        self._table.setRowCount(len(packs))
        problems = 0
        for row, pack in enumerate(packs):
            inside = ", ".join(pack.maps) if pack.maps else "no maps (assets only)"
            problem = pack.download_problem()
            if problem:
                problems += 1
            for col, text in enumerate((pack.name, pack.size_label, inside)):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(pack.path))
                    item.setToolTip(
                        problem or f"Players fetch this from {pack.download_path}"
                    )
                    if problem:
                        item.setText(f"⚠  {text}")
                self._table.setItem(row, col, item)

        total = sum(len(p.maps) for p in packs)
        summary = (
            f"{len(packs)} pack{'s' if len(packs) != 1 else ''}, {total} map"
            f"{'s' if total != 1 else ''}"
        )
        if problems:
            summary += f"  ·  ⚠ {problems} cannot be auto-downloaded (hover for why)"
        self._maps_summary.setText(summary)

    def set_server_state(self, running: bool, port: int = 0) -> None:
        self._refresh_url()
        if running:
            self._status.setText(f"Running on port {port}. Serving .pk3 files only.")
        elif self._enabled.isChecked():
            self._status.setText("Starts with the server.")
        else:
            self._status.setText("Off. Players cannot download maps they are missing.")

    def log_activity(self, line: str) -> None:
        self._activity.appendPlainText(line)

    # -- Editing ------------------------------------------------------------

    def _on_enabled_toggled(self, checked: bool) -> None:
        self._port.setEnabled(checked)
        self._host.setEnabled(checked)
        if self._loading or self._profile is None:
            return
        self._profile.dl_enabled = checked
        self._refresh_url()
        self.settings_changed.emit()
        self.server_toggled.emit(checked)

    def _on_field_changed(self, *_args) -> None:
        if self._loading or self._profile is None:
            return
        self._profile.dl_port = self._port.value()
        self._profile.dl_host = self._host.text().strip()
        self._refresh_url()
        self.settings_changed.emit()

    def _refresh_url(self) -> None:
        if self._profile is None:
            return
        host = self._profile.dl_host or httpd.local_ip()
        self._url.setText(f"http://{host}:{self._profile.dl_port}")

    # -- Map management -----------------------------------------------------

    def _add_maps(self) -> None:
        if self._mod_dir is None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add custom maps", str(Path.home()), "Map packs (*.pk3);;All files (*)"
        )
        if not files:
            return

        added, failed = 0, []
        for path in files:
            try:
                maps.install_pk3(Path(path), self._mod_dir)
                added += 1
            except maps.MapInstallError as exc:
                if "already installed" in str(exc):
                    if self._confirm_overwrite(Path(path).name):
                        try:
                            maps.install_pk3(Path(path), self._mod_dir, overwrite=True)
                            added += 1
                            continue
                        except (maps.MapInstallError, OSError) as retry_exc:
                            failed.append(str(retry_exc))
                            continue
                    continue
                failed.append(str(exc))
            except OSError as exc:
                failed.append(f"{Path(path).name}: {exc}")

        self.refresh_maps()
        if added:
            self.maps_changed.emit()
        if failed:
            QMessageBox.warning(self, "Some maps were not added", "\n\n".join(failed))

    def _confirm_overwrite(self, name: str) -> bool:
        return QMessageBox.question(
            self, "Replace map pack?",
            f"{name} is already installed. Replace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _remove_map(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        path = Path(item.data(Qt.ItemDataRole.UserRole))

        if QMessageBox.question(
            self, "Remove map pack",
            f"Delete {path.name} from this server?\n\n"
            "Players who already downloaded it keep their copy.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            maps.remove_pk3(path)
        except (maps.MapInstallError, OSError) as exc:
            QMessageBox.warning(self, "Could not remove", str(exc))
            return
        self.refresh_maps()
        self.maps_changed.emit()

    def _open_folder(self) -> None:
        if self._mod_dir is None:
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        self._mod_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._mod_dir)))

    def set_read_only(self, read_only: bool) -> None:
        for w in (self._table, self._enabled, self._port, self._host):
            w.setEnabled(not read_only)
