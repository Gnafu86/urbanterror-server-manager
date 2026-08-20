"""Unit tests for the data models.

These run without Qt and without launching a server.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utsm.model import cvars, gear, maps, votes  # noqa: E402
from utsm.model.profile import Profile, ProfileStore, serverinfo_length  # noqa: E402


# -- Registry ----------------------------------------------------------------

def test_registry_names_are_unique():
    names = [c.name.lower() for c in cvars.REGISTRY]
    assert len(names) == len(set(names)), "duplicate cvar in registry"


def test_registry_lookup_is_case_insensitive():
    # The engine treats cvar names case-insensitively; so must we, because
    # server_example.cfg and the binary disagree on casing for several cvars.
    assert cvars.get("G_GRAVITY") is cvars.get("g_gravity")
    assert cvars.get("g_allowchat") is not None
    assert cvars.get("g_roundtime") is not None


def test_cvars_with_a_leading_space_are_reachable():
    """' Admin' and ' Email' are written with a deliberate leading space so they
    sort to the top of the server browser's details."""
    admin = cvars.get(" Admin")
    assert admin is not None
    assert admin.name == " Admin", "the real name keeps its leading space"
    assert cvars.get("admin") is admin, "lookup normalises whitespace and case"
    assert cvars.get(" Email") is not None


def test_enum_defaults_are_valid_choices():
    for cvar in cvars.REGISTRY:
        if cvar.kind is cvars.Kind.ENUM:
            allowed = [value for value, _ in cvar.choices]
            assert cvar.default in allowed, f"{cvar.name} default not among its choices"


def test_int_defaults_are_within_range():
    for cvar in cvars.REGISTRY:
        if cvar.kind is not cvars.Kind.INT:
            continue
        if cvar.minimum is not None:
            assert cvar.default >= cvar.minimum, f"{cvar.name} default below minimum"
        if cvar.maximum is not None:
            assert cvar.default <= cvar.maximum, f"{cvar.name} default above maximum"


def test_every_cvar_belongs_to_a_real_group():
    for cvar in cvars.REGISTRY:
        assert cvar.group in cvars.GROUPS_BY_KEY, f"{cvar.name} has unknown group"


def test_gametype_filtering_hides_irrelevant_options():
    bomb_only = cvars.get("g_bombdefusetime")
    assert bomb_only.applies_to(cvars.BOMB)
    assert not bomb_only.applies_to(cvars.CTF)

    ctf_only = cvars.get("g_flagreturntime")
    assert ctf_only.applies_to(cvars.CTF)
    assert not ctf_only.applies_to(cvars.BOMB)

    # Core gameplay options apply everywhere.
    assert cvars.get("g_gravity").applies_to(cvars.JUMP)


def test_visible_groups_differ_by_gametype():
    ctf = {g.key for g in cvars.visible_groups(cvars.CTF)}
    bomb = {g.key for g in cvars.visible_groups(cvars.BOMB)}
    assert "ctf" in ctf and "ctf" not in bomb
    assert "bomb" in bomb and "bomb" not in ctf
    # Rounds apply to Bomb but not CTF; flags the other way round.
    assert "rounds" in bomb and "rounds" not in ctf
    assert "flags" in ctf and "flags" not in bomb


# -- Gear --------------------------------------------------------------------

def test_gear_letters_are_unique():
    letters = [i.letter for i in gear.ITEMS]
    assert len(letters) == len(set(letters))
    assert all(letters), "every gear item needs a letter"


def test_gear_matches_published_mapping():
    # These letters are the long-published ones for Urban Terror and are the
    # check that the index offset in gear.py is right.
    assert gear.ITEMS_BY_KEY["hk69"].letter == "F"
    assert gear.ITEMS_BY_KEY["lr300"].letter == "G"
    assert gear.ITEMS_BY_KEY["g36"].letter == "H"
    assert gear.ITEMS_BY_KEY["he"].letter == "K"
    assert gear.ITEMS_BY_KEY["vest"].letter == "N"
    assert gear.ITEMS_BY_KEY["laser"].letter == "R"
    assert gear.ITEMS_BY_KEY["ammo"].letter == "T"


def test_gear_round_trip():
    for value in ("", "F", "FGH", "NRT", "KLM"):
        assert gear.encode(gear.decode(value)) == "".join(sorted(value))


def test_gear_preserves_unknown_letters():
    # A config from a newer build might carry letters this table lacks; they
    # must survive editing rather than silently re-enabling equipment.
    weird = "F~"
    assert gear.unknown_letters(weird) == "~"
    out = gear.encode(gear.decode(weird), preserve=gear.unknown_letters(weird))
    assert "~" in out and "F" in out


def test_gear_describe():
    assert "All weapons" in gear.describe("")
    assert "HE Grenade" in gear.describe("K")


# -- Votes -------------------------------------------------------------------

def test_vote_bits_are_sequential_and_unique():
    assert [v.bit for v in votes.VOTES] == list(range(len(votes.VOTES)))


def test_vote_default_mask_decodes_sensibly():
    allowed = votes.decode(votes.DEFAULT_MASK)
    # The shipped default permits ordinary map and player management...
    for key in ("reload", "restart", "map", "nextmap", "kick", "timelimit"):
        assert key in allowed, f"{key} should be allowed by default"
    # ...but not the ones that hand over broad control.
    for key in ("exec", "g_matchMode", "shuffleteams"):
        assert key not in allowed, f"{key} should not be allowed by default"


def test_vote_round_trip():
    for mask in (0, votes.DEFAULT_MASK, votes.ALL_MASK, 1 << 5):
        assert votes.encode(votes.decode(mask)) == mask


def test_vote_preserves_unknown_high_bits():
    future = votes.DEFAULT_MASK | (1 << 31)
    assert votes.unknown_bits(future) == (1 << 31)
    out = votes.encode(votes.decode(future), preserve_high_bits=future)
    assert out & (1 << 31)


# -- Map cycle ---------------------------------------------------------------

def test_parse_cycle_ignores_comments_and_blocks():
    text = """
    // a comment
    ut4_casa
    ut4_turnpike
    {
        set timelimit 10
    }
    ut4_abbey   // trailing comment
    """
    assert maps.parse_cycle(text) == ["ut4_casa", "ut4_turnpike", "ut4_abbey"]


def test_cycle_round_trip():
    names = ["ut4_casa", "ut4_abbey"]
    assert maps.parse_cycle(maps.render_cycle(names)) == names


def test_map_display_name():
    assert maps.GameMap("ut4_oildepot", Path(".")).display_name == "Oildepot"


# -- Profile -----------------------------------------------------------------

def test_profile_only_stores_non_defaults():
    p = Profile()
    p.set("g_gravity", 800)          # the default
    assert "g_gravity" not in p.settings
    p.set("g_gravity", 400)
    assert p.settings["g_gravity"] == 400
    p.set("g_gravity", 800)          # back to default
    assert "g_gravity" not in p.settings


def test_profile_get_falls_back_to_default():
    p = Profile()
    assert p.get("g_gravity") == 800
    assert p.get("sv_maxclients") == 16


def test_profile_coerces_types():
    p = Profile()
    p.set("sv_pure", "0")
    assert p.get("sv_pure") is False
    p.set("timelimit", "45")
    assert p.get("timelimit") == 45


def test_profile_json_round_trip():
    p = Profile(name="Test")
    p.set("g_gametype", cvars.BOMB)
    p.mapcycle = ["ut4_casa"]
    restored = Profile.from_dict(p.to_dict())
    assert restored.name == "Test"
    assert restored.gametype == cvars.BOMB
    assert restored.mapcycle == ["ut4_casa"]


def test_import_cfg_reads_settings_and_start_map():
    text = """
    // my server
    set sv_hostname "My Cool Server"
    set g_gametype "8"
    seta sv_maxclients "20"
    sets sv_dlURL "example.com"
    set g_gravity 400
    map ut4_abbey
    """
    p = Profile.from_cfg(text)
    assert p.get("sv_hostname") == "My Cool Server"
    assert p.gametype == 8
    assert p.get("sv_maxclients") == 20
    assert p.get("sv_dlURL") == "example.com"
    assert p.get("g_gravity") == 400       # unquoted values parse too
    assert p.start_map == "ut4_abbey"


def test_import_cfg_reads_quoted_cvar_names():
    p = Profile.from_cfg('sets " Admin" "serveradmin"\nsets " Email" "a@b.c"\n')
    assert p.get(" Admin") == "serveradmin"
    assert p.get(" Email") == "a@b.c"
    assert "Admin" not in p.extra_cfg, "a known cvar must not fall through to extras"


def test_import_cfg_preserves_unknown_directives():
    text = 'set sv_hostname "X"\nbind F1 "say hi"\npb_sv_enable\n'
    p = Profile.from_cfg(text)
    assert "bind F1" in p.extra_cfg
    assert "pb_sv_enable" in p.extra_cfg


def test_import_real_server_example_cfg():
    # The shipped example is the broadest real-world config available.
    example = Path("/opt/urbanterror/q3ut4/server_example.cfg")
    if not example.is_file():
        return
    p = Profile.from_cfg_file(example)
    assert p.get("sv_hostname") == "New Unnamed Server"
    assert p.gametype == 7
    assert p.start_map == "ut4_casa"
    # Every recognised cvar should have landed in settings, not extra_cfg.
    assert "sv_hostname" not in p.extra_cfg


def test_problems_flags_port_and_slot_mistakes():
    p = Profile()
    p.set("net_port", 80)
    p.set("sv_maxclients", 8)
    p.set("sv_privateClients", 8)
    issues = " ".join(p.problems())
    assert "outside the usable range" in issues
    assert "no public slots" in issues


def test_problems_flags_start_map_outside_cycle():
    p = Profile()
    p.mapcycle = ["ut4_casa", "ut4_abbey"]
    p.start_map = "ut4_turnpike"
    assert any("not in the map cycle" in i for i in p.problems())


def test_serverinfo_length_grows_with_hostname():
    short = Profile()
    short.set("sv_hostname", "a")
    long = Profile()
    long.set("sv_hostname", "b" * 400)
    assert serverinfo_length(long) > serverinfo_length(short)


def test_store_round_trip(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    p = Profile(name="Alpha")
    p.set("g_gravity", 300)
    store.add(p)
    store.save()

    reloaded = ProfileStore(tmp_path / "profiles.json").load()
    assert len(reloaded.profiles) == 1
    assert reloaded.profiles[0].name == "Alpha"
    assert reloaded.profiles[0].get("g_gravity") == 300


def test_store_detects_port_conflicts():
    store = ProfileStore(Path("/nonexistent"))
    a, b = Profile(name="A"), Profile(name="B")
    a.set("net_port", 27960)
    b.set("net_port", 27960)
    store.add(a)
    store.add(b)
    assert 27960 in store.port_conflicts()
