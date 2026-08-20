# Urban Terror Server Manager

A desktop GUI for configuring, launching and administering Urban Terror 4.3
dedicated servers. Every server option is a form control, so there is no need to
hand-edit `server.cfg`, memorise the `g_gear` letter set, or work out the
`g_allowvote` bitmask.

![Settings](docs/screenshot-settings.png)

## Requirements

- Python 3.10+
- PySide6 — `sudo pacman -S pyside6` (Arch/CachyOS), `sudo apt install python3-pyside6` (Debian/Ubuntu), or `pip install --user PySide6`
- An Urban Terror 4.3 installation with a dedicated server binary

Nothing else. Everything beyond PySide6 comes from the standard library.

## Running

```bash
python3 run.py
```

To add a desktop launcher:

```bash
./install.sh
```

The installation is found automatically in the usual places (`/opt/urbanterror`,
`/usr/share/urbanterror`, `~/UrbanTerror`, …), including by resolving an
`urbanterror-server` wrapper on `PATH`. If it is somewhere else you are asked
for the folder once.

## What it does

**Every server option, as a form.** Around 130 cvars across Identity, Network &
Slots, Admin & Security, Authentication, Logging, Gameplay, Teams and Match
Mode, plus per-gametype pages that appear and disappear as you change the
gametype — a Capture the Flag server is not cluttered with bomb defuse timers.
Help text is carried over from the shipped `server_example.cfg`. Options the
engine only applies on map reload are badged as such rather than appearing to
have taken effect.

**Weapons and items.** A checkbox grid instead of the `g_gear` letter set.
Ticked equipment is allowed. The raw value stays visible and editable.

**Voting.** A checklist instead of the `g_allowvote` bitmask, with the votes
that hand players broad control (`exec` above all) called out separately.

**Map cycle.** A reorderable rotation built from the maps actually installed,
read out of the `.pk3` archives.

**Custom maps, with a download server.** Add `.pk3` files to a server and they
are copied into its own map folder, so the server can load them and they show up
in the rotation. Tick *Serve custom maps over HTTP* and a built-in web server
hands them to joining players, setting `sv_dlURL` for you. It starts and stops
with the game server, and shows each download as it happens.

The URL shown is built from your LAN address; on a LAN that is all you need. Behind
a router, enter your public address and forward the TCP port. To use a web host
you already have instead, leave the built-in server off and set **Download URL**
under Network & Slots — files must be reachable at `<your URL>/q3ut4/<pack>.pk3`,
which is the path the engine tells clients to use.

> The download server serves **only** `.pk3` files. That is deliberate: the same
> folder holds the generated server config, which contains your rcon password in
> clear text. Path traversal is blocked and directory listings are not offered.

**Start, stop, restart.** Plus map reload, next map, and live editing: changing
an option on a running server applies it immediately when the engine allows it.

**Console and players.** The real server console with history and tab
completion, and a live player list with kick, ban, mute, slap, nuke, smite and
force-team from the right-click menu.

**Remote servers.** Add a server by host, port and rcon password to configure
and administer it the same way. It cannot be started or stopped, since it is not
running here.

## How it works

Servers run as child processes of the manager, so **closing the manager stops
them**. You are warned first. Their console is the child's own stdin and stdout,
which means no rcon password is needed to administer a server you launched here.

Each profile gets its own `fs_homepath` under
`~/.local/share/utsm/profiles/<id>/`, holding its generated config, map cycle,
logs and demos. The game installation is never written to — on a packaged Linux
install it is root-owned and read-only. Several servers can run at once without
sharing a log file or a map cycle.

Profiles are stored as JSON in `~/.config/utsm/profiles.json`; the `.cfg` is
generated from a profile at launch. An existing `server.cfg` can be imported,
and any directive the manager does not model is preserved and still written out.

## Tests

```bash
python3 -m pytest tests/ -q
```

The suite launches real dedicated servers on high ports (27965–27990) to check
that generated configs are honoured, that start/stop/restart leaves no orphaned
processes, and that rcon works. Those tests skip automatically when no
installation is present.

## A note on two lookup tables

`g_gear`'s letters and `g_allowvote`'s bits are documented on a site that now
blocks automated access, so both were recovered from the game's own files:

- The gear table comes from `ui/menudef.h` inside `zUrT43_020.pk3`, which
  defines `ITEM_KNIFE = 1` through `ITEM_MAGNUM = 33`. It reproduces the
  long-published letters (`F` = HK69, `K` = HE grenade, `N` = kevlar, `R` =
  laser). The six weapons added in 4.3 continue into lowercase, which is the one
  part not corroborated by a shipped file.
- The vote order comes from the usage string inside `qagame.qvm`, and decoding
  the shipped default of `603981055` under it gives a coherent set.

Both editors show the raw value, so a wrong letter or bit can never block you,
and correcting either is a one-line change in `utsm/model/gear.py` or
`utsm/model/votes.py`.

## Layout

```
run.py                    entry point
utsm/paths.py             install discovery, XDG directories
utsm/model/cvars.py       the registry every form is generated from
utsm/model/gear.py        g_gear letter set
utsm/model/votes.py       g_allowvote bitmask
utsm/model/maps.py        .pk3 scanning, map cycle files
utsm/model/profile.py     profiles, .cfg import, validation
utsm/core/supervisor.py   process lifecycle, stdin console
utsm/core/channel.py      shared admin commands; rcon
utsm/core/query.py        UDP getstatus/getinfo, status parsing
utsm/core/cfgwriter.py    profile to .cfg and mapcycle
utsm/core/httpd.py        map download server (.pk3 only)
utsm/ui/                  the Qt interface
```

Adding a server option means adding one row to `utsm/model/cvars.py`. The form
builds itself from that registry.
