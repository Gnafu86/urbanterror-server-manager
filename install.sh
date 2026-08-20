#!/usr/bin/env bash
# Install a desktop launcher for the Urban Terror Server Manager.
#
# Nothing is copied: the launcher points at this directory, so pulling updates
# here is enough. Removing the entry again is a single rm, printed at the end.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPS_DIR/urbanterror-server-manager.desktop"
ICON="/usr/share/pixmaps/urbanterror.png"

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is not installed." >&2
    exit 1
fi

if ! python3 -c "import PySide6" >/dev/null 2>&1; then
    cat >&2 <<'MSG'
error: PySide6 is not installed.

  Arch / CachyOS : sudo pacman -S pyside6
  Debian/Ubuntu  : sudo apt install python3-pyside6
  Any distro     : pip install --user PySide6
MSG
    exit 1
fi

[ -f "$ICON" ] || ICON="applications-games"

mkdir -p "$APPS_DIR"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Urban Terror Server Manager
GenericName=Game Server Manager
Comment=Configure, launch and administer Urban Terror dedicated servers
Exec=python3 "$HERE/run.py"
Path=$HERE
Icon=$ICON
Terminal=false
Categories=Game;ActionGame;Settings;
Keywords=urbanterror;quake;server;dedicated;
EOF

chmod +x "$DESKTOP_FILE"
command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$APPS_DIR" 2>/dev/null || true

echo "Installed launcher: $DESKTOP_FILE"
echo "Run directly with:  python3 $HERE/run.py"
echo "Uninstall with:     rm $DESKTOP_FILE"
