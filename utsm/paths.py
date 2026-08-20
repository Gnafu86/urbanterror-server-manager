"""Locating the Urban Terror installation and the manager's own storage.

The game install is treated as read-only. On a packaged Linux install it lives
under ``/opt/urbanterror`` owned by root, so nothing here ever writes into it:
generated configs, map cycles, logs and demos all go to a per-profile
``fs_homepath`` under the user's data directory instead. The engine searches
homepath before basepath, so a config written there is found by ``exec``.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

WINDOWS = sys.platform.startswith("win")

#: Where installs put the game, most likely first.
_UNIX_CANDIDATES = (
    "/opt/urbanterror",
    "/usr/share/urbanterror",
    "/usr/local/games/urbanterror",
    "~/.local/share/urbanterror",
    "~/UrbanTerror",
    "~/urbanterror",
    "~/.steam/steam/steamapps/common/UrbanTerror",
)

_WINDOWS_CANDIDATES = (
    r"C:\Program Files\UrbanTerror",
    r"C:\Program Files (x86)\UrbanTerror",
    r"C:\Games\UrbanTerror",
    r"C:\UrbanTerror",
    "~/UrbanTerror",
)

INSTALL_CANDIDATES: tuple[str, ...] = (
    _WINDOWS_CANDIDATES if WINDOWS else _UNIX_CANDIDATES
)

#: Dedicated server binary names, most likely first. The Windows builds ship
#: under their own names, so both sets are tried regardless of platform -- a
#: Windows install copied onto a Linux box is not worth failing over.
SERVER_BINARIES: tuple[str, ...] = (
    "urbanterror-ded",
    "Quake3-UrT-Ded.x86_64",
    "Quake3-UrT-Ded.i386",
    "Quake3-UrT-Ded.exe",
    "Quake3-UrT-Ded.x86.exe",
    "Quake3-UrT-Ded.x64.exe",
    "ioq3ded",
    "ioUrbanTerror.x86_64",
)

#: The mod directory every Urban Terror install carries.
MOD_DIR = "q3ut4"


@dataclass(frozen=True)
class Install:
    """A usable Urban Terror installation."""

    basepath: Path
    server_binary: Path

    @property
    def mod_path(self) -> Path:
        return self.basepath / MOD_DIR

    @property
    def is_writable(self) -> bool:
        return os.access(self.mod_path, os.W_OK)

    def pk3_files(self) -> list[Path]:
        if not self.mod_path.is_dir():
            return []
        return sorted(self.mod_path.glob("*.pk3"))


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def find_install(explicit: str | Path | None = None) -> Install | None:
    """Locate an installation, preferring an explicitly configured path.

    Falls back to resolving the ``urbanterror-server`` wrapper on PATH, which on
    Arch-family packages is a two-line shell script that cds into the real
    install directory.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(_expand(str(explicit)))
    candidates += [_expand(c) for c in INSTALL_CANDIDATES]

    wrapper = shutil.which("urbanterror-server") or shutil.which("urbanterror")
    if wrapper:
        found = _basepath_from_wrapper(Path(wrapper))
        if found:
            candidates.append(found)

    for base in candidates:
        if not base.is_dir() or not (base / MOD_DIR).is_dir():
            continue
        for name in SERVER_BINARIES:
            binary = base / name
            if not binary.is_file():
                continue
            # Windows has no execute bit; the extension is the marker there.
            if WINDOWS or os.access(binary, os.X_OK):
                return Install(base, binary)
    return None


def _basepath_from_wrapper(wrapper: Path) -> Path | None:
    """Read the ``cd <dir>`` out of a packaged launcher script."""
    try:
        text = wrapper.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("cd "):
            target = line[3:].strip().strip('"').strip("'").rstrip("/")
            if target:
                return Path(target)
    return None


# -- Manager storage ---------------------------------------------------------

APP_NAME = "utsm"


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or os.path.expanduser(fallback))


def config_dir() -> Path:
    """Where profiles.json lives.

    XDG on Unix; on Windows the environment variables are respected first so a
    test can redirect them, then APPDATA, which is where Windows applications
    are expected to keep settings.
    """
    if WINDOWS and not os.environ.get("XDG_CONFIG_HOME"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "UrbanTerrorServerManager"
    return _xdg("XDG_CONFIG_HOME", "~/.config") / APP_NAME


def data_dir() -> Path:
    """Where per-profile server directories live."""
    if WINDOWS and not os.environ.get("XDG_DATA_HOME"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "UrbanTerrorServerManager"
    return _xdg("XDG_DATA_HOME", "~/.local/share") / APP_NAME


def profiles_dir() -> Path:
    """Root of the per-profile ``fs_homepath`` directories."""
    return data_dir() / "profiles"


def profile_home(profile_id: str) -> Path:
    """The ``fs_homepath`` for one profile.

    Each profile gets its own tree so several servers can run at once without
    fighting over ``games.log``, the map cycle file or the demo folder.
    """
    return profiles_dir() / profile_id


def profile_mod_dir(profile_id: str) -> Path:
    """Where a profile's generated .cfg and mapcycle live."""
    return profile_home(profile_id) / MOD_DIR


def profiles_file() -> Path:
    """The JSON file holding every profile's settings."""
    return config_dir() / "profiles.json"


def ensure_dirs(profile_id: str | None = None) -> None:
    """Create the manager's directories. Safe to call repeatedly."""
    config_dir().mkdir(parents=True, exist_ok=True)
    profiles_dir().mkdir(parents=True, exist_ok=True)
    if profile_id:
        profile_mod_dir(profile_id).mkdir(parents=True, exist_ok=True)
