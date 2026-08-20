"""Lifecycle tests for the server supervisor, against a real server.

These drive a Qt event loop and launch actual dedicated servers, so they are
skipped without an installation. They cover the promise the manager makes:
start, stop, restart, and no orphaned processes when the app exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utsm import paths  # noqa: E402
from utsm.core import query  # noqa: E402
from utsm.core.channel import ChannelError  # noqa: E402
from utsm.core.supervisor import ServerState, ServerSupervisor  # noqa: E402
from utsm.model import cvars  # noqa: E402
from utsm.model.profile import Profile  # noqa: E402

INSTALL = paths.find_install()
NEEDS_GAME = pytest.mark.skipif(INSTALL is None, reason="No Urban Terror installation found")

BASE_PORT = 27971


@pytest.fixture
def make_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    counter = {"n": 0}

    def _make(**overrides) -> Profile:
        counter["n"] += 1
        p = Profile(name=f"Supervisor Test {counter['n']}")
        p.set("sv_hostname", f"Supervisor Test {counter['n']}")
        p.set("net_port", BASE_PORT + counter["n"])
        p.set("dedicated", 1)          # LAN only: never announce a test server
        p.set("sv_pure", False)
        p.start_map = "ut4_casa"
        p.mapcycle = ["ut4_casa"]
        for key, value in overrides.items():
            p.set(key, value)
        return p

    return _make


def spin(app, predicate, timeout=45.0, interval=0.05):
    """Run the event loop until ``predicate`` holds or time runs out."""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    app.processEvents()
    return predicate()


@pytest.fixture
def supervisor(app):
    sup = ServerSupervisor(INSTALL.basepath, INSTALL.server_binary)
    yield sup
    sup.stop_and_wait()
    app.processEvents()


# -- Lifecycle ---------------------------------------------------------------

@NEEDS_GAME
def test_start_reaches_running(app, supervisor, make_profile):
    profile = make_profile()
    assert supervisor.start(profile)
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING), \
        f"stuck in {supervisor.state}"
    assert query.is_alive("127.0.0.1", profile.net_port, timeout=2.0)


@NEEDS_GAME
def test_stop_reaches_stopped_and_frees_the_port(app, supervisor, make_profile):
    profile = make_profile()
    supervisor.start(profile)
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING)

    supervisor.stop()
    assert spin(app, lambda: supervisor.state is ServerState.STOPPED), \
        f"stuck in {supervisor.state}"
    assert not query.is_alive("127.0.0.1", profile.net_port, timeout=1.0)


@NEEDS_GAME
def test_stopping_is_not_reported_as_a_crash(app, supervisor, make_profile):
    """A clean 'quit' must not raise a crash warning at the user."""
    failures: list[str] = []
    supervisor.failed.connect(failures.append)

    supervisor.start(make_profile())
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING)
    supervisor.stop()
    assert spin(app, lambda: supervisor.state is ServerState.STOPPED)

    assert not failures, f"clean shutdown reported failures: {failures}"


@NEEDS_GAME
def test_restart_comes_back_up(app, supervisor, make_profile):
    profile = make_profile()
    supervisor.start(profile)
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING)

    supervisor.restart()
    assert spin(app, lambda: supervisor.state is ServerState.STOPPED
                or supervisor.state is ServerState.STARTING)
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING), \
        f"did not come back up, stuck in {supervisor.state}"
    assert query.is_alive("127.0.0.1", profile.net_port, timeout=2.0)


@NEEDS_GAME
def test_restart_is_distinguishable_from_a_stop(app, supervisor, make_profile):
    """A restart passes through STOPPED on its way back up.

    Anything tied to the server's lifetime -- the map download server, in
    practice -- has to tell that apart from a real stop, or it tears itself
    down mid-restart and never returns.
    """
    seen: list[tuple[str, bool]] = []
    supervisor.state_changed.connect(
        lambda s: seen.append((s.value, supervisor.is_restarting))
    )

    supervisor.start(make_profile())
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING)
    assert supervisor.is_restarting is False

    seen.clear()
    supervisor.restart()
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING and not supervisor.is_restarting)

    stopped = [flag for state, flag in seen if state == "stopped"]
    assert stopped, "a restart should pass through the stopped state"
    assert all(stopped), "is_restarting must be set while passing through stopped"
    assert supervisor.is_restarting is False, "cleared once back up"


@NEEDS_GAME
def test_stop_and_wait_leaves_no_orphan(app, make_profile):
    """Servers are children of the manager and must not outlive it."""
    profile = make_profile()
    sup = ServerSupervisor(INSTALL.basepath, INSTALL.server_binary)
    sup.start(profile)
    assert spin(app, lambda: sup.state is ServerState.RUNNING)

    sup.stop_and_wait()
    app.processEvents()

    assert sup.state is ServerState.STOPPED
    # Nothing should still be answering on that port.
    assert not query.is_alive("127.0.0.1", profile.net_port, timeout=1.0)


# -- Console -----------------------------------------------------------------

@NEEDS_GAME
def test_console_output_is_streamed(app, supervisor, make_profile):
    chunks: list[str] = []
    supervisor.output.connect(chunks.append)

    supervisor.start(make_profile())
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING)

    combined = "".join(chunks)
    assert "InitGame:" in combined, "did not see the gamecode start"
    assert "Supervisor Test" in combined, "config settings not reflected in output"


@NEEDS_GAME
def test_commands_reach_the_server(app, supervisor, make_profile):
    profile = make_profile()
    supervisor.start(profile)
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING)

    supervisor.channel.change_map("ut4_abbey")

    def switched():
        app.processEvents()
        try:
            return query.get_status("127.0.0.1", profile.net_port, 0.5).mapname == "ut4_abbey"
        except query.QueryError:
            return False

    assert spin(app, switched, timeout=45), "map change never took effect"


@NEEDS_GAME
def test_live_cvar_change_is_visible_over_the_wire(app, supervisor, make_profile):
    profile = make_profile()
    supervisor.start(profile)
    assert spin(app, lambda: supervisor.state is ServerState.RUNNING)

    supervisor.channel.set_cvar("timelimit", "17")

    def applied():
        app.processEvents()
        try:
            return query.get_status("127.0.0.1", profile.net_port, 0.5).info.get("timelimit") == "17"
        except query.QueryError:
            return False

    assert spin(app, applied, timeout=20), "live cvar change did not reach the server"


def test_sending_to_a_stopped_server_is_refused(app):
    sup = ServerSupervisor(Path("/nonexistent"), Path("/nonexistent/urbanterror-ded"))
    with pytest.raises(ChannelError):
        sup.send("say hello")
    assert not sup.channel.available


def test_missing_binary_is_reported_not_crashed(app, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    failures: list[str] = []
    sup = ServerSupervisor(tmp_path, tmp_path / "does-not-exist")
    sup.failed.connect(failures.append)

    assert sup.start(Profile()) is False
    assert failures and "not found" in failures[0]
    assert sup.state is ServerState.STOPPED


# -- Command escaping --------------------------------------------------------

def test_channel_escapes_command_injection(app):
    """A crafted map name or message must not chain a second console command."""
    sent: list[str] = []

    class Recorder(ServerSupervisor):
        def send(self, command: str, echo: bool = True) -> None:
            sent.append(command)

    sup = Recorder(Path("/tmp"), Path("/tmp/x"))
    sup.channel.say('hello"; quit; say "bye')
    sup.channel.change_map("ut4_casa; quit")

    assert all(";" not in c for c in sent), f"semicolon survived escaping: {sent}"
    assert sent[0].count('"') == 2, "say argument must stay one quoted string"
    assert sent[1] == "map ut4_casa,"


def test_quiet_commands_are_not_echoed_to_the_console(app):
    """The player list polls 'status'; that must not fill the user's console."""
    emitted: list[str] = []
    sup = ServerSupervisor(Path("/tmp"), Path("/tmp/x"))
    sup.output.connect(emitted.append)

    # Bypass the running-process check; only the echo behaviour is under test.
    sup._write = lambda command: None

    sup.send("status", echo=False)
    assert emitted == [], "a quiet command must not be echoed"

    sup.send("status")
    assert emitted == ["> status\n"], "a user command must be echoed"
