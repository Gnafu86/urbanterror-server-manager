"""Quake 3 out-of-band UDP queries against a running server.

Every out-of-band packet is four ``0xFF`` bytes followed by a command. Two are
useful here:

``getstatus``
    Returns the full serverinfo plus one line per connected player. This is how
    the manager reads live state, and it works identically for a server it
    launched and one on the far side of the internet.

``getinfo``
    A smaller reply with just enough for a browser listing. Used as a cheap
    liveness check.

Player names carry ``^n`` colour codes, which :func:`strip_colors` removes for
display while the raw name is kept for admin commands that need an exact match.
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass

OOB_PREFIX = b"\xff\xff\xff\xff"

#: ``^`` followed by any single character is a colour escape in Quake 3.
_COLOR_RE = re.compile(r"\^.")

DEFAULT_TIMEOUT = 1.5


def strip_colors(text: str) -> str:
    """Remove Quake 3 ``^n`` colour codes from a name or message."""
    return _COLOR_RE.sub("", text)


@dataclass(frozen=True)
class Player:
    name: str
    score: int
    ping: int

    @property
    def display_name(self) -> str:
        return strip_colors(self.name)

    @property
    def is_connecting(self) -> bool:
        # The engine reports a ping of zero for a client still loading.
        return self.ping == 0


@dataclass(frozen=True)
class ServerStatus:
    """A snapshot of a running server."""

    info: dict[str, str]
    players: tuple[Player, ...]

    @property
    def hostname(self) -> str:
        return strip_colors(self.info.get("sv_hostname", ""))

    @property
    def mapname(self) -> str:
        return self.info.get("mapname", "")

    @property
    def gametype(self) -> int:
        try:
            return int(self.info.get("g_gametype", "0"))
        except ValueError:
            return 0

    @property
    def max_clients(self) -> int:
        try:
            return int(self.info.get("sv_maxclients", "0"))
        except ValueError:
            return 0

    @property
    def player_count(self) -> int:
        return len(self.players)


class QueryError(Exception):
    """A server did not answer, or answered with something unusable."""


def _parse_infostring(text: str) -> dict[str, str]:
    """``\\key\\value\\key\\value`` into a dict."""
    parts = text.split("\\")
    if parts and not parts[0]:
        parts = parts[1:]
    return {parts[i]: parts[i + 1] for i in range(0, len(parts) - 1, 2)}


def _parse_player(line: str) -> Player | None:
    """``<score> <ping> "<name>"`` into a player."""
    match = re.match(r'^\s*(-?\d+)\s+(\d+)\s+"(.*)"\s*$', line)
    if not match:
        return None
    return Player(name=match.group(3), score=int(match.group(1)), ping=int(match.group(2)))


def send_oob(
    host: str,
    port: int,
    command: bytes,
    timeout: float = DEFAULT_TIMEOUT,
    expect_multiple: bool = False,
) -> bytes:
    """Send one out-of-band packet and collect the reply.

    Rcon replies can arrive as several datagrams, so ``expect_multiple`` keeps
    reading until the socket goes quiet rather than stopping at the first one.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(OOB_PREFIX + command, (host, port))
        chunks: list[bytes] = []
        while True:
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                break
            chunks.append(data)
            if not expect_multiple:
                break
        if not chunks:
            raise QueryError(f"No reply from {host}:{port}")
        return b"".join(chunks)
    except OSError as exc:
        raise QueryError(f"Could not reach {host}:{port}: {exc}") from exc
    finally:
        sock.close()


def _strip_oob(payload: bytes, expected: str) -> str:
    """Drop the out-of-band framing and the reply keyword, then decode.

    The prefix has to go before decoding: 0xFF is not valid UTF-8, so decoding
    first turns each byte into a replacement character and the prefix can no
    longer be recognised. Every occurrence is removed rather than just a leading
    one, because a reply split across datagrams carries the prefix on each.
    """
    payload = payload.replace(OOB_PREFIX, b"")
    text = payload.decode("utf-8", errors="replace")
    if expected and text.lstrip().startswith(expected):
        text = text.lstrip()[len(expected):]
    return text.lstrip("\n")


def get_status(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> ServerStatus:
    """Full status: serverinfo plus the player list."""
    payload = send_oob(host, port, b"getstatus\x00", timeout)
    body = _strip_oob(payload, "statusResponse")
    lines = body.split("\n")
    info = _parse_infostring(lines[0] if lines else "")
    players = tuple(p for p in (_parse_player(l) for l in lines[1:]) if p is not None)
    return ServerStatus(info=info, players=players)


def get_info(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> dict[str, str]:
    """The short browser-listing reply. Cheaper than a full status."""
    payload = send_oob(host, port, b"getinfo utsm\x00", timeout)
    info = _parse_infostring(_strip_oob(payload, "infoResponse").split("\n")[0])
    if not info:
        # Something answered but it was not a server infostring. Treating this
        # as success would make liveness checks pass against anything at all.
        raise QueryError(f"Unrecognised reply from {host}:{port}")
    return info


def is_alive(host: str, port: int, timeout: float = 0.6) -> bool:
    """Whether a server is answering on this port."""
    try:
        get_info(host, port, timeout)
        return True
    except QueryError:
        return False


# -- The `status` console command --------------------------------------------
#
# `getstatus` over UDP gives names, scores and pings but not client numbers,
# and every admin command (kick, mute, slap, forceteam) addresses a client by
# number. The `status` console command is the only source of that mapping, so
# its output is parsed too.
#
#   num score ping name            lastmsg address               qport rate
#   --- ----- ---- --------------- ------- --------------------- ----- -----
#     0    12   45 Player^7               0 203.0.113.5:27960     12345 25000

_STATUS_ROW = re.compile(
    r"^\s*(\d+)\s+(-?\d+)\s+(\d+)\s+(.*?)\s+(\d+)\s+"
    r"((?:\d{1,3}\.){3}\d{1,3}(?::\d+)?|loopback|bot)\s+(\d+)\s+(\d+)\s*$"
)


@dataclass(frozen=True)
class StatusClient:
    """A connected client, including the number admin commands address it by."""

    number: int
    name: str
    score: int
    ping: int
    address: str

    @property
    def display_name(self) -> str:
        return strip_colors(self.name)

    @property
    def is_bot(self) -> bool:
        return self.address in ("bot", "loopback")


def parse_status(text: str) -> list[StatusClient]:
    """Client rows from the output of the ``status`` console command.

    Tolerates the surrounding console noise, so it can be run over a stream of
    server output without first isolating the block.
    """
    clients: list[StatusClient] = []
    for line in text.replace("\r", "").splitlines():
        match = _STATUS_ROW.match(line)
        if not match:
            continue
        number, score, ping, name, _lastmsg, address, _qport, _rate = match.groups()
        clients.append(
            StatusClient(
                number=int(number),
                name=name.strip(),
                score=int(score),
                ping=int(ping),
                address=address,
            )
        )
    return clients
