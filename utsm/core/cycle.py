"""Applying a per-map gametype as the rotation advances.

``g_gametype`` is latched: it is accepted at any time but only takes effect when
a map loads. That rules out the obvious approach of writing it into the map
cycle's per-map setting block, because the block runs *after* the map has
loaded. Measured against 4.3.4, a block setting ``timelimit`` takes effect while
the same block setting ``g_gametype`` leaves the mode unchanged for every map in
the rotation.

Setting it *before* the map changes does work::

    start                            -> ut4_casa      gametype 7 (CTF)
    set g_gametype 8; cyclemap       -> ut4_abbey     gametype 8 (BOMB)
    set g_gametype 4; cyclemap       -> ut4_turnpike  gametype 4 (TS)

So the manager watches for a map load and immediately sets the gametype the
*next* map wants. The latched value is then already in place when that map
spawns: no extra reload, and players on the current map keep seeing the mode
they are actually playing.
"""

from __future__ import annotations

import re

from ..model.maps import CycleEntry

#: The gamecode prints this on every map load, with the map in the info string.
_INIT_GAME = re.compile(r"InitGame:\s*(\\.*)")


def map_from_output(text: str) -> str | None:
    """The map name from a server's ``InitGame`` line, if the text has one.

    Returns the last one in the text, so a burst covering several map changes
    settles on the current map rather than an earlier one.
    """
    found = None
    for match in _INIT_GAME.finditer(text):
        info = match.group(1)
        parts = info.split("\\")
        for i in range(1, len(parts) - 1, 2):
            if parts[i].lower() == "mapname":
                found = parts[i + 1]
    return found


def index_of(cycle: list[CycleEntry], map_name: str) -> int | None:
    """Position of a map in the rotation, or None if it is not in it."""
    target = (map_name or "").strip().lower()
    for i, entry in enumerate(cycle):
        if entry.map_name.strip().lower() == target:
            return i
    return None


def next_entry(cycle: list[CycleEntry], current_map: str) -> CycleEntry | None:
    """The entry that will play after ``current_map``, wrapping at the end."""
    if not cycle:
        return None
    here = index_of(cycle, current_map)
    if here is None:
        # The server is on a map outside the rotation -- someone voted or an
        # admin switched. Predicting the next map would be guesswork, so leave
        # the gametype alone until the rotation is rejoined.
        return None
    return cycle[(here + 1) % len(cycle)]


def gametype_for_next(
    cycle: list[CycleEntry],
    current_map: str,
    default_gametype: int,
) -> int | None:
    """The gametype to set now so the next map spawns in it.

    Returns ``None`` when there is nothing to decide. An entry without its own
    gametype resolves to the server's configured one, so a mode set for one map
    does not leak into the maps after it.
    """
    entry = next_entry(cycle, current_map)
    if entry is None:
        return None
    return default_gametype if entry.gametype is None else entry.gametype


class GametypeScheduler:
    """Tracks the current map and reports gametype changes worth sending.

    Remembers what it last set so a repeated ``InitGame`` -- the engine prints
    one per map load, and the console can replay text -- does not resend the
    same command.
    """

    def __init__(self) -> None:
        self._current_map: str | None = None
        self._last_set: int | None = None

    def reset(self) -> None:
        self._current_map = None
        self._last_set = None

    @property
    def current_map(self) -> str | None:
        return self._current_map

    def on_map_loaded(
        self,
        map_name: str,
        cycle: list[CycleEntry],
        default_gametype: int,
    ) -> int | None:
        """Note a map load; return a gametype to set, or None to do nothing."""
        if not map_name:
            return None
        changed = map_name != self._current_map
        self._current_map = map_name
        if not changed:
            return None

        wanted = gametype_for_next(cycle, map_name, default_gametype)
        if wanted is None or wanted == self._last_set:
            return None
        self._last_set = wanted
        return wanted
