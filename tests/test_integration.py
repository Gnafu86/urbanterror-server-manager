"""End-to-end tests against the real Urban Terror dedicated server.

These launch an actual server on a high port, so they are skipped when no
installation is present. They are the tests that matter most: they prove the
generated config is found and honoured, which is the whole basis of the
manager's design.
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
from utsm.model import cvars, maps  # noqa: E402
from utsm.model.profile import Profile  # noqa: E402

INSTALL = paths.find_install()
NEEDS_GAME = pytest.mark.skipif(INSTALL is None, reason="No Urban Terror installation found")

#: A port well clear of the default range so a real server is never disturbed.
TEST_PORT = 27987


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """A profile whose files land in a temporary directory."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile(name="UTSM Integration Test")
    p.set("sv_hostname", "UTSM Integration Test")
    p.set("net_port", TEST_PORT)
    p.set("dedicated", 1)          # LAN only: never announce a test server
    p.set("sv_pure", False)
    p.set("g_gametype", cvars.BOMB)
    p.set("sv_maxclients", 14)
    p.set("timelimit", 13)
    p.set("g_gravity", 640)
    p.start_map = "ut4_casa"
    p.mapcycle = ["ut4_casa"]
    return p


def _launch(profile) -> subprocess.Popen:
    cfgwriter.write(profile)
    args = cfgwriter.launch_args(profile, INSTALL.basepath)
    return subprocess.Popen(
        [str(INSTALL.server_binary), *args],
        cwd=str(INSTALL.basepath),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _wait_until_up(port: int, deadline: float = 25.0) -> bool:
    end = time.time() + deadline
    while time.time() < end:
        if query.is_alive("127.0.0.1", port, timeout=0.5):
            return True
        time.sleep(0.4)
    return False


# -- Install discovery -------------------------------------------------------

@NEEDS_GAME
def test_install_is_discovered():
    assert INSTALL.server_binary.is_file()
    assert INSTALL.mod_path.is_dir()
    assert INSTALL.pk3_files(), "no .pk3 data files found in the install"


@NEEDS_GAME
def test_maps_are_discovered():
    found = maps.discover(INSTALL.mod_path)
    names = maps.names(found)
    assert len(names) >= 20, f"expected the stock map set, found {len(names)}"
    for expected in ("ut4_casa", "ut4_turnpike", "ut4_abbey"):
        assert expected in names
    # The entity pack is a .bsp but is not playable and must be filtered out.
    assert "ut4_jumpents" not in names


# -- Config generation -------------------------------------------------------

def test_generated_cfg_quotes_and_sanitises(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile(name="Quoting")
    # A hostname containing a quote and a semicolon would otherwise break out
    # of the directive and inject a console command.
    p.set("sv_hostname", 'Evil " ; quit ; server')
    text = cfgwriter.render_cfg(p)
    hostname_line = next(l for l in text.splitlines() if l.startswith("set sv_hostname"))
    assert hostname_line.count('"') == 2, "value must stay inside one quoted string"
    assert ";" not in hostname_line


def test_generated_cfg_quotes_names_with_a_leading_space(tmp_path, monkeypatch):
    """Written bare, ``sets  Admin "x"`` sets a cvar called 'Admin', not ' Admin'."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.set(" Admin", "serveradmin")
    line = next(l for l in cfgwriter.render_cfg(p).splitlines() if "Admin" in l and "//" not in l)
    assert line == 'sets " Admin" "serveradmin"'

    # And it survives a round-trip back through the importer.
    assert Profile.from_cfg(cfgwriter.render_cfg(p)).get(" Admin") == "serveradmin"


def test_generated_cfg_omits_irrelevant_gametype_options(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.set("g_gametype", cvars.CTF)
    text = cfgwriter.render_cfg(p)
    assert "g_flagreturntime" in text        # CTF option, belongs
    assert "g_bombdefusetime" not in text    # Bomb option, does not
    assert "g_thawTime" not in text          # Freeze Tag option, does not


def test_generated_cfg_ends_with_start_map(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.start_map = "ut4_abbey"
    assert cfgwriter.render_cfg(p).strip().endswith("map ut4_abbey")


def test_write_produces_cfg_and_mapcycle(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.mapcycle = ["ut4_casa", "ut4_abbey"]
    cfg_path = cfgwriter.write(p)
    assert cfg_path.is_file()
    cycle = cfg_path.parent / cfgwriter.mapcycle_filename(p)
    assert cycle.is_file()
    assert maps.parse_cycle(cycle.read_text()) == ["ut4_casa", "ut4_abbey"]


def test_launch_args_point_at_profile_homepath(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.set("net_port", 27999)
    basepath = Path("/opt/urbanterror")
    args = cfgwriter.launch_args(p, basepath)
    joined = " ".join(args)
    # Compare against the rendered Path, not a literal: the separator differs
    # by platform and the assertion is about the argument, not about POSIX.
    assert f"+set fs_basepath {basepath}" in joined
    assert str(paths.profile_home(p.id)) in joined
    assert "+set net_port 27999" in joined
    assert f"+exec {cfgwriter.cfg_filename(p)}" in joined


# -- The real thing ----------------------------------------------------------

@NEEDS_GAME
def test_server_starts_and_honours_generated_config(profile):
    """The load-bearing test.

    The game install is root-owned and unwritable, so the config is written to
    the profile's own fs_homepath. This proves the engine finds it there and
    applies every setting.
    """
    server = _launch(profile)
    try:
        assert _wait_until_up(TEST_PORT), "server did not come up in time"

        status = query.get_status("127.0.0.1", TEST_PORT)
        assert status.hostname == "UTSM Integration Test"
        assert status.gametype == cvars.BOMB
        assert status.max_clients == 14
        assert status.mapname == "ut4_casa"
        assert status.info.get("timelimit") == "13"
        assert status.player_count == 0
    finally:
        server.kill()
        server.wait(timeout=10)


@NEEDS_GAME
def test_server_accepts_console_commands_on_stdin(profile):
    """Local control runs over the child's stdin, so prove the channel works."""
    profile.set("net_port", TEST_PORT + 1)
    server = _launch(profile)
    try:
        assert _wait_until_up(TEST_PORT + 1), "server did not come up in time"

        # Change a live cvar through the console and read it back off the wire.
        server.stdin.write("set g_gravity 250\n")
        server.stdin.flush()
        time.sleep(1.0)

        server.stdin.write("quit\n")
        server.stdin.flush()
        server.wait(timeout=15)
        assert server.returncode is not None, "server did not exit on 'quit'"
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=10)


@NEEDS_GAME
def test_custom_map_download_chain(profile, tmp_path):
    """The whole custom-map path, end to end.

    A .pk3 added to a profile has to do two things: be visible to the game
    server as a loadable map, and be fetchable by a joining client at exactly
    the URL the engine tells it to use.
    """
    import urllib.request
    import zipfile

    from utsm.core import httpd
    from utsm.model import maps as maps_model

    # A custom pack, built the way a mapper would ship one.
    source = tmp_path / "ut4_utsmtest.pk3"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("maps/ut4_utsmtest.bsp", b"BSP\x00" + b"x" * 4096)

    mod_dir = paths.profile_mod_dir(profile.id)
    installed = maps_model.install_pk3(source, mod_dir)

    # 1. The game server can see it: discovery covers the profile's homepath,
    #    which is the directory the engine searches first.
    found = maps_model.names(maps_model.discover(INSTALL.mod_path, mod_dir))
    assert "ut4_utsmtest" in found

    # 2. The download server offers it.
    dl = httpd.DownloadServer()
    dl_port = TEST_PORT + 20
    assert dl.start(paths.profile_home(profile.id), dl_port)

    profile.set("net_port", TEST_PORT + 3)
    profile.set("sv_allowdownload", True)
    profile.set("sv_dlURL", f"http://127.0.0.1:{dl_port}")

    server = _launch(profile)
    try:
        assert _wait_until_up(TEST_PORT + 3), "server did not come up in time"

        # 3. The server advertises the download URL to clients.
        status = query.get_status("127.0.0.1", TEST_PORT + 3)
        assert status.info.get("sv_dlURL") == f"http://127.0.0.1:{dl_port}"

        # 4. A client following that URL gets the pack. The engine reports paks
        #    as "q3ut4/<name>", so this is the exact request a client makes.
        url = f"http://127.0.0.1:{dl_port}/q3ut4/{installed.name}"
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.status == 200
            assert response.read() == installed.read_bytes()
    finally:
        dl.stop()
        server.kill()
        server.wait(timeout=10)


def test_download_url_is_generated_not_stored(tmp_path, monkeypatch):
    """A stored URL outlives the server it points at.

    The failure it caused: the download server stopped, sv_dlURL kept being
    advertised, and joining players saw a missing map with no explanation.
    """
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.dl_enabled = True
    p.dl_host = "play.example.com"
    p.dl_port = 8123

    line = next(l for l in cfgwriter.render_cfg(p).splitlines() if "sv_dlURL" in l)
    assert line == 'sets sv_dlURL "http://play.example.com:8123"'
    # Nothing was written back to the profile.
    assert "sv_dlURL" not in p.settings


def test_download_url_follows_a_changed_port(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.dl_enabled = True
    p.dl_host = "example.com"
    p.dl_port = 8000
    assert 'sets sv_dlURL "http://example.com:8000"' in cfgwriter.render_cfg(p)
    p.dl_port = 9999
    assert 'sets sv_dlURL "http://example.com:9999"' in cfgwriter.render_cfg(p)


def test_manual_download_url_is_kept_when_builtin_server_is_off(tmp_path, monkeypatch):
    """Using an external web host must still work."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.dl_enabled = False
    p.set("sv_dlURL", "maps.example.org/urt")
    line = next(l for l in cfgwriter.render_cfg(p).splitlines() if "sv_dlURL" in l)
    assert line == 'sets sv_dlURL "maps.example.org/urt"'


def test_stale_download_url_is_dropped_on_load():
    """Profiles written by earlier versions carry a stored URL."""
    stored = {
        "id": "abc", "name": "Old", "dl_enabled": True, "dl_port": 8000,
        "settings": {"sv_dlURL": "http://192.0.2.10:8000", "g_gravity": 400},
    }
    p = Profile.from_dict(stored)
    assert "sv_dlURL" not in p.settings, "the stale URL must not survive"
    assert p.get("g_gravity") == 400, "other settings are untouched"


def test_stored_download_url_kept_when_builtin_server_is_off():
    stored = {
        "id": "abc", "name": "Old", "dl_enabled": False,
        "settings": {"sv_dlURL": "maps.example.org"},
    }
    assert Profile.from_dict(stored).get("sv_dlURL") == "maps.example.org"


@NEEDS_GAME
def test_status_query_reports_map_change(profile):
    profile.set("net_port", TEST_PORT + 2)
    server = _launch(profile)
    try:
        assert _wait_until_up(TEST_PORT + 2), "server did not come up in time"
        assert query.get_status("127.0.0.1", TEST_PORT + 2).mapname == "ut4_casa"

        server.stdin.write("map ut4_abbey\n")
        server.stdin.flush()

        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if query.get_status("127.0.0.1", TEST_PORT + 2).mapname == "ut4_abbey":
                    break
            except query.QueryError:
                pass
            time.sleep(0.5)
        else:
            pytest.fail("map change was not reflected in the status query")
    finally:
        server.kill()
        server.wait(timeout=10)


def test_allowdownload_goes_on_the_command_line(tmp_path, monkeypatch):
    """A config assignment is silently discarded by this build.

    Measured against the real server: '+set sv_allowdownload 1' yields 1, while
    the same assignment in the config yields 0 -- with no error either way. It
    gates client downloads, so getting it wrong leaves players unable to join a
    custom map.
    """
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.set("sv_allowdownload", True)

    joined = " ".join(cfgwriter.launch_args(p, Path("/opt/urbanterror")))
    assert "+set sv_allowdownload 1" in joined

    # And it must not be written to the config, where it would do nothing.
    assert "sv_allowdownload" not in cfgwriter.render_cfg(p)


def test_allowdownload_off_is_passed_through(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.set("sv_allowdownload", False)
    assert "+set sv_allowdownload 0" in " ".join(
        cfgwriter.launch_args(p, Path("/opt/urbanterror"))
    )


def test_download_url_carries_the_scheme(tmp_path, monkeypatch):
    """The client prepends http:// itself.

    server_example.cfg describes an sv_dlURL of 'yoursite.com/maps' as being
    fetched from 'http://www.yoursite.com/maps/q3ut4/<map>.pk3'. Including the
    scheme here yields 'http://http://host:port/...' and the download fails.
    Every public server observed omits it as well.
    """
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    p = Profile()
    p.dl_enabled = True
    p.dl_host = "example.com"
    p.dl_port = 8000

    # The engine does va("%s/%s", sv_dlURL, remoteName) and passes the result
    # straight to CURLOPT_URL, so the scheme has to be here.
    assert cfgwriter.download_url(p) == "http://example.com:8000"
