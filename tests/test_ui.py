"""Tests for the widgets and their binding to the profile model.

Run offscreen, without launching a server.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utsm import paths  # noqa: E402
from utsm.model import cvars, gear, votes  # noqa: E402
from utsm.model.profile import Profile  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402
from utsm.ui.formbuilder import CVarField, CVarFormPage, escape_mnemonic  # noqa: E402
from utsm.ui.gearpage import GearPage  # noqa: E402
from utsm.ui.mapcyclepage import MapCyclePage  # noqa: E402
from utsm.ui.votespage import VotesPage  # noqa: E402


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(paths, "profiles_file", lambda: tmp_path / "config" / "profiles.json")
    install = paths.find_install()
    if install is None:
        pytest.skip("No Urban Terror installation found")
    from utsm.ui.mainwindow import MainWindow
    w = MainWindow(install)
    yield w
    w.supervisors.clear()
    w.deleteLater()


# -- Field binding -----------------------------------------------------------

def test_bool_field_round_trip(app):
    field = CVarField(cvars.get("sv_pure"))
    field.value = False
    assert field.value is False
    field.value = True
    assert field.value is True


def test_int_field_clamps_to_registry_range(app):
    field = CVarField(cvars.get("g_walljumps"))   # min 3, max 100
    field.value = 1
    assert field.value == 3, "spinbox must not allow a value the engine rejects"
    field.value = 500
    assert field.value == 100


def test_enum_field_maps_labels_to_values(app):
    field = CVarField(cvars.get("g_gametype"))
    field.value = cvars.BOMB
    assert field.value == cvars.BOMB
    assert field.widget.currentText() == "Bomb Mode"


def test_password_field_is_masked(app):
    from PySide6.QtWidgets import QLineEdit
    field = CVarField(cvars.get("rconpassword"))
    assert field.widget.echoMode() == QLineEdit.EchoMode.Password


def test_field_emits_on_user_change_but_not_on_load(app):
    seen = []
    field = CVarField(cvars.get("g_gravity"))
    field.changed.connect(lambda n, v: seen.append((n, v)))

    field.value = 500          # programmatic load must stay silent
    assert seen == []

    field.widget.setValue(300)  # a user edit must be reported
    assert seen == [("g_gravity", 300)]


# -- Form pages --------------------------------------------------------------

def test_form_page_builds_rows_for_its_group(app):
    page = CVarFormPage(cvars.GROUPS_BY_KEY["gameplay"], cvars.CTF)
    assert "g_gravity" in page.fields
    assert "g_knockback" in page.fields
    # Options from other groups must not leak in.
    assert "sv_hostname" not in page.fields


def test_form_page_hides_advanced_until_asked(app):
    basic = CVarFormPage(cvars.GROUPS_BY_KEY["gameplay"], cvars.CTF, show_advanced=False)
    assert "g_speed" not in basic.fields

    advanced = CVarFormPage(cvars.GROUPS_BY_KEY["gameplay"], cvars.CTF, show_advanced=True)
    assert "g_speed" in advanced.fields


def test_form_page_load_populates_fields(app):
    page = CVarFormPage(cvars.GROUPS_BY_KEY["gameplay"], cvars.CTF)
    page.load({"g_gravity": 275, "timelimit": 40})
    assert page.fields["g_gravity"].value == 275
    assert page.fields["timelimit"].value == 40


# -- Ampersands in labels ----------------------------------------------------

def test_ampersands_survive_qt_mnemonics(app):
    """Qt eats a literal '&' as an accelerator: 'H&K G36' would show as 'HK G36'."""
    assert escape_mnemonic("H&K G36") == "H&&K G36"
    assert escape_mnemonic("no ampersand") == "no ampersand"


def test_gear_labels_render_ampersands(app):
    page = GearPage()
    hk = page._boxes["g36"]
    assert hk.text() == "H&&K G36", "the & must be escaped for display"
    # Qt strips one '&' when reporting the displayed text.
    assert "&" in gear.ITEMS_BY_KEY["g36"].label, "the model keeps the real name"


def test_group_titles_with_ampersands_are_escaped(window):
    titles = [window._settings_tabs.tabText(i)
              for i in range(window._settings_tabs.count())]
    assert "Network && Slots" in titles
    assert "Admin && Security" in titles


# -- Gear page ---------------------------------------------------------------

def test_gear_page_inverts_the_cvar(app):
    page = GearPage()
    page.load("K")     # HE grenade disallowed
    assert page._boxes["he"].isChecked() is False
    assert page._boxes["g36"].isChecked() is True


def test_gear_page_emits_updated_cvar(app):
    page = GearPage()
    page.load("")
    seen = []
    page.changed.connect(lambda n, v: seen.append((n, v)))

    page._boxes["negev"].setChecked(False)
    assert seen, "unticking an item must update g_gear"
    name, value = seen[-1]
    assert name == "g_gear"
    assert gear.ITEMS_BY_KEY["negev"].letter in value


def test_gear_page_preserves_unknown_letters(app):
    page = GearPage()
    page.load("K~")
    page._boxes["g36"].setChecked(False)
    assert "~" in page._raw.text(), "unknown letters must survive an edit"


def test_gear_allow_all_clears_the_cvar(app):
    page = GearPage()
    page.load("KFG")
    page._set_all(True)
    assert page._raw.text() == ""


# -- Votes page --------------------------------------------------------------

def test_votes_page_reflects_the_mask(app):
    page = VotesPage()
    page.load(votes.DEFAULT_MASK)
    allowed = votes.decode(votes.DEFAULT_MASK)
    for key, box in page._boxes.items():
        assert box.isChecked() == (key in allowed)


def test_votes_page_emits_updated_mask(app):
    page = VotesPage()
    page.load(0)
    seen = []
    page.changed.connect(lambda n, v: seen.append((n, v)))

    page._boxes["kick"].setChecked(True)
    assert seen[-1] == ("g_allowvote", votes.VOTES_BY_KEY["kick"].mask)


def test_votes_page_rejects_a_non_numeric_raw_value(app):
    page = VotesPage()
    page.load(votes.DEFAULT_MASK)
    page._raw.setText("not a number")
    page._on_raw_edited()
    assert "not a number" in page._summary.text().lower()


def test_votes_page_carries_timing_options(app):
    page = VotesPage()
    page.load(votes.DEFAULT_MASK, {"g_failedVoteTime": 120})
    assert page._timing["g_failedVoteTime"].value == 120


# -- Map cycle page ----------------------------------------------------------

def test_mapcycle_add_and_reorder(app):
    from utsm.model.maps import GameMap
    page = MapCyclePage()
    page.set_available_maps([GameMap(n, Path(".")) for n in
                             ("ut4_casa", "ut4_abbey", "ut4_turnpike")])
    page.load([], "ut4_casa")

    page._available_list.selectAll()
    page._add_selected()
    assert page.cycle == ["ut4_casa", "ut4_abbey", "ut4_turnpike"]

    page._cycle_list.setCurrentRow(2)
    page._move(-1)
    assert page.cycle == ["ut4_casa", "ut4_turnpike", "ut4_abbey"]


def test_mapcycle_warns_when_start_map_is_outside_the_rotation(app):
    from utsm.model.maps import GameMap
    page = MapCyclePage()
    page.set_available_maps([GameMap(n, Path(".")) for n in ("ut4_casa", "ut4_abbey")])
    page.load(["ut4_abbey"], "ut4_casa")
    assert "not in the rotation" in page._status.text()


def test_mapcycle_does_not_duplicate_entries(app):
    from utsm.model.maps import GameMap
    page = MapCyclePage()
    page.set_available_maps([GameMap("ut4_casa", Path("."))])
    page.load(["ut4_casa"], "ut4_casa")
    page._available_list.selectAll()
    page._add_selected()
    assert page.cycle == ["ut4_casa"]


# -- Custom maps and download server -----------------------------------------

def test_download_page_shows_installed_packs(app, tmp_path):
    import zipfile
    from utsm.ui.downloadpage import DownloadPage

    mod = tmp_path / "q3ut4"
    mod.mkdir(parents=True)
    # Named after the map it holds, which is what a client will download.
    with zipfile.ZipFile(mod / "ut4_custom.pk3", "w") as z:
        z.writestr("maps/ut4_custom.bsp", b"BSP\x00")

    page = DownloadPage()
    page.set_profile(Profile(), mod)
    assert page._table.rowCount() == 1
    assert page._table.item(0, 0).text() == "ut4_custom.pk3"
    assert "ut4_custom" in page._table.item(0, 2).text()


def test_download_page_flags_a_pack_clients_cannot_download(app, tmp_path):
    """A misnamed pack is silently dropped by the client, so say so."""
    import zipfile
    from utsm.ui.downloadpage import DownloadPage

    mod = tmp_path / "q3ut4"
    mod.mkdir(parents=True)
    with zipfile.ZipFile(mod / "wrongname.pk3", "w") as z:
        z.writestr("maps/ut4_thing.bsp", b"BSP\x00")

    page = DownloadPage()
    page.set_profile(Profile(), mod)
    assert "⚠" in page._table.item(0, 0).text()
    assert "ut4_thing.pk3" in page._table.item(0, 0).toolTip()
    assert "cannot be auto-downloaded" in page._maps_summary.text()


def test_download_page_builds_the_url_from_profile_fields(app, tmp_path):
    from utsm.ui.downloadpage import DownloadPage

    profile = Profile()
    profile.dl_port = 9123
    profile.dl_host = "play.example.com"
    page = DownloadPage()
    page.set_profile(profile, tmp_path)
    assert page._url.text() == "http://play.example.com:9123"


def test_download_page_note_is_plain_text(app, tmp_path):
    """The example URL contains angle brackets Qt would treat as markup."""
    from utsm.ui.downloadpage import DownloadPage

    page = DownloadPage()
    page.set_profile(Profile(), tmp_path)
    notes = [w.text() for w in page.findChildren(QLabel) if "q3ut4" in w.text()]
    assert notes, "expected the external-host note"
    assert "<b>" not in notes[0] and "&amp;" not in notes[0]


def test_profile_warns_when_downloads_are_disabled_but_server_is_on():
    profile = Profile()
    profile.dl_enabled = True
    profile.set("sv_allowdownload", False)
    assert any("never fetch anything" in p for p in profile.problems())


def test_profile_download_fields_round_trip():
    profile = Profile()
    profile.dl_enabled = True
    profile.dl_port = 8123
    profile.dl_host = "example.com"
    restored = Profile.from_dict(profile.to_dict())
    assert restored.dl_enabled is True
    assert restored.dl_port == 8123
    assert restored.dl_host == "example.com"


# -- Main window -------------------------------------------------------------

def test_window_starts_with_a_profile(window):
    assert window.store.profiles
    assert window._current is not None


def test_settings_tabs_track_the_gametype(window):
    def tabs():
        return [window._settings_tabs.tabText(i)
                for i in range(window._settings_tabs.count())]

    profile = window._current
    profile.set("g_gametype", cvars.CTF)
    window._rebuild_settings_tabs(profile)
    assert "Capture the Flag" in tabs()
    assert "Bomb Mode" not in tabs()

    profile.set("g_gametype", cvars.BOMB)
    window._rebuild_settings_tabs(profile)
    assert "Bomb Mode" in tabs()
    assert "Capture the Flag" not in tabs()


def test_composite_groups_do_not_appear_twice(window):
    """Weapons, voting and the map cycle have their own top-level tabs."""
    settings = {window._settings_tabs.tabText(i)
                for i in range(window._settings_tabs.count())}
    assert "Voting" not in settings
    assert "Weapons & Items" not in settings
    assert "Map Cycle" not in settings

    top = {window._tabs.tabText(i).replace("&&", "&")
           for i in range(window._tabs.count())}
    assert {"Voting", "Weapons & Items", "Map Cycle"} <= top


def test_no_settings_tab_is_empty(window):
    for i in range(window._settings_tabs.count()):
        page = window._settings_tabs.widget(i)
        assert page.fields, f"tab '{window._settings_tabs.tabText(i)}' has no options"


def test_editing_a_field_updates_the_profile(window):
    profile = window._current
    window._on_value_changed("g_gravity", 350)
    assert profile.get("g_gravity") == 350


def test_editing_gametype_rebuilds_tabs(window):
    window._on_value_changed("g_gametype", cvars.JUMP)
    tabs = [window._settings_tabs.tabText(i)
            for i in range(window._settings_tabs.count())]
    assert "Jump Training" in tabs


def test_every_registry_option_is_reachable_in_some_gametype(window):
    """The promise is that every server option has a GUI control."""
    reachable: set[str] = set()

    for gametype, _ in cvars.GAMETYPES:
        for group in cvars.visible_groups(gametype):
            page = CVarFormPage(group, gametype, show_advanced=True)
            reachable.update(page.fields)
            page.deleteLater()

    # Plus the composite editors and the fields they carry.
    reachable.update({"g_gear", "g_allowvote", "g_mapcycle"})
    reachable.update(window._votes._timing)

    missing = {c.name for c in cvars.REGISTRY} - reachable
    assert not missing, f"options with no GUI control: {sorted(missing)}"


# -- Download server lifecycle -----------------------------------------------

def _listening(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def test_download_server_survives_a_server_restart(app, window):
    """The regression: restarting the game server killed the download server.

    A restart goes through the supervisor directly and never passes back
    through the Start button, so the download server was stopped on the way
    through STOPPED and never brought back. The game server carried on
    advertising sv_dlURL, and joining players saw a missing map.
    """
    import time
    from utsm.core.supervisor import ServerState

    profile = window._current
    profile.set("net_port", 27992)
    profile.set("dedicated", 1)
    profile.set("sv_pure", False)
    profile.dl_enabled = True
    profile.dl_port = 8057
    profile.start_map = "ut4_casa"
    profile.mapcycle = ["ut4_casa"]
    window._load_profile(profile)

    def spin(pred, timeout=60.0):
        end = time.time() + timeout
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)
            if pred():
                return True
        return False

    try:
        window._start()
        assert spin(lambda: window._state_of(profile) is ServerState.RUNNING)
        assert _listening(8057), "download server should be up with the game server"

        window._restart()
        assert spin(lambda: window._state_of(profile) is ServerState.RUNNING
                    and not window.supervisors[profile.id].is_restarting)
        app.processEvents()
        assert _listening(8057), "download server must survive a restart"
    finally:
        for s in window.download_servers.values():
            s.stop()
        for s in window.supervisors.values():
            s.stop_and_wait()


def test_download_server_stops_with_the_game_server(app, window):
    import time
    from utsm.core.supervisor import ServerState

    profile = window._current
    profile.set("net_port", 27991)
    profile.set("dedicated", 1)
    profile.set("sv_pure", False)
    profile.dl_enabled = True
    profile.dl_port = 8058
    profile.start_map = "ut4_casa"
    window._load_profile(profile)

    def spin(pred, timeout=60.0):
        end = time.time() + timeout
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)
            if pred():
                return True
        return False

    try:
        window._start()
        assert spin(lambda: window._state_of(profile) is ServerState.RUNNING)
        assert _listening(8058)

        window._stop()
        assert spin(lambda: window._state_of(profile) is ServerState.STOPPED)
        app.processEvents()
        assert not _listening(8058), "download server should not outlive the game server"
    finally:
        for s in window.download_servers.values():
            s.stop()
        for s in window.supervisors.values():
            s.stop_and_wait()


def test_verify_downloads_warns_when_server_reports_downloads_off(app, window, monkeypatch):
    """Regression: this path referenced an unimported module and raised NameError.

    It also has to actually warn -- a server that quietly refuses downloads
    leaves players unable to join a custom map, and the client only reports a
    missing .bsp.
    """
    from utsm.core import query as query_mod

    profile = window._current
    profile.dl_enabled = True

    fake = query_mod.ServerStatus(info={"sv_allowdownload": "0"}, players=())
    monkeypatch.setattr(query_mod, "get_status", lambda *a, **k: fake)

    logged: list[str] = []
    monkeypatch.setattr(window._downloads, "log_activity", logged.append)

    window._verify_downloads(profile)

    joined = " ".join(logged)
    assert "sv_allowdownload" in joined, "must report the server refusing downloads"
    assert "cl_allowdownload" in joined, "must mention the client-side requirement"


def test_verify_downloads_is_quiet_when_the_server_is_unreachable(app, window, monkeypatch):
    from utsm.core import query as query_mod

    profile = window._current
    profile.dl_enabled = True
    monkeypatch.setattr(
        query_mod, "get_status",
        lambda *a, **k: (_ for _ in ()).throw(query_mod.QueryError("no server")),
    )
    logged: list[str] = []
    monkeypatch.setattr(window._downloads, "log_activity", logged.append)
    window._verify_downloads(profile)      # must not raise
    assert logged == []


def test_verify_downloads_skipped_when_disabled(app, window, monkeypatch):
    profile = window._current
    profile.dl_enabled = False
    logged: list[str] = []
    monkeypatch.setattr(window._downloads, "log_activity", logged.append)
    window._verify_downloads(profile)
    assert logged == []
