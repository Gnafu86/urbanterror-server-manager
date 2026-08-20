"""Discovering which maps a server can actually load.

Maps live as ``maps/<name>.bsp`` inside ``.pk3`` archives, which are ordinary
zip files. Scanning the archives is the only reliable way to know what is
installed, since there is no manifest.

Both the install's ``q3ut4`` directory and a profile's own ``fs_homepath`` are
scanned, because a profile may carry extra maps the base install does not have.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_BSP = re.compile(r"^maps/([^/]+)\.bsp$", re.IGNORECASE)

#: Entries that are .bsp files but not playable maps. Only content shipped
#: inside the official pk3s belongs here: ``ut4_jumpents`` is an entity
#: definition pack that every installation carries.
#:
#: Nothing derived from one machine's downloaded maps belongs here. An earlier
#: version listed two names found in a particular user's download folder, which
#: would have hidden those maps from anyone else who legitimately had them.
_NOT_PLAYABLE = frozenset({
    "ut4_jumpents",
})


@dataclass(frozen=True)
class GameMap:
    name: str
    source: Path

    @property
    def display_name(self) -> str:
        """``ut4_casa`` reads as ``Casa``."""
        stem = self.name[4:] if self.name.lower().startswith("ut4_") else self.name
        return stem.replace("_", " ").title()


def scan_pk3(path: Path) -> set[str]:
    """Map names inside one .pk3, or an empty set if it cannot be read."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return set()
    found = set()
    for entry in names:
        match = _BSP.match(entry)
        if match:
            found.add(match.group(1).lower())
    return found


def discover(*roots: Path, include_unplayable: bool = False) -> list[GameMap]:
    """Every map found under the given directories, sorted by name.

    Later roots win on name collisions, matching the engine's own precedence
    where a profile's homepath overrides the base install.
    """
    found: dict[str, Path] = {}
    for root in roots:
        if root is None or not Path(root).is_dir():
            continue
        for pk3 in sorted(Path(root).rglob("*.pk3")):
            for name in scan_pk3(pk3):
                if not include_unplayable and name in _NOT_PLAYABLE:
                    continue
                found[name] = pk3
    return [GameMap(name, src) for name, src in sorted(found.items())]


def names(maps: list[GameMap]) -> list[str]:
    return [m.name for m in maps]


# -- Map cycle file ----------------------------------------------------------
#
# The cycle file is one map name per line. The engine also supports per-map
# setting blocks in braces; those are preserved verbatim rather than parsed,
# so hand-written cycles survive a round-trip through this editor.


@dataclass(frozen=True)
class CycleEntry:
    """One position in the rotation, optionally with its own gametype.

    ``gametype`` of ``None`` means the map plays in the server's configured
    gametype.
    """

    map_name: str
    gametype: int | None = None

    def to_dict(self) -> dict:
        return {"map": self.map_name, "gametype": self.gametype}

    @classmethod
    def from_any(cls, value) -> "CycleEntry":
        """Accept a bare map name or a stored entry.

        Profiles written before per-map gametypes existed hold a plain list of
        names, so those still load.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(value)
        if isinstance(value, dict):
            gt = value.get("gametype")
            return cls(str(value.get("map", "")), None if gt is None else int(gt))
        return cls(str(value))


def parse_cycle(text: str) -> list[str]:
    """Map names from a mapcycle file, ignoring comments and setting blocks."""
    out: list[str] = []
    depth = 0
    for raw in text.replace("\r", "").splitlines():
        line = raw.split("//")[0].strip()
        if not line:
            continue
        depth += line.count("{") - line.count("}")
        if depth > 0 or line in ("{", "}"):
            continue
        token = line.split()[0]
        if token and not token.startswith(("{", "}")):
            out.append(token)
    return out


def render_cycle(map_names: list[str]) -> str:
    """A mapcycle file body for the given rotation.

    Only map names are written. Per-map gametypes deliberately do not go in the
    setting block here: ``g_gametype`` is latched, and the block runs after the
    map has already loaded, so the value is accepted and then never applied.
    Measured on 4.3.4 -- ``timelimit`` set in a block takes effect, while
    ``g_gametype`` in the same block leaves the mode unchanged. The manager
    applies gametypes itself, before the map changes, where they do take.
    """
    if not map_names:
        return ""
    return "\n".join(map_names) + "\n"


# -- Custom map packs --------------------------------------------------------
#
# Custom maps are .pk3 files added to a profile's own q3ut4 directory. Putting
# them there does two jobs at once: the game server loads them from its
# fs_homepath, and the download server offers them to joining clients from the
# same path.


@dataclass(frozen=True)
class CustomPk3:
    """A .pk3 the user added to a profile."""

    path: Path
    maps: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def size_label(self) -> str:
        size = float(self.size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    @property
    def download_path(self) -> str:
        """Where a client will ask for this pack, relative to ``sv_dlURL``."""
        return f"/{self.path.parent.name}/{self.path.name}"

    def download_problem(self) -> str:
        """Why a client would refuse to auto-download this pack, if it would.

        Empty when the pack is fine.
        """
        if not self.maps:
            return ""
        stem = self.path.stem
        if len(self.maps) > 1:
            others = [m for m in self.maps if m != stem]
            if stem in self.maps:
                return (
                    f"Only '{stem}' can be auto-downloaded from this pack. Players "
                    f"missing {', '.join(others)} will not be able to join, because "
                    "a client only downloads a pack named after the map it needs."
                )
            return (
                f"None of the {len(self.maps)} maps in this pack can be "
                "auto-downloaded: a client only downloads a pack named after the "
                "map it needs, and one file cannot match several names. Repack "
                "them one map per .pk3."
            )
        only = self.maps[0]
        if stem != only:
            return (
                f"This pack must be named '{only}.pk3' for players to be able to "
                f"download it; it is currently '{self.path.name}'."
            )
        if not autodownloadable(only):
            return (
                f"Maps whose name starts with '{only[0]}' are never auto-downloaded "
                "by the client. Players must install this map by hand."
            )
        return ""


def list_custom(mod_dir: Path) -> list[CustomPk3]:
    """Every .pk3 a profile carries of its own, newest name order."""
    mod_dir = Path(mod_dir)
    if not mod_dir.is_dir():
        return []
    packs = []
    for pk3 in sorted(mod_dir.glob("*.pk3")):
        packs.append(CustomPk3(pk3, tuple(sorted(scan_pk3(pk3)))))
    return packs


class MapInstallError(Exception):
    """A .pk3 could not be added."""


#: Map names the client refuses to auto-download, whatever the pack is called.
#: ``CL_FirstDownload`` only proceeds when the first letter of the map name is
#: a-y or A-Y; ``z`` is excluded because the base game's own packs are z-prefixed.
def autodownloadable(map_name: str) -> bool:
    first = (map_name or " ")[0]
    return ("a" <= first <= "y") or ("A" <= first <= "Y")


def expected_pack_name(map_name: str) -> str:
    """The only filename a client will auto-download this map from."""
    return f"{map_name}.pk3"


def install_pk3(
    source: Path,
    mod_dir: Path,
    overwrite: bool = False,
    rename_for_download: bool = True,
) -> Path:
    """Copy a .pk3 into a profile's mod directory.

    A single-map pack is renamed to ``<mapname>.pk3``, because that is the only
    name a client will auto-download it under. The client builds the list of
    packs it needs from ``sv_referencedPakNames``, then in ``CL_FirstDownload``
    keeps only the entry matching ``/<mapname>.pk3@`` and discards the rest::

        s = strstr(clc.downloadList, va("/%s.pk3@", clc.mapname));
        if (s) { ...keep it... }
        else { clc.downloadList[0] = '\\0'; }

    So a pack called ``mymap_v2.pk3`` or ``mymap_autopacked.pk3`` is dropped
    from the list, no request is ever made, and the player is told only that
    the .bsp is missing. Renaming on the way in is what makes auto-download
    work at all.

    Multi-map packs cannot be renamed to suit every map they hold; the caller is
    expected to warn about that.
    """
    source = Path(source)
    mod_dir = Path(mod_dir)

    if source.suffix.lower() != ".pk3":
        raise MapInstallError(f"{source.name} is not a .pk3 file.")
    if not source.is_file():
        raise MapInstallError(f"{source} does not exist.")
    if not zipfile.is_zipfile(source):
        raise MapInstallError(
            f"{source.name} is not a valid .pk3 archive (a .pk3 is a zip file)."
        )

    mod_dir.mkdir(parents=True, exist_ok=True)

    name = source.name
    if rename_for_download:
        contained = sorted(scan_pk3(source))
        if len(contained) == 1:
            name = expected_pack_name(contained[0])
    target = mod_dir / name

    if target.exists() and not overwrite:
        raise MapInstallError(f"{source.name} is already installed.")
    if target.resolve() == source.resolve():
        return target

    shutil.copy2(source, target)
    return target


def remove_pk3(path: Path) -> None:
    """Delete a custom .pk3 from a profile."""
    path = Path(path)
    if path.suffix.lower() != ".pk3":
        raise MapInstallError("Refusing to delete something that is not a .pk3.")
    path.unlink(missing_ok=True)
