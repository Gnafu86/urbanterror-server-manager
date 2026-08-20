#!/usr/bin/env bash
# Install a launcher for the Urban Terror Server Manager.
#
#   ./install.sh              application menu entry and icons
#   ./install.sh --desktop    the above, plus a shortcut on the desktop
#   ./install.sh --uninstall  remove everything this script installs
#
# Nothing is copied into place: the launcher points back at this directory, so
# updating the checkout is enough to update the installed application.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="urbanterror-server-manager"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
ICON_SIZES=(256 128 64 48)

want_desktop=0
uninstall=0
for arg in "$@"; do
    case "$arg" in
        --desktop)   want_desktop=1 ;;
        --uninstall) uninstall=1 ;;
        -h|--help)   sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

desktop_dir() {
    if command -v xdg-user-dir >/dev/null 2>&1; then
        xdg-user-dir DESKTOP
    else
        echo "$HOME/Desktop"
    fi
}

refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 &&
        update-desktop-database "$APPS_DIR" 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 &&
        gtk-update-icon-cache -f -t "$ICON_ROOT" 2>/dev/null || true
}

# -- Uninstall ---------------------------------------------------------------

if [ "$uninstall" -eq 1 ]; then
    removed=0
    for f in "$DESKTOP_FILE" "$(desktop_dir)/$APP_ID.desktop"; do
        [ -e "$f" ] && rm -f "$f" && echo "removed $f" && removed=1
    done
    for size in "${ICON_SIZES[@]}"; do
        icon="$ICON_ROOT/${size}x${size}/apps/$APP_ID.png"
        [ -e "$icon" ] && rm -f "$icon" && removed=1
    done
    [ "$removed" -eq 1 ] && echo "removed icons" || echo "nothing was installed"
    refresh_caches
    exit 0
fi

# -- Checks ------------------------------------------------------------------

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

# -- Icons -------------------------------------------------------------------
# Drawn from packaging/make_icon.py rather than shipped as a binary, so it can
# be produced at whatever sizes the desktop asks for.

for size in "${ICON_SIZES[@]}"; do
    target_dir="$ICON_ROOT/${size}x${size}/apps"
    mkdir -p "$target_dir"
    python3 "$HERE/packaging/make_icon.py" "$target_dir/$APP_ID.png" "$size" >/dev/null
done
echo "installed icons (${ICON_SIZES[*]})"

# -- Desktop entry -----------------------------------------------------------

write_entry() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Urban Terror Server Manager
GenericName=Game Server Manager
Comment=Configure, launch and administer Urban Terror dedicated servers
Exec=python3 "$HERE/run.py"
Path=$HERE
Icon=$APP_ID
Terminal=false
Categories=Game;ActionGame;
Keywords=urbanterror;urt;quake;quake3;server;dedicated;
StartupNotify=true
EOF
    chmod +x "$1"
}

mkdir -p "$APPS_DIR"
write_entry "$DESKTOP_FILE"
echo "installed menu entry: $DESKTOP_FILE"

if [ "$want_desktop" -eq 1 ]; then
    DESK="$(desktop_dir)"
    if [ -d "$DESK" ]; then
        write_entry "$DESK/$APP_ID.desktop"
        # KDE and GNOME both refuse to run a desktop file that is not marked
        # executable; write_entry sets that already.
        if command -v gio >/dev/null 2>&1; then
            gio set "$DESK/$APP_ID.desktop" metadata::trusted true 2>/dev/null || true
        fi
        echo "installed desktop shortcut: $DESK/$APP_ID.desktop"
    else
        echo "warning: no desktop directory found at $DESK; skipped the shortcut" >&2
    fi
fi

refresh_caches

echo
echo "Run directly with: python3 $HERE/run.py"
echo "Uninstall with:    $HERE/install.sh --uninstall"
