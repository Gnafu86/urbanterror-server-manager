"""Declarative registry of every Urban Terror 4.3 server option.

This module is the specification the GUI is generated from: `utsm.ui.formbuilder`
walks ``REGISTRY`` and emits one widget per entry, so exposing a new server
option means adding a row here and nothing else.

Sources, in order of authority:

* ``/opt/urbanterror/q3ut4/server_example.cfg`` -- names, shipped defaults and
  the help text, which is reproduced close to verbatim so the tooltips teach
  the same thing the config file does.
* ``urbanterror-ded +cvarlist`` with the game module loaded -- the engine's own
  registered casing, defaults and flags. The ``L`` (latch) flag is where
  ``latched=True`` comes from; those cvars only take effect on map reload, and
  the engine says so itself ("g_gametype will be changed upon reloading").

Where the two disagree the engine wins, except for defaults: the example config
represents what a server operator is expected to start from, which is a better
default for a manager than the engine's bare fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Kind(str, Enum):
    """How a value is edited, which decides the widget the form builder emits."""

    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    PASSWORD = "password"
    ENUM = "enum"
    GEAR = "gear"
    VOTES = "votes"
    MAPCYCLE = "mapcycle"


# -- Gametypes ---------------------------------------------------------------
# 2 is absent: it was Single Player in stock Q3 and is unused in Urban Terror.

FFA, LMS, TDM, TS, FTL, CAH, CTF, BOMB, JUMP, FREEZE, GUN = 0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11

GAMETYPES: tuple[tuple[int, str], ...] = (
    (FFA, "Free For All"),
    (LMS, "Last Man Standing"),
    (TDM, "Team Deathmatch"),
    (TS, "Team Survivor"),
    (FTL, "Follow the Leader"),
    (CAH, "Capture and Hold"),
    (CTF, "Capture the Flag"),
    (BOMB, "Bomb Mode"),
    (JUMP, "Jump Training"),
    (FREEZE, "Freeze Tag"),
    (GUN, "Gun Game"),
)

ALL_GAMETYPES = frozenset(value for value, _ in GAMETYPES)

#: Modes that respawn players individually rather than running elimination rounds.
DEATHMATCH_MODES = frozenset({FFA, TDM, CAH, CTF, GUN})
#: Modes built on elimination rounds, where a dead player stays dead until next round.
ROUND_MODES = frozenset({LMS, TS, FTL, BOMB})
#: Modes that have flags to capture or hold.
FLAG_MODES = frozenset({CAH, CTF})


# -- Groups ------------------------------------------------------------------

@dataclass(frozen=True)
class Group:
    """A page of related options. ``gametypes`` hides the whole page when the
    selected gametype cannot use any of it."""

    key: str
    title: str
    blurb: str = ""
    gametypes: frozenset[int] | None = None

    def applies_to(self, gametype: int) -> bool:
        return self.gametypes is None or gametype in self.gametypes


GROUPS: tuple[Group, ...] = (
    Group("identity", "Identity", "How your server presents itself in the browser."),
    Group("network", "Network & Slots", "Ports, player slots and connection limits."),
    Group("admin", "Admin & Security", "Rcon, referees and server passwords."),
    Group("auth", "Authentication", "The FrozenSand account system."),
    Group("logging", "Logging", "Log files, for stats parsers and admin tools."),
    Group("gameplay", "Gameplay", "Core rules that apply to every gametype."),
    Group("teams", "Teams", "Friendly fire, balancing and team identity."),
    Group("match", "Match Mode", "Competitive play: timeouts, pauses and ready-up."),
    Group("weapons", "Weapons & Items", "Which equipment players may spawn with."),
    Group("voting", "Voting", "Which votes players are allowed to call."),
    Group("mapcycle", "Map Cycle", "Which maps the server rotates through."),
    Group("respawn", "Respawning", "Respawn timing.", frozenset(DEATHMATCH_MODES)),
    Group("rounds", "Rounds", "Round length and scoring.", frozenset(ROUND_MODES)),
    Group("flags", "Flags", "Flag scoring and wave respawns.", frozenset(FLAG_MODES)),
    Group("cah", "Capture and Hold", "", frozenset({CAH})),
    Group("ctf", "Capture the Flag", "", frozenset({CTF})),
    Group("bomb", "Bomb Mode", "", frozenset({BOMB})),
    Group("jump", "Jump Training", "", frozenset({JUMP})),
    Group("freeze", "Freeze Tag", "", frozenset({FREEZE})),
    Group("gungame", "Gun Game", "", frozenset({GUN})),
)

GROUPS_BY_KEY: dict[str, Group] = {g.key: g for g in GROUPS}


# -- CVar --------------------------------------------------------------------

@dataclass(frozen=True)
class CVar:
    """One server option.

    ``latched`` mirrors the engine's ``CVAR_LATCH`` flag: the value is accepted
    immediately but does not take effect until the map reloads, so the UI marks
    it instead of pretending the change landed.

    ``setter`` is ``sets`` for cvars that must be broadcast in the serverinfo
    string so the game browser can read them, matching server_example.cfg.
    """

    name: str
    label: str
    kind: Kind
    default: Any
    group: str
    help: str = ""
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[tuple[Any, str], ...] = ()
    latched: bool = False
    setter: str = "set"
    gametypes: frozenset[int] | None = None
    unit: str = ""
    advanced: bool = False

    def applies_to(self, gametype: int) -> bool:
        """Whether this option has any effect in the given gametype."""
        if self.gametypes is not None:
            return gametype in self.gametypes
        return GROUPS_BY_KEY[self.group].applies_to(gametype)

    def coerce(self, raw: Any) -> Any:
        """Convert a value from config text or JSON into this cvar's Python type."""
        if self.kind is Kind.BOOL:
            if isinstance(raw, str):
                return raw.strip() not in ("", "0", "false", "False")
            return bool(raw)
        if self.kind in (Kind.INT, Kind.ENUM, Kind.VOTES):
            try:
                return int(str(raw).strip() or 0)
            except ValueError:
                return self.default
        if self.kind is Kind.FLOAT:
            try:
                return float(str(raw).strip() or 0)
            except ValueError:
                return self.default
        if self.kind is Kind.MAPCYCLE:
            if isinstance(raw, (list, tuple)):
                return list(raw)
            return [m for m in str(raw).split() if m]
        return str(raw)

    def to_cfg_value(self, value: Any) -> str:
        """Render a Python value the way the server config expects it."""
        if self.kind is Kind.BOOL:
            return "1" if value else "0"
        if self.kind is Kind.MAPCYCLE:
            # The cycle lives in its own file; the cvar only names that file.
            return str(value)
        return str(value)


def _b(name, label, default, group, help_="", **kw) -> CVar:
    return CVar(name, label, Kind.BOOL, default, group, help_, **kw)


def _i(name, label, default, group, help_="", lo=0, hi=None, **kw) -> CVar:
    return CVar(name, label, Kind.INT, default, group, help_, minimum=lo, maximum=hi, **kw)


def _s(name, label, default, group, help_="", **kw) -> CVar:
    return CVar(name, label, Kind.STRING, default, group, help_, **kw)


def _p(name, label, default, group, help_="", **kw) -> CVar:
    return CVar(name, label, Kind.PASSWORD, default, group, help_, **kw)


def _e(name, label, default, group, choices, help_="", **kw) -> CVar:
    return CVar(name, label, Kind.ENUM, default, group, help_, choices=choices, **kw)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

REGISTRY: tuple[CVar, ...] = (
    # -- Identity -----------------------------------------------------------
    _s("sv_hostname", "Server name", "New Unnamed Server", "identity",
       "Your server name. Note that not all game browsers display colour codes "
       "correctly. Use ^1..^9 for colours."),
    _s("sv_joinmessage", "Join message", "Welcome to Urban Terror 4.3", "identity",
       "Displayed to clients as they join your server."),
    _s("g_motd", "Message of the day", "Urban Terror, presented by FrozenSand", "identity",
       "Displayed on the loading screen while a player connects."),
    _s(" Admin", "Admin name", "", "identity",
       "Server administrator, shown in the browser's server details.", setter="sets"),
    _s(" Email", "Contact email", "", "identity",
       "An email address for technical support, shown in server details.", setter="sets"),
    _s("sv_keywords", "Browser keywords", "", "identity",
       "Keywords the master server browser can filter your server by.",
       setter="sets", advanced=True),

    # -- Network & Slots ----------------------------------------------------
    _i("net_port", "Port", 27960, "network",
       "UDP port the server listens on. Each server on this machine needs its own.",
       lo=1024, hi=65535),
    _i("sv_maxclients", "Max players", 16, "network",
       "Max clients allowed to connect. More than 16 is not advised: it can cause lag "
       "and most maps are not built for it. Going over 24 can cause nasty bugs.",
       lo=1, hi=64, latched=True),
    _i("g_maxGameClients", "Max playing clients", 0, "network",
       "Max clients that can actually join the game; the rest are forced to spectate. "
       "0 = all.", lo=0, hi=64, latched=True),
    _i("sv_privateClients", "Private slots", 0, "network",
       "Slots reserved for players who enter the private password.", lo=0, hi=64),
    _p("sv_privatePassword", "Private slot password", "", "network",
       "Password used to claim one of the private slots."),
    _e("dedicated", "Server visibility", 2, "network",
       ((1, "LAN only"), (2, "Public (advertise to master servers)")),
       "LAN servers are not announced to the master server list."),
    _i("sv_timeout", "Client timeout", 180, "network",
       "Seconds before a 'Connection Interrupted' player is dropped. Smaller values "
       "clear zombies faster, but slow clients may not finish loading a map in time.",
       lo=10, hi=600, unit="s"),
    _i("sv_maxPing", "Max ping", 0, "network",
       "Upper ping limit at which players may still join. 0 = no limit.", lo=0, hi=1000),
    _i("sv_minPing", "Min ping", 0, "network",
       "Lower ping limit at which players may still join. 0 = no limit.", lo=0, hi=1000),
    _i("sv_maxRate", "Max rate", 0, "network",
       "Maximum traffic per second the server sends per client, in bytes. 0 = 25000 = max.",
       lo=0, hi=100000, unit="B/s", advanced=True),
    _i("sv_minRate", "Min rate", 0, "network",
       "Minimum traffic per second the server sends per client, in bytes. 0 = 25000 = max.",
       lo=0, hi=100000, unit="B/s", advanced=True),
    _i("sv_clientsPerIp", "Clients per IP", 3, "network",
       "Maximum clients allowed to connect simultaneously from one IP address.", lo=1, hi=64),
    _i("sv_reconnectlimit", "Reconnect limit", 0, "network",
       "How many times a disconnected client may come back during the same map.",
       lo=0, hi=100),
    _i("sv_floodprotect", "Flood protection", 2, "network",
       "Client commands allowed per second, to stop chat and bind spam. 0 = unlimited.",
       lo=0, hi=100),
    _b("sv_pure", "Pure server", True, "network",
       "Prevents players from loading modified .pk3 files."),
    _b("sv_allowdownload", "Allow downloads", True, "network",
       "Lets clients download .pk3 files (maps) they do not have. Auto-download only "
       "works for ioUrbanTerror clients, not stock Quake 3 clients."),
    _s("sv_dlURL", "Download URL", "urbanterror.info", "network",
       "Address used for auto-downloading: the client fetches <URL>/q3ut4/<mapname>.pk3. "
       "Leaving this as urbanterror.info uses a mirror carrying the most common maps.",
       setter="sets"),
    _b("sv_strictauth", "Strict CD-key auth", False, "network",
       "Check for a valid Quake 3 CD key. This blocks ioUrbanTerror players, so it is "
       "almost always left off.", advanced=True),
    _i("sv_fps", "Server tickrate", 20, "network",
       "Server frames per second. Higher is smoother but costs more CPU.",
       lo=10, hi=125, advanced=True),
    _s("sv_master2", "Master server 2", "master.urbanterror.info", "network",
       "Master server to announce this server to.", advanced=True),
    _s("sv_master3", "Master server 3", "master2.urbanterror.info", "network",
       "Master server to announce this server to.", advanced=True),
    _s("sv_master4", "Master server 4", "master.quake3arena.com:27950", "network",
       "Master server to announce this server to.", advanced=True),
    _s("sv_master5", "Master server 5", "", "network",
       "Reserved for future use.", advanced=True),

    # -- Admin & Security ---------------------------------------------------
    _p("rconpassword", "Rcon password", "", "admin",
       "Password for controlling the server remotely with rcon. Required if you want "
       "to administer this server from another machine."),
    _p("g_password", "Server password", "", "admin",
       "Password required to join. Empty means the server is public."),
    _b("g_referee", "Enable referees", False, "admin",
       "Enables the referee commands, a limited admin role for match play."),
    _p("g_refPass", "Referee password", "", "admin",
       "Referee password. An empty password also disables referees."),
    _b("g_refNoBan", "Referees cannot ban", False, "admin",
       "Prevents referees from banning players from the server."),
    _b("g_refNoExec", "Referees cannot exec", False, "admin",
       "Prevents referees from using the 'exec' command."),
    _b("g_filterBan", "Enable ban list", True, "admin",
       "Allows banning players via the banlist.txt file."),
    _s("sv_sayprefix", "Say prefix", "console: ", "admin",
       "Prefix for /rcon say messages shown in game.", advanced=True),
    _s("sv_tellprefix", "Tell prefix", "console_tell: ", "admin",
       "Prefix for /rcon tell messages shown in game.", advanced=True),
    _s("sv_demonotice", "Demo notice", "Smile! You're on camera!", "admin",
       "Message printed when a player starts being recorded server-side. "
       "Empty means no message."),
    _s("sv_demofolder", "Demo folder", "serverdemos", "admin",
       "Folder that server-side demos are written to.", advanced=True),
    _b("sv_battleye", "BattlEye anti-cheat", False, "admin",
       "Enables BattlEye anti-cheat support, if available.", advanced=True),

    # -- Authentication -----------------------------------------------------
    _b("auth_enable", "Enable auth system", True, "auth",
       "The FrozenSand account system. Set to 0 to disable it entirely.", setter="sets"),
    _i("auth_notoriety", "Minimum notoriety", 0, "auth",
       "Minimum notoriety level required to connect. 0 allows everyone to join.",
       lo=0, hi=100, setter="sets"),
    _b("auth_tags", "Protect clan tags", True, "auth",
       "Prevents clan tag thieves from joining your server."),
    _b("auth_cheaters", "Block known cheaters", True, "auth",
       "Blocks officially banned cheaters from your server."),
    _e("auth_verbosity", "Auth messages", 1, "auth",
       ((0, "Silent"), (1, "Top of screen"), (2, "In chat box")),
       "How a player's authentication is announced when they connect."),
    _b("auth_log", "Log auth info", True, "auth",
       "Writes each player's account information into the server logs."),
    _s("auth_owners", "Owner group IDs", "", "auth",
       "Space-separated group IDs allowed to execute auth-rcon commands.", setter="sets"),
    _s("auth_groups", "Allowed group IDs", "", "auth",
       "Space-separated group IDs authorised to join. Empty means any player can connect.",
       setter="sets"),

    # -- Logging ------------------------------------------------------------
    _s("g_log", "Log file name", "games.log", "logging",
       "Server log file name."),
    _b("g_logSync", "Unbuffered logging", True, "logging",
       "Real-time (unbuffered) log writing. Required by third-party admin software."),
    _b("g_loghits", "Log every hit", False, "logging",
       "Logs every single hit. Creates very large logs, but is needed for accurate "
       "hit detection in third-party admin software."),
    _b("g_logroll", "Roll log files", False, "logging",
       "Starts a new log periodically as <nnnn>_<logname>.log. Leave off if you use "
       "a log parser that follows a single file."),
    _e("logfile", "Console log", 0, "logging",
       ((0, "Disabled"), (1, "Buffered"), (2, "Synced"), (3, "Appended")),
       "Additional engine logging to a separate qconsole.log file."),

    # -- Gameplay -----------------------------------------------------------
    _e("g_gametype", "Gametype", CTF, "gameplay", GAMETYPES,
       "The game mode. Changing this takes effect on the next map load.", latched=True),
    _b("g_instagib", "InstaGib", False, "gameplay",
       "One-shot-kill mode. Cannot be used in Gun Game or Jump.", latched=True),
    _i("timelimit", "Time limit", 20, "gameplay",
       "Minutes before the map is over. 0 = never.", lo=0, hi=1440, unit="min"),
    _i("fraglimit", "Frag limit", 0, "gameplay",
       "Points to be scored before the map is over. 0 = never.", lo=0, hi=9999),
    _i("g_warmup", "Warmup", 15, "gameplay",
       "Seconds before the game starts on a new map, giving slower machines time to load.",
       lo=0, hi=300, unit="s"),
    _i("g_gravity", "Gravity", 800, "gameplay",
       "Higher number = lower jumps. Default 800; popular 'moon mode' values are 300 and 100.",
       lo=0, hi=10000),
    _i("g_knockback", "Knockback", 1000, "gameplay",
       "Knockback from a weapon. Higher number = greater knockback.", lo=0, hi=10000),
    _i("g_speed", "Player speed", 280, "gameplay",
       "Base movement speed. The stock value is 280.", lo=50, hi=2000, advanced=True),
    _b("g_followstrict", "Strict spectating", True, "gameplay",
       "When on, dead players cannot follow and scout enemies."),
    _i("g_removeBodyTime", "Body removal", 15, "gameplay",
       "Seconds after which a body fades out of the world.", lo=0, hi=300, unit="s"),
    _b("g_antiwarp", "Anti-warp", True, "gameplay",
       "Smooths the movement of warping players, whether they warp from packet loss or "
       "cheating. The warping player experiences stutters instead."),
    _i("g_antiwarptol", "Anti-warp tolerance", 50, "gameplay",
       "Anti-warp tolerance in milliseconds. Higher is more tolerant; low settings "
       "increase server load.", lo=0, hi=1000, unit="ms", advanced=True),
    _i("g_inactivity", "Inactivity timeout", 0, "gameplay",
       "Seconds before a non-moving player is acted on. 0 disables the check.",
       lo=0, hi=3600, unit="s"),
    _e("g_inactivityAction", "On inactivity", 1, "gameplay",
       ((0, "Kick from server"), (1, "Move to spectators")),
       "What to do when a player hits the inactivity time."),
    _e("g_allowChat", "Chat", 2, "gameplay",
       ((0, "No chat at all"), (1, "Team chat only"), (2, "All chat"),
        (3, "Captains only (match mode)")),
       "How much chatting is permitted on the server."),
    _e("g_deadchat", "Dead chat", 2, "gameplay",
       ((0, "Living cannot see dead chat"), (1, "Team messages only"),
        (2, "All dead messages visible")),
       "Whether living players can read messages from dead players."),
    _e("g_armbands", "Armband colour", 0, "gameplay",
       ((0, "Player's choice"), (1, "Based on team colour"), (2, "Random, server assigned")),
       "How each player's armband colour is decided."),
    _b("g_skins", "Client skin selection", True, "gameplay",
       "Enables the client-side skin selection system. Off falls back to plain red and "
       "blue teams."),
    _b("g_allowForceSkins", "Allow forced skins", True, "gameplay",
       "Lets clients force everyone to a single skin locally.", advanced=True),
    _b("g_funstuff", "Fun stuff", True, "gameplay",
       "Enables cosmetic funstuff (hats and similar) on the server."),
    _b("g_enableDust", "Dust effects", False, "gameplay", "Enables dust effects.", advanced=True),
    _b("g_enableBreath", "Breath effects", False, "gameplay",
       "Enables visible breath in cold maps.", advanced=True),
    _b("g_enablePrecip", "Precipitation", False, "gameplay",
       "Enables rain and snow effects.", advanced=True),
    _b("g_healthReport", "Health report", False, "gameplay",
       "Shows teammate health on the team overlay.", advanced=True),
    _b("g_dedAutoChat", "Automatic console chat", False, "gameplay",
       "Lets the dedicated server emit automated chat messages.", advanced=True),
    _i("g_bulletPredictionThreshold", "Bullet prediction threshold", 300, "gameplay",
       "Ping threshold in milliseconds above which bullet prediction is applied.",
       lo=0, hi=1000, unit="ms", advanced=True),
    _i("g_ClientReconnectMin", "Reconnect cooldown", 0, "gameplay",
       "Minimum seconds before a client may reconnect.", lo=0, hi=3600, unit="s",
       advanced=True),

    # -- Teams --------------------------------------------------------------
    _e("g_friendlyfire", "Friendly fire", 1, "teams",
       ((0, "Off"), (1, "On, kick after too many team kills"), (2, "On, no kicks")),
       "Whether players can damage their own team, and what happens if they do."),
    _i("g_maxteamkills", "Max team kills", 3, "teams",
       "Team kills allowed before a kick, when friendly fire is set to kick.", lo=0, hi=100),
    _i("g_teamkillsForgetTime", "Team kill amnesty", 200, "teams",
       "Seconds before team kills are forgotten.", lo=0, hi=3600, unit="s"),
    _b("g_teamAutoJoin", "Auto-join teams", False, "teams",
       "Forces players onto a team on connect instead of letting them spectate first."),
    _b("g_teamforcebalance", "Force team balance", False, "teams",
       "Stops players joining a team that already has more players than the other."),
    _i("g_autobalance", "Auto-balance interval", 1, "teams",
       "Balances teams every X minutes when above 0. In survivor modes (Bomb, TS) "
       "balancing happens at the end of each round instead.", lo=0, hi=60, unit="min"),
    _b("g_maintainTeam", "Keep teams across maps", True, "teams",
       "Players stay on their team when the map changes."),
    _s("g_nameRed", "Red team name", "", "teams",
       "Name for the red team. Empty uses the default name."),
    _s("g_nameBlue", "Blue team name", "", "teams",
       "Name for the blue team. Empty uses the default name."),
    _b("g_swaproles", "Swap roles after map", False, "teams",
       "Replays the map with the teams swapped. Recommended for Bomb mode.", latched=True),
    _b("g_shuffleNoRestart", "Shuffle without restart", True, "teams",
       "When off, the map restarts after shuffleteams."),

    # -- Match mode ---------------------------------------------------------
    _b("g_matchmode", "Match mode", False, "match",
       "Competitive play: adds timeouts and ready-up commands.", latched=True),
    _i("g_timeouts", "Timeouts per team", 3, "match",
       "How many timeouts each team may call per map.", lo=0, hi=20),
    _i("g_timeoutLength", "Timeout length", 240, "match",
       "Seconds before a timeout expires.", lo=10, hi=3600, unit="s"),
    _i("g_pauseLength", "Pause length", 0, "match",
       "Length of a pause; only affects the rcon pause command. 0 = unlimited.",
       lo=0, hi=3600, unit="s"),
    _i("g_stratTime", "Strategy time", 5, "match",
       "Strategy time in seconds at the start of a round.", lo=0, hi=120, unit="s",
       gametypes=frozenset({TS, BOMB})),

    # -- Respawning (FFA, TDM, CAH, CTF, Gun Game) --------------------------
    _i("g_respawnProtection", "Spawn protection", 2, "respawn",
       "Seconds a spawning player is protected from damage.", lo=0, hi=60, unit="s"),
    _i("g_respawndelay", "Respawn delay", 5, "respawn",
       "Seconds before respawn. Ignored when wave respawns are on.", lo=0, hi=120, unit="s"),
    _i("g_forcerespawn", "Force respawn", 20, "respawn",
       "Seconds before a respawn is forced, even if the player does not press fire.",
       lo=0, hi=120, unit="s"),

    # -- Rounds (LMS, TS, FTL, Bomb) ----------------------------------------
    _i("g_maxrounds", "Rounds per map", 0, "rounds",
       "Rounds before the map is over. 0 = unlimited.", lo=0, hi=100),
    _i("g_RoundTime", "Round time", 2, "rounds",
       "Maximum minutes a single round may take.", lo=1, hi=60, unit="min"),
    _e("g_survivorrule", "Survivor rule", 0, "rounds",
       ((0, "No point if time runs out"), (1, "Team with most players left scores")),
       "How a round is scored when time expires before everyone is dead."),
    _b("g_suddendeath", "Sudden death", False, "rounds",
       "Adds another round when the map ends with the scores level."),

    # -- Flags (CAH, CTF) ---------------------------------------------------
    _i("capturelimit", "Capture limit", 0, "flags",
       "Flag captures before the map is over. 0 = unlimited.", lo=0, hi=999),
    _b("g_waverespawns", "Wave respawns", True, "flags",
       "Everyone on a team respawns together in waves."),
    _i("g_redwave", "Red wave interval", 15, "flags",
       "Seconds between red team wave respawns. Ignored when wave respawns are off.",
       lo=1, hi=120, unit="s"),
    _i("g_bluewave", "Blue wave interval", 15, "flags",
       "Seconds between blue team wave respawns. Ignored when wave respawns are off.",
       lo=1, hi=120, unit="s"),

    # -- Capture and Hold ---------------------------------------------------
    _i("g_cahTime", "Scoring interval", 30, "cah",
       "Seconds between awarding points for held flags.", lo=1, hi=300, unit="s"),

    # -- Capture the Flag ---------------------------------------------------
    _i("g_flagreturntime", "Flag return time", 30, "ctf",
       "Seconds before a dropped flag returns to base automatically.",
       lo=0, hi=300, unit="s"),
    _i("g_hotpotato", "Hot potato", 1, "ctf",
       "Minutes both flags may be held before they explode. 0 disables it.",
       lo=0, hi=60, unit="min"),
    _b("g_ctfUnsubWait", "Unsub waits for wave", False, "ctf",
       "A player who unsubs in match mode waits for the next wave to spawn."),

    # -- Bomb ---------------------------------------------------------------
    _i("g_bombPlantTime", "Plant time", 3, "bomb",
       "Seconds it takes to plant the bomb.", lo=1, hi=60, unit="s", latched=True),
    _i("g_bombdefusetime", "Defuse time", 5, "bomb",
       "Seconds it takes to defuse the bomb.", lo=1, hi=60, unit="s"),
    _i("g_bombexplodetime", "Explode time", 30, "bomb",
       "Seconds before the bomb goes off after being planted.", lo=5, hi=300, unit="s"),

    # -- Jump ---------------------------------------------------------------
    _i("g_walljumps", "Wall jumps", 3, "jump",
       "Maximum wall jumps a player may chain.", lo=3, hi=100),
    _b("g_noDamage", "No damage", True, "jump",
       "Players take no falling or telefrag damage."),
    _e("g_stamina", "Stamina", 1, "jump",
       ((0, "Default"), (1, "Regain when standing still"), (2, "Infinite")),
       "How stamina behaves."),
    _b("g_allowGoto", "Allow /goto", True, "jump",
       "Enables the /goto and /allowgoto commands for teleporting between players."),
    _b("g_allowPosSaving", "Allow position saving", True, "jump",
       "Lets players use /savePos and /loadPos."),
    _b("g_persistentPositions", "Persistent positions", True, "jump",
       "Saved positions survive a player disconnecting."),
    _i("g_jumpruns", "Jump run attempts", 0, "jump",
       "Maximum jump run attempts per player in match mode. 0 = unlimited.", lo=0, hi=100),
    _b("g_noVest", "Remove kevlar", True, "jump",
       "Removes kevlar from all players and gives a medkit instead."),

    # -- Freeze Tag ---------------------------------------------------------
    _i("g_thawTime", "Thaw time", 6, "freeze",
       "Seconds a player takes to thaw a frozen ally.", lo=1, hi=60, unit="s"),
    _i("g_meltdownTime", "Meltdown time", 60, "freeze",
       "Seconds before a frozen player melts back to life on their own.",
       lo=0, hi=600, unit="s"),

    # -- Gun Game -----------------------------------------------------------
    _b("g_hardcore", "Hardcore", False, "gungame",
       "Different gun order, and being killed by a lower level demotes you one level.",
       latched=True),
    _b("g_randomorder", "Random gun order", False, "gungame",
       "The gun order is randomly generated on map load.", latched=True),

    # -- Composite editors --------------------------------------------------
    CVar("g_gear", "Weapons & items", Kind.GEAR, "", "weapons",
         "Which weapons and items players may not spawn with. Stored as a letter set."),
    CVar("g_allowvote", "Allowed votes", Kind.VOTES, 603981055, "voting",
         "Bitmask deciding which votes players may call."),
    _i("g_failedVoteTime", "Failed vote cooldown", 300, "voting",
       "Seconds before another vote may be called after one fails.",
       lo=0, hi=3600, unit="s"),
    _i("g_newMapVoteTime", "New map vote cooldown", 0, "voting",
       "Seconds after a map change before a map vote may be called.",
       lo=0, hi=3600, unit="s", advanced=True),
    CVar("g_mapcycle", "Map cycle", Kind.MAPCYCLE, "mapcycle.txt", "mapcycle",
         "The rotation of maps the server plays through."),
)

def _key(name: str) -> str:
    """Normalise a cvar name for lookup.

    Case is folded because the engine's own casing and server_example.cfg's
    disagree for several cvars. Surrounding whitespace is stripped because a
    couple of names genuinely carry a leading space -- ``" Admin"`` and
    ``" Email"`` are written that way so they sort to the top of the server
    browser's details -- and the same normalisation has to be applied when
    building the index and when looking up, or those two are unreachable.
    """
    return name.strip().lower()


REGISTRY_BY_NAME: dict[str, CVar] = {_key(c.name): c for c in REGISTRY}


def get(name: str) -> CVar | None:
    """Look a cvar up case-insensitively, the way the engine does."""
    return REGISTRY_BY_NAME.get(_key(name))


def for_group(group: str, gametype: int | None = None, advanced: bool = True) -> list[CVar]:
    """All cvars in a group, optionally filtered to one gametype and to basic options."""
    out = []
    for c in REGISTRY:
        if c.group != group:
            continue
        if gametype is not None and not c.applies_to(gametype):
            continue
        if not advanced and c.advanced:
            continue
        out.append(c)
    return out


def visible_groups(gametype: int) -> list[Group]:
    """Groups that have at least one applicable option in this gametype."""
    return [g for g in GROUPS if g.applies_to(gametype) and for_group(g.key, gametype)]


def defaults() -> dict[str, Any]:
    """A fresh settings mapping using every cvar's shipped default."""
    return {c.name: c.default for c in REGISTRY}


#: Cvars the manager passes on the command line rather than writing into the
#: config, because the engine only honours them at startup.
COMMAND_LINE_ONLY = frozenset({"net_port", "dedicated"})
