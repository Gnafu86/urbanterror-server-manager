"""Tests for applying a per-map gametype as the rotation advances.

The mechanism is dictated by ``g_gametype`` being latched: it must be set
*before* the map changes, because the map cycle's own setting block runs after
the map has already loaded and is therefore ignored for this variable.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utsm.core import cycle  # noqa: E402
from utsm.model import cvars  # noqa: E402
from utsm.model.maps import CycleEntry  # noqa: E402

CTF, BOMB, TS = cvars.CTF, cvars.BOMB, cvars.TS


def rotation():
    return [
        CycleEntry("ut4_casa"),
        CycleEntry("ut4_abbey", BOMB),
        CycleEntry("ut4_turnpike", TS),
    ]


# -- Reading the map off the server's output ---------------------------------

def test_map_is_read_from_initgame():
    text = r'InitGame: \sv_hostname\X\g_gametype\7\mapname\ut4_abbey\sv_maxclients\12'
    assert cycle.map_from_output(text) == "ut4_abbey"


def test_unrelated_output_yields_nothing():
    assert cycle.map_from_output("Hitch warning: 700 msec frame time") is None
    assert cycle.map_from_output("") is None


def test_the_latest_map_wins_in_a_burst():
    """A chunk of console output can span more than one map change."""
    text = (
        r"InitGame: \mapname\ut4_casa\x\1" "\n"
        r"InitGame: \mapname\ut4_abbey\x\1"
    )
    assert cycle.map_from_output(text) == "ut4_abbey"


# -- Choosing the gametype ---------------------------------------------------

def test_gametype_is_chosen_for_the_next_map_not_the_current_one():
    """It has to be in place before the map changes, so it looks ahead."""
    assert cycle.gametype_for_next(rotation(), "ut4_casa", CTF) == BOMB
    assert cycle.gametype_for_next(rotation(), "ut4_abbey", CTF) == TS


def test_rotation_wraps_at_the_end():
    # After the last map comes the first, which has no override.
    assert cycle.gametype_for_next(rotation(), "ut4_turnpike", CTF) == CTF


def test_entry_without_an_override_restores_the_server_default():
    """A mode set for one map must not leak into the maps after it."""
    assert cycle.gametype_for_next(rotation(), "ut4_turnpike", CTF) == CTF


def test_a_map_outside_the_rotation_changes_nothing():
    """Someone voted a map, or an admin switched; the next map is a guess."""
    assert cycle.gametype_for_next(rotation(), "ut4_elgin", CTF) is None


def test_empty_rotation_changes_nothing():
    assert cycle.gametype_for_next([], "ut4_casa", CTF) is None


def test_index_and_next_entry():
    assert cycle.index_of(rotation(), "UT4_ABBEY") == 1     # case-insensitive
    assert cycle.next_entry(rotation(), "ut4_casa").map_name == "ut4_abbey"
    assert cycle.next_entry(rotation(), "nope") is None


# -- The scheduler -----------------------------------------------------------

def test_scheduler_emits_once_per_map_change():
    s = cycle.GametypeScheduler()
    assert s.on_map_loaded("ut4_casa", rotation(), CTF) == BOMB
    # The engine prints InitGame more than once, and console text can repeat.
    assert s.on_map_loaded("ut4_casa", rotation(), CTF) is None
    assert s.on_map_loaded("ut4_abbey", rotation(), CTF) == TS


def test_scheduler_skips_a_value_already_set():
    """Two consecutive maps wanting the same mode need only one command."""
    cyc = [
        CycleEntry("ut4_casa"),
        CycleEntry("ut4_abbey", BOMB),
        CycleEntry("ut4_turnpike", BOMB),
    ]
    s = cycle.GametypeScheduler()
    assert s.on_map_loaded("ut4_casa", cyc, CTF) == BOMB
    assert s.on_map_loaded("ut4_abbey", cyc, CTF) is None   # still BOMB
    assert s.on_map_loaded("ut4_turnpike", cyc, CTF) == CTF  # wraps to casa


def test_scheduler_tracks_the_current_map():
    s = cycle.GametypeScheduler()
    s.on_map_loaded("ut4_casa", rotation(), CTF)
    assert s.current_map == "ut4_casa"
    s.reset()
    assert s.current_map is None


def test_scheduler_ignores_an_empty_map_name():
    s = cycle.GametypeScheduler()
    assert s.on_map_loaded("", rotation(), CTF) is None
