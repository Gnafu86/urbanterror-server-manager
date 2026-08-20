"""Server profiles: the manager's unit of configuration.

A profile is the GUI's source of truth. It persists as JSON and a ``.cfg`` is
generated from it at launch, rather than the GUI editing a config file in place.
That keeps the round-trip lossless in the direction that matters -- what you see
in the form is exactly what the server is told.

Existing hand-written configs are not lost: :func:`Profile.from_cfg` parses one
into a profile, keeping any directive the registry does not know about in
``extra_cfg`` so it still reaches the server.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import cvars, maps
from .maps import CycleEntry

LOCAL = "local"
REMOTE = "remote"

#: Directives in a config file that are actions rather than settings. They are
#: handled explicitly instead of being treated as unknown leftovers.
_ACTION_DIRECTIVES = frozenset({"map", "devmap", "spmap", "exec", "wait", "vstr"})

# Both the name and the value may be quoted. The name has to allow quoting
# because ``" Admin"`` and ``" Email"`` carry a deliberate leading space.
_SET_RE = re.compile(
    r'^\s*(set[asu]?)\s+(?:"([^"]*)"|([^\s"]+))\s+(?:"([^"]*)"|(\S+))\s*$',
    re.IGNORECASE,
)
_MAP_RE = re.compile(r'^\s*(?:dev)?map\s+"?([A-Za-z0-9_.-]+)"?\s*$', re.IGNORECASE)


@dataclass
class Profile:
    """One configured server."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "New Server"
    kind: str = LOCAL

    #: cvar name -> value, holding only what differs from the registry default.
    settings: dict[str, Any] = field(default_factory=dict)
    #: The rotation. Entries may carry their own gametype; see
    #: :mod:`utsm.core.cycle` for why that is applied by the manager rather
    #: than written into the map cycle file.
    mapcycle: list[CycleEntry] = field(default_factory=list)
    start_map: str = "ut4_casa"

    #: Raw config lines appended verbatim after the generated ones.
    extra_cfg: str = ""

    #: Remote connection details, used only when ``kind`` is REMOTE.
    host: str = "127.0.0.1"
    port: int = 27960
    rcon_password: str = ""

    #: Built-in HTTP server for custom map auto-download.
    dl_enabled: bool = False
    dl_port: int = 8000
    #: Address clients should use to reach the download server. Empty means
    #: "detect the LAN address at start time", which is right for a LAN game
    #: but has to be set by hand behind NAT.
    dl_host: str = ""

    # -- Settings access ----------------------------------------------------

    def get(self, name: str) -> Any:
        """Effective value of a cvar: the override if set, else its default."""
        cvar = cvars.get(name)
        if cvar is None:
            return self.settings.get(name)
        if cvar.name in self.settings:
            return cvar.coerce(self.settings[cvar.name])
        return cvar.default

    def set(self, name: str, value: Any) -> None:
        """Record a value, dropping it if it matches the registry default."""
        cvar = cvars.get(name)
        if cvar is None:
            self.settings[name] = value
            return
        coerced = cvar.coerce(value)
        if coerced == cvar.default:
            self.settings.pop(cvar.name, None)
        else:
            self.settings[cvar.name] = coerced

    def effective(self) -> dict[str, Any]:
        """Every cvar with its effective value."""
        out = cvars.defaults()
        for name, value in self.settings.items():
            cvar = cvars.get(name)
            out[cvar.name if cvar else name] = cvar.coerce(value) if cvar else value
        return out

    def cycle_entries(self) -> list[CycleEntry]:
        """The rotation, normalised.

        Tolerates a plain list of map names, which is both what older profiles
        stored and the obvious thing for calling code to assign.
        """
        return [CycleEntry.from_any(e) for e in self.mapcycle]

    def cycle_map_names(self) -> list[str]:
        """Just the map names, in rotation order."""
        return [e.map_name for e in self.cycle_entries()]

    @property
    def gametype(self) -> int:
        return int(self.get("g_gametype") or 0)

    @property
    def net_port(self) -> int:
        """The port the server listens on, kept in sync for remote profiles."""
        if self.kind == REMOTE:
            return self.port
        return int(self.get("net_port") or 27960)

    @property
    def is_local(self) -> bool:
        return self.kind == LOCAL

    def duplicate(self, new_name: str | None = None) -> "Profile":
        return replace(
            self,
            id=uuid.uuid4().hex[:12],
            name=new_name or f"{self.name} (copy)",
            settings=dict(self.settings),
            mapcycle=list(self.mapcycle),
        )

    # -- Persistence --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "settings": dict(self.settings),
            "mapcycle": [e.to_dict() for e in self.cycle_entries()],
            "start_map": self.start_map,
            "extra_cfg": self.extra_cfg,
            "host": self.host,
            "port": self.port,
            "rcon_password": self.rcon_password,
            "dl_enabled": self.dl_enabled,
            "dl_port": self.dl_port,
            "dl_host": self.dl_host,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        known = {f for f in cls.__dataclass_fields__}
        payload = {k: v for k, v in data.items() if k in known}
        # Older profiles stored the rotation as a plain list of map names.
        payload["mapcycle"] = [
            CycleEntry.from_any(e) for e in payload.get("mapcycle", [])
        ]
        profile = cls(**payload)

        # Earlier versions stored the generated download URL in the settings.
        # That address went stale as soon as the machine's address changed or
        # the download server stopped, and the game server carried on
        # advertising it -- which a joining player sees as a missing map. It is
        # generated at launch now, so any stored copy is dropped.
        if profile.dl_enabled:
            profile.settings.pop("sv_dlURL", None)

        return profile

    # -- Import from an existing server.cfg ---------------------------------

    @classmethod
    def from_cfg(cls, text: str, name: str = "Imported Server") -> "Profile":
        """Build a profile from a hand-written server config.

        Unrecognised directives are preserved in ``extra_cfg`` so importing
        never silently drops behaviour the operator relied on.
        """
        profile = cls(name=name)
        leftovers: list[str] = []

        for raw in text.replace("\r", "").splitlines():
            line = raw.split("//")[0].rstrip()
            if not line.strip():
                continue

            map_match = _MAP_RE.match(line)
            if map_match:
                profile.start_map = map_match.group(1).lower()
                continue

            set_match = _SET_RE.match(line)
            if set_match:
                _, quoted_name, bare_name, quoted, bare = set_match.groups()
                cvar_name = quoted_name if quoted_name is not None else bare_name
                value = quoted if quoted is not None else (bare or "")
                if cvars.get(cvar_name) is not None:
                    profile.set(cvar_name, value)
                else:
                    profile.settings[cvar_name] = value
                continue

            first = line.strip().split()[0].lower()
            if first not in _ACTION_DIRECTIVES:
                leftovers.append(line.strip())

        profile.extra_cfg = "\n".join(leftovers)
        return profile

    @classmethod
    def from_cfg_file(cls, path: Path, name: str | None = None) -> "Profile":
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return cls.from_cfg(text, name or Path(path).stem)

    # -- Validation ---------------------------------------------------------

    def problems(self, custom_maps: int = 0) -> list[str]:
        """Configuration issues worth warning about before launch.

        ``custom_maps`` is the number of custom .pk3 packs the profile carries;
        the caller supplies it because the model does not read the filesystem.
        """
        issues: list[str] = []

        if custom_maps and not self.dl_enabled:
            url = str(self.get("sv_dlURL") or "").strip()
            default_url = cvars.get("sv_dlURL").default
            if not url or url == default_url:
                issues.append(
                    f"This server carries {custom_maps} custom map pack"
                    f"{'s' if custom_maps != 1 else ''}, but has no download source "
                    "for them. Players who do not already have the map will fail to "
                    "join. Turn on the download server under Custom Maps, or set a "
                    "Download URL pointing at a host that has the files."
                )

        port = self.net_port
        if not 1024 <= port <= 65535:
            issues.append(f"Port {port} is outside the usable range 1024-65535.")

        if self.kind == REMOTE and not self.rcon_password:
            issues.append("A remote server needs an rcon password to be controlled.")

        max_clients = int(self.get("sv_maxclients") or 0)
        private = int(self.get("sv_privateClients") or 0)
        if private >= max_clients and max_clients:
            issues.append(
                f"Private slots ({private}) leave no public slots out of {max_clients}."
            )
        if private and not self.get("sv_privatePassword"):
            issues.append("Private slots are reserved but no private password is set.")

        if self.mapcycle and self.start_map not in self.cycle_map_names():
            issues.append(
                f"Start map {self.start_map} is not in the map cycle; "
                "the rotation will jump elsewhere after the first map."
            )

        if self.dl_enabled:
            if not 1 <= self.dl_port <= 65535:
                issues.append(f"Download server port {self.dl_port} is not a valid port.")
            if self.dl_port == port:
                issues.append(
                    f"The download server and the game server are both set to "
                    f"{port}. They can share a number only by accident: the game "
                    "server is UDP and the download server is TCP, but using two "
                    "different ports avoids confusion in firewall rules."
                )
            if not self.get("sv_allowdownload"):
                issues.append(
                    "The download server is on but 'Allow downloads' is off, so "
                    "clients will never fetch anything."
                )

        length = serverinfo_length(self)
        if length > INFOSTRING_LIMIT:
            issues.append(
                f"Server info string is {length} characters, over the {INFOSTRING_LIMIT} "
                "limit. The server may fail to start; shorten the hostname or MOTD."
            )
        elif length > INFOSTRING_WARN:
            issues.append(
                f"Server info string is {length} characters, close to the "
                f"{INFOSTRING_LIMIT} limit."
            )

        return issues


#: Quake 3 refuses to start when the serverinfo string overflows. The example
#: config leads with a warning about exactly this, so it is checked up front.
INFOSTRING_LIMIT = 1024
INFOSTRING_WARN = 900


def serverinfo_length(profile: Profile) -> int:
    """Approximate length of the serverinfo string this profile produces.

    Only cvars broadcast to the browser (``sets``) plus the handful the engine
    always includes contribute, which is what actually overflows in practice.
    """
    always = (
        "sv_hostname", "sv_maxclients", "g_gametype", "sv_privateClients",
        "g_needpass", "mapname", "version", "protocol", "gamename",
    )
    total = 0
    for cvar in cvars.REGISTRY:
        broadcast = cvar.setter == "sets" or cvar.name in always
        if not broadcast:
            continue
        value = profile.get(cvar.name)
        total += len(cvar.name) + len(str(value)) + 2  # two backslash separators
    return total + 120  # engine-supplied keys not in the registry


# -- Store -------------------------------------------------------------------


class ProfileStore:
    """All profiles, persisted as a single JSON file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.profiles: list[Profile] = []

    def load(self) -> "ProfileStore":
        if not self.path.is_file():
            self.profiles = []
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.profiles = []
            return self
        self.profiles = [Profile.from_dict(d) for d in data.get("profiles", [])]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "profiles": [p.to_dict() for p in self.profiles]}
        # Write via a temporary file so an interrupted save cannot truncate
        # the only copy of every profile.
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, profile: Profile) -> Profile:
        self.profiles.append(profile)
        return profile

    def remove(self, profile_id: str) -> None:
        self.profiles = [p for p in self.profiles if p.id != profile_id]

    def by_id(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def port_conflicts(self) -> dict[int, list[str]]:
        """Local profiles that would fight over the same UDP port."""
        by_port: dict[int, list[str]] = {}
        for p in self.profiles:
            if p.is_local:
                by_port.setdefault(p.net_port, []).append(p.name)
        return {port: names for port, names in by_port.items() if len(names) > 1}


def default_profile(name: str = "My Server", installed_maps: list[str] | None = None) -> Profile:
    """A sensible starting profile: a CTF server on a short stock rotation."""
    profile = Profile(name=name)
    profile.set("sv_hostname", name)
    rotation = [m for m in (installed_maps or []) if m in _STOCK_ROTATION]
    profile.mapcycle = [CycleEntry(m) for m in (rotation or list(_STOCK_ROTATION))]
    profile.start_map = profile.mapcycle[0].map_name if profile.mapcycle else "ut4_casa"
    return profile


#: A short rotation of well-known stock maps, used for a new profile.
_STOCK_ROTATION = (
    "ut4_casa", "ut4_turnpike", "ut4_abbey", "ut4_prague",
    "ut4_uptown", "ut4_algiers", "ut4_ramelle",
)
