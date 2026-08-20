"""Encoding and decoding of the ``g_allowvote`` bitmask.

Each vote players can call has one bit. A set bit permits the vote.

Provenance
----------
The vote list and its order come from the game module itself: ``qagame.qvm``
carries the usage string it prints when someone calls an unknown vote --

    Vote commands are: reload restart map nextmap g_gametype kick timelimit
    fraglimit capturelimit g_warmup g_friendlyFire swapteams shuffleteams
    g_respawnDelay g_waveRespawns g_redWave g_blueWave g_bombExplodeTime
    g_bombDefuseTime g_matchMode g_timeouts g_timeoutLength g_followStrict
    g_RoundTime g_cahTime exec g_gear g_maxrounds g_swaproles g_instagib

-- and bit *n* is the *n*th entry of that list. The shipped default of
``603981055`` decodes under this mapping to reload, restart, map, nextmap,
gametype, kick, timelimit, fraglimit, friendly fire, gear and instagib, which is
a coherent "sensible public server" set and is the corroboration that the order
is right.

The UI shows the resulting integer next to the checkboxes, so the value is
always inspectable and can be typed in directly.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The value server_example.cfg ships with.
DEFAULT_MASK = 603981055


@dataclass(frozen=True)
class Vote:
    bit: int
    key: str
    label: str
    help: str = ""

    @property
    def mask(self) -> int:
        return 1 << self.bit


def _v(bit: int, key: str, label: str, help_: str = "") -> Vote:
    return Vote(bit, key, label, help_)


#: Every callable vote, bit index equal to list position.
VOTES: tuple[Vote, ...] = (
    _v(0, "reload", "Reload map", "Reload the current map, keeping scores."),
    _v(1, "restart", "Restart map", "Restart the current map from scratch."),
    _v(2, "map", "Change map", "Switch immediately to a chosen map."),
    _v(3, "nextmap", "Set next map", "Choose the map that plays after this one."),
    _v(4, "g_gametype", "Change gametype", "Switch the game mode."),
    _v(5, "kick", "Kick player", "Remove a player from the server."),
    _v(6, "timelimit", "Time limit", "Change minutes per map."),
    _v(7, "fraglimit", "Frag limit", "Change the score needed to end a map."),
    _v(8, "capturelimit", "Capture limit", "Change the captures needed to end a map."),
    _v(9, "g_warmup", "Warmup time", "Change the pre-match warmup length."),
    _v(10, "g_friendlyFire", "Friendly fire", "Turn team damage on or off."),
    _v(11, "swapteams", "Swap teams", "Swap red and blue sides."),
    _v(12, "shuffleteams", "Shuffle teams", "Randomly redistribute players."),
    _v(13, "g_respawnDelay", "Respawn delay", "Change seconds before respawn."),
    _v(14, "g_waveRespawns", "Wave respawns", "Turn synchronised respawns on or off."),
    _v(15, "g_redWave", "Red wave interval", "Change red's wave respawn timing."),
    _v(16, "g_blueWave", "Blue wave interval", "Change blue's wave respawn timing."),
    _v(17, "g_bombExplodeTime", "Bomb explode time", "Change the bomb fuse length."),
    _v(18, "g_bombDefuseTime", "Bomb defuse time", "Change how long defusing takes."),
    _v(19, "g_matchMode", "Match mode", "Turn competitive match mode on or off."),
    _v(20, "g_timeouts", "Timeout count", "Change timeouts allowed per team."),
    _v(21, "g_timeoutLength", "Timeout length", "Change how long a timeout lasts."),
    _v(22, "g_followStrict", "Strict spectating", "Restrict what dead players may watch."),
    _v(23, "g_RoundTime", "Round time", "Change maximum round length."),
    _v(24, "g_cahTime", "Capture and Hold interval", "Change flag scoring interval."),
    _v(25, "exec", "Execute config", "Run a server config file. Grants broad control."),
    _v(26, "g_gear", "Weapons and items", "Change which equipment is allowed."),
    _v(27, "g_maxrounds", "Rounds per map", "Change how many rounds a map lasts."),
    _v(28, "g_swaproles", "Swap roles", "Replay the map with sides reversed."),
    _v(29, "g_instagib", "InstaGib", "Turn one-shot-kill mode on or off."),
)

VOTES_BY_KEY: dict[str, Vote] = {v.key: v for v in VOTES}

#: Votes that hand a player broad control over the server. The UI warns on these.
SENSITIVE = frozenset({"exec", "map", "g_gametype", "g_matchMode"})

ALL_MASK = (1 << len(VOTES)) - 1


def decode(mask: int) -> set[str]:
    """Bitmask to the set of vote keys that are **allowed**."""
    return {v.key for v in VOTES if mask & v.mask}


def encode(allowed: set[str] | frozenset[str], preserve_high_bits: int = 0) -> int:
    """Set of allowed vote keys back to a bitmask.

    ``preserve_high_bits`` carries through any bits above the known range so a
    future game build's extra votes survive a round-trip through this editor.
    """
    mask = 0
    for v in VOTES:
        if v.key in allowed:
            mask |= v.mask
    return mask | (preserve_high_bits & ~ALL_MASK)


def unknown_bits(mask: int) -> int:
    """Bits set in ``mask`` that fall outside the votes this build knows about."""
    return mask & ~ALL_MASK


def describe(mask: int) -> str:
    """Short human summary of a mask, for status lines."""
    allowed = decode(mask)
    if not allowed:
        return "No votes allowed"
    if len(allowed) == len(VOTES):
        return "All votes allowed"
    return f"{len(allowed)} of {len(VOTES)} votes allowed"
