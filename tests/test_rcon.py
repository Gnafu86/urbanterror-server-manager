"""Rcon tests against a real server.

Remote servers are managed entirely over rcon, so these run a genuine server
with an rcon password and drive it the same way the app would drive one on
another machine.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utsm import paths  # noqa: E402
from utsm.core import cfgwriter, query  # noqa: E402
from utsm.core.channel import ChannelError, RconChannel  # noqa: E402
from utsm.model import cvars  # noqa: E402
from utsm.model.profile import REMOTE, Profile  # noqa: E402

INSTALL = paths.find_install()
NEEDS_GAME = pytest.mark.skipif(INSTALL is None, reason="No Urban Terror installation found")

RCON_PORT = 27981
RCON_PASSWORD = "utsm-test-password"


@pytest.fixture
def rcon_server(tmp_path, monkeypatch):
    """A real server with rcon enabled, and a channel pointed at it."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")

    profile = Profile(name="Rcon Test")
    profile.set("sv_hostname", "Rcon Test Server")
    profile.set("net_port", RCON_PORT)
    profile.set("dedicated", 1)          # LAN only
    profile.set("sv_pure", False)
    profile.set("rconpassword", RCON_PASSWORD)
    profile.set("g_gametype", cvars.CTF)
    profile.start_map = "ut4_casa"

    cfgwriter.write(profile)
    args = cfgwriter.launch_args(profile, INSTALL.basepath)
    server = subprocess.Popen(
        [str(INSTALL.server_binary), *args],
        cwd=str(INSTALL.basepath),
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, text=True,
    )

    end = time.time() + 40
    while time.time() < end:
        if query.is_alive("127.0.0.1", RCON_PORT, timeout=0.5):
            break
        time.sleep(0.4)
    else:
        server.kill()
        pytest.fail("server did not start")

    yield RconChannel("127.0.0.1", RCON_PORT, RCON_PASSWORD, timeout=3.0)

    server.kill()
    server.wait(timeout=10)


@NEEDS_GAME
def test_rcon_runs_a_command_and_returns_the_reply(rcon_server):
    reply = rcon_server.send("serverinfo")
    assert reply is not None
    assert "sv_hostname" in reply
    assert "Rcon Test Server" in reply


@NEEDS_GAME
def test_rcon_rejects_a_wrong_password(rcon_server):
    wrong = RconChannel("127.0.0.1", RCON_PORT, "definitely-not-the-password", timeout=3.0)
    with pytest.raises(ChannelError):
        wrong.send("serverinfo")


@NEEDS_GAME
def test_rcon_status_yields_parseable_client_numbers(rcon_server):
    reply = rcon_server.send("status")
    assert reply is not None
    assert "num score ping name" in reply
    # An empty server parses to no clients rather than raising.
    assert query.parse_status(reply) == []


@NEEDS_GAME
def test_rcon_changes_a_cvar_on_the_running_server(rcon_server):
    rcon_server.set_cvar("timelimit", "23")

    end = time.time() + 15
    while time.time() < end:
        status = query.get_status("127.0.0.1", RCON_PORT, timeout=1.0)
        if status.info.get("timelimit") == "23":
            break
        time.sleep(0.5)
    else:
        pytest.fail("rcon cvar change was not applied")


@NEEDS_GAME
def test_rcon_changes_the_map(rcon_server):
    rcon_server.change_map("ut4_abbey")

    end = time.time() + 40
    while time.time() < end:
        try:
            if query.get_status("127.0.0.1", RCON_PORT, timeout=1.0).mapname == "ut4_abbey":
                break
        except query.QueryError:
            pass
        time.sleep(0.5)
    else:
        pytest.fail("rcon map change did not take effect")


def test_rcon_without_a_password_is_refused():
    channel = RconChannel("127.0.0.1", 27960, "")
    assert not channel.available
    with pytest.raises(ChannelError):
        channel.send("status")


def test_rcon_reports_an_unreachable_host():
    # Port 1 has nothing on it, so this must fail cleanly rather than hang.
    channel = RconChannel("127.0.0.1", 1, "x", timeout=0.5)
    with pytest.raises(ChannelError):
        channel.send("status")


def test_remote_profile_requires_a_password():
    profile = Profile(name="Remote", kind=REMOTE)
    profile.host, profile.port = "example.com", 27960
    assert any("rcon password" in p for p in profile.problems())

    profile.rcon_password = "secret"
    assert not any("rcon password" in p for p in profile.problems())


def test_remote_profile_reports_its_own_port():
    profile = Profile(name="Remote", kind=REMOTE)
    profile.port = 29070
    assert profile.net_port == 29070
