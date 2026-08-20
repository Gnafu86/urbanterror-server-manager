"""Lifecycle control for a locally launched dedicated server.

The server runs as a child of the manager, so closing the manager stops it. Its
console is the child's stdin and stdout, which the dedicated server reads and
writes directly -- no rcon password is needed to administer a server started
here.

Readiness is taken from the server's own output rather than by polling the
network: the game prints ``InitGame:`` once the map is loaded and the gamecode
is up. That is both faster and cheaper than blocking the UI thread on UDP
round-trips, and it is the same line the engine logs on every map change.

Shutdown escalates. ``quit`` on the console is the clean path and lets the
server write its logs and tell the master server it is going; ``terminate`` and
finally ``kill`` are there for a server that has hung.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .. import paths
from ..model.profile import Profile
from . import cfgwriter
from .channel import ChannelError, ControlChannel

#: Printed by the gamecode once a map is loaded and the game is running.
_INIT_GAME = re.compile(r"^InitGame:", re.MULTILINE)
#: Printed while the server is shutting down.
_SHUTDOWN = re.compile(r"Server Shutdown|ShutdownGame", re.MULTILINE)
#: The engine's own fatal error format.
_FATAL = re.compile(r"^(?:Error|ERROR|\*+ERROR\*+|Com_Error):?\s*(.+)$", re.MULTILINE)

#: How long to let a clean ``quit`` work before escalating, in milliseconds.
QUIT_GRACE_MS = 6000
#: How long to let SIGTERM work before SIGKILL, in milliseconds.
TERM_GRACE_MS = 3000
#: How long a server may take to reach InitGame before we call it failed.
START_TIMEOUT_MS = 60000


class ServerState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"

    @property
    def is_active(self) -> bool:
        return self in (ServerState.STARTING, ServerState.RUNNING, ServerState.STOPPING)

    @property
    def label(self) -> str:
        return {
            ServerState.STOPPED: "Stopped",
            ServerState.STARTING: "Starting",
            ServerState.RUNNING: "Running",
            ServerState.STOPPING: "Stopping",
            ServerState.CRASHED: "Crashed",
        }[self]


class StdinChannel(ControlChannel):
    """Console commands written to a running child's stdin.

    Replies are not returned: the server answers on stdout, which the supervisor
    surfaces through its ``output`` signal instead.
    """

    echoes = True

    def __init__(self, supervisor: "ServerSupervisor"):
        self._supervisor = supervisor

    @property
    def available(self) -> bool:
        return self._supervisor.state is ServerState.RUNNING

    def send(self, command: str, quiet: bool = False) -> None:
        self._supervisor.send(command, echo=not quiet)
        return None


class ServerSupervisor(QObject):
    """Starts, stops and talks to one local server."""

    #: A chunk of server console output.
    output = Signal(str)
    #: The lifecycle state changed.
    state_changed = Signal(object)
    #: Something went wrong, with a message suitable for showing the user.
    failed = Signal(str)

    def __init__(self, basepath: Path, binary: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._basepath = Path(basepath)
        self._binary = Path(binary)
        self._state = ServerState.STOPPED
        self._profile: Profile | None = None
        self._buffer = ""
        self._restarting = False
        self._quitting = False

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.setWorkingDirectory(str(self._basepath))
        self._process.readyReadStandardOutput.connect(self._on_output)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_process_error)

        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._on_start_timeout)

        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._escalate)
        #: How far shutdown has escalated: 0 none, 1 sent quit, 2 sent terminate.
        self._stop_stage = 0

        self.channel = StdinChannel(self)

    # -- State --------------------------------------------------------------

    @property
    def state(self) -> ServerState:
        return self._state

    @property
    def profile(self) -> Profile | None:
        return self._profile

    @property
    def is_running(self) -> bool:
        return self._state is ServerState.RUNNING

    @property
    def is_restarting(self) -> bool:
        """True between a restart's stop and the start that follows it.

        A restart passes through STOPPED on its way back up. Anything tied to
        the server's lifetime needs to tell that apart from a real stop, or it
        tears itself down mid-restart and never comes back.
        """
        return self._restarting

    def _set_state(self, state: ServerState) -> None:
        if state is not self._state:
            self._state = state
            self.state_changed.emit(state)

    # -- Lifecycle ----------------------------------------------------------

    def start(self, profile: Profile) -> bool:
        """Generate the config and launch the server."""
        if self._state.is_active:
            self.failed.emit("This server is already running.")
            return False
        if not self._binary.is_file():
            self.failed.emit(f"Server binary not found at {self._binary}.")
            return False

        self._profile = profile
        self._buffer = ""
        self._quitting = False

        try:
            paths.ensure_dirs(profile.id)
            cfgwriter.write(profile)
        except OSError as exc:
            self.failed.emit(f"Could not write the server config: {exc}")
            return False

        args = cfgwriter.launch_args(profile, self._basepath)
        self._set_state(ServerState.STARTING)
        self.output.emit(f"$ {self._binary} {' '.join(args)}\n")
        self._process.start(str(self._binary), args)
        self._start_timer.start(START_TIMEOUT_MS)
        return True

    def stop(self) -> None:
        """Shut the server down cleanly, escalating if it does not comply."""
        if self._process.state() == QProcess.ProcessState.NotRunning:
            self._set_state(ServerState.STOPPED)
            return

        self._start_timer.stop()
        self._set_state(ServerState.STOPPING)
        self._quitting = True

        # The clean path: let the server close its logs and deregister itself.
        try:
            self._write("quit")
        except ChannelError:
            pass

        self._stop_stage = 1
        self._stop_timer.start(QUIT_GRACE_MS)

    def _escalate(self) -> None:
        """Move shutdown to the next, less polite stage."""
        if self._process.state() == QProcess.ProcessState.NotRunning:
            self._stop_stage = 0
            return
        if self._stop_stage == 1:
            self.output.emit("\n[manager] Server did not quit; sending terminate.\n")
            self._process.terminate()
            self._stop_stage = 2
            self._stop_timer.start(TERM_GRACE_MS)
        elif self._stop_stage == 2:
            self.output.emit("\n[manager] Server unresponsive; killing it.\n")
            self._process.kill()
            self._stop_stage = 0

    def restart(self) -> None:
        """Stop, then start the same profile again once it has exited."""
        if self._process.state() == QProcess.ProcessState.NotRunning:
            if self._profile:
                self.start(self._profile)
            return
        self._restarting = True
        self.stop()

    def stop_and_wait(self, timeout_ms: int = QUIT_GRACE_MS + TERM_GRACE_MS) -> None:
        """Blocking shutdown, for application exit.

        Servers are children of the manager, so this runs on close to make sure
        none are left orphaned.
        """
        if self._process.state() == QProcess.ProcessState.NotRunning:
            return
        self._restarting = False
        self._quitting = True
        self._start_timer.stop()
        self._stop_timer.stop()
        try:
            self._write("quit")
        except ChannelError:
            pass
        if not self._process.waitForFinished(QUIT_GRACE_MS):
            self._process.terminate()
            if not self._process.waitForFinished(TERM_GRACE_MS):
                self._process.kill()
                self._process.waitForFinished(1000)
        self._set_state(ServerState.STOPPED)

    # -- Commands -----------------------------------------------------------

    def send(self, command: str, echo: bool = True) -> None:
        """Write one console command to the running server.

        ``echo`` is off for housekeeping the user did not type, so the console
        stays a record of their own commands.
        """
        command = command.strip()
        if not command:
            return
        self._write(command)
        if echo:
            self.output.emit(f"> {command}\n")

    def _write(self, command: str) -> None:
        if self._process.state() != QProcess.ProcessState.Running:
            raise ChannelError("The server is not running.")
        written = self._process.write(f"{command}\n".encode("utf-8", errors="replace"))
        if written == -1:
            raise ChannelError("Could not write to the server console.")

    # -- Process events -----------------------------------------------------

    def _on_output(self) -> None:
        raw = self._process.readAllStandardOutput().data()
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        if not text:
            return

        self.output.emit(text)

        # Keep a bounded tail for pattern matching, so a long-running server
        # does not accumulate its entire console history in memory here.
        self._buffer = (self._buffer + text)[-8000:]

        if self._state is ServerState.STARTING and _INIT_GAME.search(self._buffer):
            self._start_timer.stop()
            self._set_state(ServerState.RUNNING)
            self._buffer = ""

    def _on_start_timeout(self) -> None:
        if self._state is not ServerState.STARTING:
            return
        self.failed.emit(
            "The server did not finish starting in time. Check the console output "
            "for a missing map or a port already in use."
        )
        self.stop()

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._start_timer.stop()
            self._set_state(ServerState.CRASHED)
            self.failed.emit(f"Could not launch {self._binary}.")

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        self._start_timer.stop()
        self._stop_timer.stop()
        self._stop_stage = 0

        expected = self._quitting
        crashed = not expected and (
            exit_code != 0 or status == QProcess.ExitStatus.CrashExit
        )

        if crashed:
            self._set_state(ServerState.CRASHED)
            self.failed.emit(
                f"The server stopped unexpectedly (exit code {exit_code}). "
                "The console output above may say why."
            )
        else:
            self._set_state(ServerState.STOPPED)

        self._quitting = False

        if self._restarting:
            self._restarting = False
            if self._profile:
                QTimer.singleShot(600, lambda: self.start(self._profile))
