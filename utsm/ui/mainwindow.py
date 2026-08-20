"""The main window: profiles on the left, everything about one server on the right.

Editing is live. A change to a running server's option is pushed straight to it
when the engine allows that, and badged as needing a map reload when it does not,
so what the form shows and what the server is doing never quietly diverge.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..core import cfgwriter, httpd
from ..core.channel import ChannelError, ControlChannel, NullChannel, RconChannel
from ..core.httpd import DownloadServer
from ..core.supervisor import ServerState, ServerSupervisor
from ..model import cvars, maps
from ..model.profile import LOCAL, REMOTE, Profile, ProfileStore, default_profile
from .console import ConsolePanel
from .downloadpage import DownloadPage
from .formbuilder import CVarFormPage, escape_mnemonic
from .gearpage import GearPage
from .mapcyclepage import MapCyclePage
from .players import PlayersPanel
from .votespage import VotesPage

#: Delay before persisting edits, so typing does not hit the disk per keystroke.
AUTOSAVE_MS = 800

#: Registry groups that have their own top-level tab, so they are left out of
#: the Settings sub-tabs rather than being presented in two places.
DEDICATED_GROUPS = frozenset({"weapons", "voting", "mapcycle"})


class MainWindow(QMainWindow):
    def __init__(self, install, parent: QWidget | None = None):
        super().__init__(parent)
        self.install = install
        self.store = ProfileStore(paths.profiles_file()).load()
        self.supervisors: dict[str, ServerSupervisor] = {}
        self.download_servers: dict[str, DownloadServer] = {}
        self._current: Profile | None = None
        self._loading = False

        self.setWindowTitle("Urban Terror Server Manager")
        self.resize(1180, 820)

        self._available_maps = maps.discover(install.mod_path) if install else []

        self._build_ui()
        self._build_menu()

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.timeout.connect(self._save)

        if not self.store.profiles:
            self.store.add(default_profile(installed_maps=maps.names(self._available_maps)))
            self._save()

        self._refresh_profile_list()
        if self.store.profiles:
            self._profiles.setCurrentRow(0)

    # -- Construction -------------------------------------------------------

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 920])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready")

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 6, 10)
        layout.setSpacing(8)

        title = QLabel("Servers")
        title.setProperty("role", "heading")
        layout.addWidget(title)

        self._profiles = QListWidget()
        self._profiles.currentRowChanged.connect(self._on_profile_selected)
        self._profiles.itemDoubleClicked.connect(lambda _: self._rename_profile())
        self._profiles.setToolTip("Double-click a server to rename it.")
        layout.addWidget(self._profiles, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(6)
        for text, handler in (
            ("New server", self._new_profile),
            ("Add remote…", self._new_remote),
            ("Rename…", self._rename_profile),
            ("Duplicate", self._duplicate_profile),
            ("Import .cfg…", self._import_cfg),
            ("Delete", self._delete_profile),
        ):
            b = QPushButton(text)
            b.clicked.connect(handler)
            buttons.addWidget(b)
        layout.addLayout(buttons)
        return panel

    def _build_detail(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 10, 10, 10)
        layout.setSpacing(8)

        layout.addWidget(self._build_toolbar())

        self._tabs = QTabWidget()

        self._settings_tabs = QTabWidget()
        self._settings_tabs.setTabPosition(QTabWidget.TabPosition.West)
        self._tabs.addTab(self._settings_tabs, "Settings")

        self._gear = GearPage()
        self._gear.changed.connect(self._on_value_changed)
        self._tabs.addTab(self._gear, escape_mnemonic("Weapons & Items"))

        self._votes = VotesPage()
        self._votes.changed.connect(self._on_value_changed)
        self._tabs.addTab(self._votes, "Voting")

        self._mapcycle = MapCyclePage()
        self._mapcycle.set_available_maps(self._available_maps)
        self._mapcycle.cycle_changed.connect(self._on_cycle_changed)
        self._mapcycle.start_map_changed.connect(self._on_start_map_changed)
        self._tabs.addTab(self._mapcycle, "Map Cycle")

        self._downloads = DownloadPage()
        self._downloads.settings_changed.connect(self._on_download_settings_changed)
        self._downloads.server_toggled.connect(self._on_download_toggled)
        self._downloads.maps_changed.connect(self._on_custom_maps_changed)
        self._tabs.addTab(self._downloads, "Custom Maps")

        self._players = PlayersPanel()
        self._players.command_requested.connect(self._send_command)
        self._tabs.addTab(self._players, "Players")

        self._console = ConsolePanel()
        self._console.command_entered.connect(self._send_command)
        self._tabs.addTab(self._console, "Console")

        self._tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self._tabs, 1)
        return panel

    def _on_tab_changed(self, index: int) -> None:
        self._players.set_visible_to_user(self._tabs.widget(index) is self._players)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("role", "toolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)

        self._title = QLabel("—")
        self._title.setProperty("role", "heading")
        row.addWidget(self._title)

        self._state_badge = QLabel("Stopped")
        self._state_badge.setProperty("role", "badge")
        row.addWidget(self._state_badge)
        row.addStretch(1)

        self._btn_start = QPushButton("Start")
        self._btn_start.clicked.connect(self._start)
        row.addWidget(self._btn_start)

        self._btn_stop = QPushButton("Stop")
        self._btn_stop.clicked.connect(self._stop)
        row.addWidget(self._btn_stop)

        self._btn_restart = QPushButton("Restart")
        self._btn_restart.clicked.connect(self._restart)
        row.addWidget(self._btn_restart)

        self._btn_reload = QPushButton("Reload map")
        self._btn_reload.setToolTip("Reload the map so latched settings take effect")
        self._btn_reload.clicked.connect(lambda: self._send_command("reload"))
        row.addWidget(self._btn_reload)

        self._btn_cycle = QPushButton("Next map")
        self._btn_cycle.clicked.connect(lambda: self._send_command("cyclemap"))
        row.addWidget(self._btn_cycle)

        return bar

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        act_new = QAction("&New server", self, shortcut=QKeySequence.StandardKey.New)
        act_new.triggered.connect(self._new_profile)
        file_menu.addAction(act_new)
        act_import = QAction("&Import server.cfg…", self)
        act_import.triggered.connect(self._import_cfg)
        file_menu.addAction(act_import)
        act_export = QAction("&Export generated .cfg…", self)
        act_export.triggered.connect(self._export_cfg)
        file_menu.addAction(act_export)
        file_menu.addSeparator()
        act_quit = QAction("&Quit", self, shortcut=QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        server_menu = self.menuBar().addMenu("&Server")
        for text, handler, shortcut in (
            ("&Start", self._start, "Ctrl+R"),
            ("S&top", self._stop, "Ctrl+T"),
            ("&Restart", self._restart, "Ctrl+Shift+R"),
        ):
            act = QAction(text, self, shortcut=QKeySequence(shortcut))
            act.triggered.connect(handler)
            server_menu.addAction(act)
        server_menu.addSeparator()
        act_check = QAction("&Check configuration", self)
        act_check.triggered.connect(self._check_configuration)
        server_menu.addAction(act_check)

        view_menu = self.menuBar().addMenu("&View")
        self._act_advanced = QAction("Show &advanced options", self, checkable=True)
        self._act_advanced.toggled.connect(self._on_advanced_toggled)
        view_menu.addAction(self._act_advanced)
        self._act_help = QAction("Show option &descriptions", self, checkable=True, checked=True)
        self._act_help.toggled.connect(self._on_help_toggled)
        view_menu.addAction(self._act_help)

    # -- Profiles -----------------------------------------------------------

    def _refresh_profile_list(self) -> None:
        current = self._profiles.currentRow()
        self._profiles.blockSignals(True)
        self._profiles.clear()
        for profile in self.store.profiles:
            state = self._state_of(profile)
            marker = {
                ServerState.RUNNING: "●",
                ServerState.STARTING: "◐",
                ServerState.STOPPING: "◐",
                ServerState.CRASHED: "✕",
            }.get(state, "○")
            suffix = "  (remote)" if profile.kind == REMOTE else ""
            item = QListWidgetItem(f"{marker}  {profile.name}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            item.setToolTip(f"{profile.name}\nPort {profile.net_port}\n{state.label}")
            self._profiles.addItem(item)
        self._profiles.blockSignals(False)
        if 0 <= current < self._profiles.count():
            self._profiles.setCurrentRow(current)

    def _maps_for(self, profile: Profile) -> list:
        """Maps this profile's server could actually load.

        The base install plus the profile's own homepath, which is exactly what
        the engine searches. Maps sitting in some other directory are not
        offered, because listing a map the server cannot load would be worse
        than not listing it.
        """
        roots = [self.install.mod_path] if self.install else []
        if profile.is_local:
            roots.append(paths.profile_mod_dir(profile.id))
        return maps.discover(*roots)

    def _state_of(self, profile: Profile) -> ServerState:
        sup = self.supervisors.get(profile.id)
        return sup.state if sup else ServerState.STOPPED

    def _on_profile_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.store.profiles):
            self._current = None
            return
        self._current = self.store.profiles[row]
        self._load_profile(self._current)

    def _load_profile(self, profile: Profile) -> None:
        self._loading = True
        try:
            self._title.setText(profile.name)
            self._rebuild_settings_tabs(profile)
            values = profile.effective()
            self._gear.load(str(profile.get("g_gear") or ""))
            self._votes.load(int(profile.get("g_allowvote") or 0), values)
            self._mapcycle.set_available_maps(self._maps_for(profile))
            self._mapcycle.load(profile.mapcycle, profile.start_map)
            self._downloads.set_profile(profile, paths.profile_mod_dir(profile.id))

            read_only = profile.kind == REMOTE
            self._mapcycle.set_read_only(read_only)
            # A remote server's maps live on the far machine; there is nothing
            # useful this manager can do with a local copy of them.
            self._downloads.set_read_only(read_only)
        finally:
            self._loading = False

        dl = self.download_servers.get(profile.id)
        self._downloads.set_server_state(
            bool(dl and dl.is_running), dl.port if dl else 0
        )

        self._attach_live_panels(profile)
        self._update_controls()

    def _rebuild_settings_tabs(self, profile: Profile) -> None:
        """Rebuild the option pages, since which groups apply depends on gametype."""
        remembered = self._settings_tabs.tabText(self._settings_tabs.currentIndex())
        self._settings_tabs.clear()

        values = profile.effective()
        for group in cvars.visible_groups(profile.gametype):
            if group.key in DEDICATED_GROUPS:
                continue
            page = CVarFormPage(
                group,
                profile.gametype,
                show_advanced=self._act_advanced.isChecked(),
                show_help=self._act_help.isChecked(),
            )
            # Groups whose only options are the composite editors (gear, votes,
            # map cycle) have their own top-level tabs and would appear here as
            # blank pages.
            if not page.fields:
                page.deleteLater()
                continue
            page.load(values)
            page.changed.connect(self._on_value_changed)
            self._settings_tabs.addTab(page, escape_mnemonic(group.title))

        for i in range(self._settings_tabs.count()):
            if self._settings_tabs.tabText(i) == remembered:
                self._settings_tabs.setCurrentIndex(i)
                break

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New server", "Name:", text="My Server")
        if not ok or not name.strip():
            return
        profile = default_profile(name.strip(), maps.names(self._available_maps))
        self.store.add(profile)
        self._save()
        self._refresh_profile_list()
        self._profiles.setCurrentRow(len(self.store.profiles) - 1)

    def _new_remote(self) -> None:
        host, ok = QInputDialog.getText(self, "Add remote server", "Host or IP:")
        if not ok or not host.strip():
            return
        port, ok = QInputDialog.getInt(self, "Add remote server", "Port:", 27960, 1, 65535)
        if not ok:
            return
        password, ok = QInputDialog.getText(self, "Add remote server", "Rcon password:")
        if not ok:
            return

        profile = Profile(name=f"{host.strip()}:{port}", kind=REMOTE)
        profile.host = host.strip()
        profile.port = port
        profile.rcon_password = password
        self.store.add(profile)
        self._save()
        self._refresh_profile_list()
        self._profiles.setCurrentRow(len(self.store.profiles) - 1)

    def _rename_profile(self) -> None:
        """Rename the profile. This is the manager's own label for the server,
        separate from ``sv_hostname``, which is what players see."""
        if not self._current:
            return
        name, ok = QInputDialog.getText(
            self, "Rename server", "Name:", text=self._current.name
        )
        if not ok or not name.strip():
            return
        self._current.name = name.strip()
        self._title.setText(self._current.name)
        self._save()
        self._refresh_profile_list()

    def _duplicate_profile(self) -> None:
        if not self._current:
            return
        self.store.add(self._current.duplicate())
        self._save()
        self._refresh_profile_list()
        self._profiles.setCurrentRow(len(self.store.profiles) - 1)

    def _delete_profile(self) -> None:
        if not self._current:
            return
        if self._state_of(self._current).is_active:
            QMessageBox.information(
                self, "Server running",
                "Stop the server before deleting its profile.",
            )
            return
        if QMessageBox.question(
            self, "Delete server", f"Delete the profile '{self._current.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.store.remove(self._current.id)
        self._save()
        self._refresh_profile_list()
        self._profiles.setCurrentRow(0 if self.store.profiles else -1)

    def _import_cfg(self) -> None:
        start_dir = str(self.install.mod_path) if self.install else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Import a server config", start_dir, "Config files (*.cfg);;All files (*)"
        )
        if not path:
            return
        try:
            profile = Profile.from_cfg_file(Path(path))
        except OSError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return

        cycle_name = str(profile.get("g_mapcycle") or "").strip()
        if cycle_name:
            candidate = Path(path).parent / cycle_name
            if candidate.is_file():
                profile.mapcycle = maps.parse_cycle(
                    candidate.read_text(encoding="utf-8", errors="replace")
                )

        self.store.add(profile)
        self._save()
        self._refresh_profile_list()
        self._profiles.setCurrentRow(len(self.store.profiles) - 1)

        note = f"Imported {Path(path).name}."
        if profile.extra_cfg.strip():
            note += (
                f" {len(profile.extra_cfg.splitlines())} directive(s) this manager does "
                "not model were kept and will still be written to the config."
            )
        self.statusBar().showMessage(note, 12000)

    def _export_cfg(self) -> None:
        if not self._current:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export generated config",
            str(Path.home() / f"{self._current.name.replace(' ', '_')}.cfg"),
            "Config files (*.cfg)",
        )
        if not path:
            return
        try:
            Path(path).write_text(cfgwriter.render_cfg(self._current), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Wrote {path}", 8000)

    # -- Editing ------------------------------------------------------------

    def _on_value_changed(self, name: str, value) -> None:
        if self._loading or not self._current:
            return
        profile = self._current
        profile.set(name, value)
        self._autosave.start(AUTOSAVE_MS)

        if name == "sv_hostname" and value:
            pass  # the profile name is separate; leave it alone

        # Changing the gametype changes which option groups apply.
        if name == "g_gametype":
            self._rebuild_settings_tabs(profile)

        self._push_live(name, value)

    def _on_cycle_changed(self, cycle: list) -> None:
        if self._loading or not self._current:
            return
        self._current.mapcycle = list(cycle)
        self._autosave.start(AUTOSAVE_MS)

    def _on_start_map_changed(self, name: str) -> None:
        if self._loading or not self._current:
            return
        self._current.start_map = name
        self._autosave.start(AUTOSAVE_MS)

    def _push_live(self, name: str, value) -> None:
        """Apply a changed option to the running server, if it will take it."""
        channel = self._channel_for(self._current)
        if channel is None or not channel.available:
            return
        cvar = cvars.get(name)
        if cvar is None or cvar.name in cvars.COMMAND_LINE_ONLY:
            return

        rendered = cvar.to_cfg_value(value)
        try:
            reply = channel.set_cvar(cvar.name, rendered)
        except ChannelError as exc:
            self.statusBar().showMessage(str(exc), 6000)
            return
        if reply:
            self._console.append(reply if reply.endswith("\n") else reply + "\n")

        if cvar.latched:
            self.statusBar().showMessage(
                f"{cvar.label} is set but only applies on the next map reload.", 8000
            )
        else:
            self.statusBar().showMessage(f"{cvar.label} applied to the running server.", 4000)

    def _on_advanced_toggled(self, _checked: bool) -> None:
        if self._current:
            self._rebuild_settings_tabs(self._current)

    def _on_help_toggled(self, _checked: bool) -> None:
        if self._current:
            self._rebuild_settings_tabs(self._current)

    def _save(self) -> None:
        try:
            self.store.save()
        except OSError as exc:
            self.statusBar().showMessage(f"Could not save profiles: {exc}", 10000)

    # -- Server control -----------------------------------------------------

    def _supervisor_for(self, profile: Profile) -> ServerSupervisor:
        sup = self.supervisors.get(profile.id)
        if sup is None:
            sup = ServerSupervisor(self.install.basepath, self.install.server_binary, self)
            sup.output.connect(self._on_server_output)
            sup.failed.connect(self._on_server_failed)
            sup.state_changed.connect(lambda _s: self._on_state_changed(profile.id))
            self.supervisors[profile.id] = sup
        return sup

    # -- Map download server ------------------------------------------------

    def _download_server_for(self, profile: Profile) -> DownloadServer:
        server = self.download_servers.get(profile.id)
        if server is None:
            server = DownloadServer(self)
            server.activity.connect(self._on_download_activity)
            server.failed.connect(self._on_download_failed)
            self.download_servers[profile.id] = server
        return server

    def _start_download_server(self, profile: Profile) -> bool:
        """Bring up the map download server and point sv_dlURL at it."""
        server = self._download_server_for(profile)
        if server.is_running:
            return True

        root = paths.profile_home(profile.id)
        if not server.start(root, profile.dl_port):
            return False

        url = f"http://{profile.dl_host or httpd.local_ip()}:{server.port}"
        profile.set("sv_dlURL", url)
        self._save()

        if self._current is profile:
            self._downloads.set_server_state(True, server.port)
            self._downloads.log_activity(f"Serving {root}/q3ut4 at {url}")
        self.statusBar().showMessage(f"Map downloads served at {url}", 8000)
        return True

    def _stop_download_server(self, profile: Profile) -> None:
        server = self.download_servers.get(profile.id)
        if server is None or not server.is_running:
            return
        server.stop()
        if self._current is profile:
            self._downloads.set_server_state(False)
            self._downloads.log_activity("Download server stopped.")

    def _on_download_toggled(self, enabled: bool) -> None:
        if not self._current:
            return
        if enabled:
            self._start_download_server(self._current)
        else:
            self._stop_download_server(self._current)
        self._save()

    def _on_download_settings_changed(self) -> None:
        self._autosave.start(AUTOSAVE_MS)
        profile = self._current
        if not profile:
            return
        # A port or address change only takes effect on a restart of the
        # download server, so do that rather than leaving the two disagreeing.
        server = self.download_servers.get(profile.id)
        if server and server.is_running:
            self._stop_download_server(profile)
            self._start_download_server(profile)

    def _on_custom_maps_changed(self) -> None:
        """Refresh the map lists after a .pk3 was added or removed."""
        if not self._current:
            return
        self._mapcycle.set_available_maps(self._maps_for(self._current))
        self._mapcycle.load(self._current.mapcycle, self._current.start_map)

    def _on_download_activity(self, line: str) -> None:
        self._downloads.log_activity(line)

    def _on_download_failed(self, message: str) -> None:
        self.statusBar().showMessage(message, 12000)
        self._downloads.log_activity(message)
        QMessageBox.warning(self, "Download server", message)

    def _channel_for(self, profile: Profile | None) -> ControlChannel | None:
        if profile is None:
            return None
        if profile.kind == REMOTE:
            return RconChannel(profile.host, profile.port, profile.rcon_password)
        sup = self.supervisors.get(profile.id)
        return sup.channel if sup else None

    def _start(self) -> None:
        if not self._current:
            return
        if self._current.kind == REMOTE:
            QMessageBox.information(
                self, "Remote server",
                "This server runs on another machine, so it cannot be started from "
                "here. Its settings and admin commands still work over rcon.",
            )
            return

        problems = self._current.problems()
        conflicts = self.store.port_conflicts().get(self._current.net_port, [])
        running_on_port = [
            p.name for p in self.store.profiles
            if p.id != self._current.id and p.is_local
            and p.net_port == self._current.net_port
            and self._state_of(p).is_active
        ]
        if running_on_port:
            QMessageBox.warning(
                self, "Port in use",
                f"'{running_on_port[0]}' is already running on port "
                f"{self._current.net_port}. Change the port before starting this one.",
            )
            return
        if len(conflicts) > 1:
            problems.append(
                f"Port {self._current.net_port} is also used by: "
                + ", ".join(n for n in conflicts if n != self._current.name)
            )

        if problems and not self._confirm_problems(problems):
            return

        # The download server has to come up first: starting it is what sets
        # sv_dlURL, and the game config is generated during the next call.
        if self._current.dl_enabled:
            self._start_download_server(self._current)

        self._tabs.setCurrentWidget(self._console)
        self._console.clear()
        self._supervisor_for(self._current).start(self._current)

    def _confirm_problems(self, problems: list[str]) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Check the configuration")
        box.setText("This server has configuration issues:")
        box.setInformativeText("\n\n".join(f"• {p}" for p in problems))
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        box.button(QMessageBox.StandardButton.Ok).setText("Start anyway")
        return box.exec() == QMessageBox.StandardButton.Ok

    def _check_configuration(self) -> None:
        if not self._current:
            return
        problems = self._current.problems()
        if not problems:
            QMessageBox.information(
                self, "Configuration", "No problems found with this configuration."
            )
            return
        QMessageBox.warning(
            self, "Configuration",
            "\n\n".join(f"• {p}" for p in problems),
        )

    def _stop(self) -> None:
        if self._current and self._current.id in self.supervisors:
            self.supervisors[self._current.id].stop()

    def _restart(self) -> None:
        if self._current and self._current.id in self.supervisors:
            self.supervisors[self._current.id].restart()

    def _send_command(self, command: str) -> None:
        channel = self._channel_for(self._current)
        if channel is None or not channel.available:
            self.statusBar().showMessage("The server is not running.", 5000)
            return
        try:
            reply = channel.send(command)
        except ChannelError as exc:
            self._console.note(str(exc))
            return
        if reply:
            self._console.append(f"> {command}\n{reply}\n")
            self._players.consume_output(reply)

    # -- Server events ------------------------------------------------------

    def _on_server_output(self, text: str) -> None:
        sender = self.sender()
        # Only show output for the profile currently on screen.
        if self._current and self.supervisors.get(self._current.id) is sender:
            self._console.append(text)
            self._players.consume_output(text)

    def _on_server_failed(self, message: str) -> None:
        self.statusBar().showMessage(message, 12000)
        self._console.note(message)

    def _on_state_changed(self, profile_id: str) -> None:
        self._refresh_profile_list()

        # The download server exists to serve a running game server, so it
        # follows the game server down rather than lingering.
        profile = self.store.by_id(profile_id)
        if profile and self._state_of(profile) in (ServerState.STOPPED, ServerState.CRASHED):
            self._stop_download_server(profile)

        if self._current and self._current.id == profile_id:
            self._update_controls()
            self._attach_live_panels(self._current)

    def _attach_live_panels(self, profile: Profile) -> None:
        state = self._state_of(profile)
        channel = self._channel_for(profile)
        if profile.kind == REMOTE or state is ServerState.RUNNING:
            host = profile.host if profile.kind == REMOTE else "127.0.0.1"
            self._players.set_visible_to_user(
                self._tabs.currentWidget() is self._players
            )
            self._players.attach(host, profile.net_port, channel)
        else:
            self._players.detach()

    def _update_controls(self) -> None:
        profile = self._current
        if profile is None:
            return
        state = self._state_of(profile)
        remote = profile.kind == REMOTE

        self._state_badge.setText("Remote" if remote else state.label)
        self._title.setText(profile.name)

        channel = self._channel_for(profile)
        live = remote or state is ServerState.RUNNING

        self._btn_start.setEnabled(not remote and state in (ServerState.STOPPED, ServerState.CRASHED))
        self._btn_stop.setEnabled(not remote and state.is_active)
        self._btn_restart.setEnabled(not remote and state.is_active)
        self._btn_reload.setEnabled(live)
        self._btn_cycle.setEnabled(live)
        self._console.set_enabled(bool(channel and channel.available) or remote)

    # -- Shutdown -----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        running = [
            p.name for p in self.store.profiles
            if self._state_of(p).is_active
        ]
        if running:
            answer = QMessageBox.question(
                self, "Stop running servers?",
                "Closing the manager stops these servers:\n\n"
                + "\n".join(f"• {n}" for n in running)
                + "\n\nClose anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._autosave.stop()
        self._save()
        for server in self.download_servers.values():
            server.stop()
        for sup in self.supervisors.values():
            sup.stop_and_wait()
        event.accept()
