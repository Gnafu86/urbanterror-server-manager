"""How the manager talks to a server, whichever side of the network it is on.

A local server is a child process, so commands go down its stdin and output
comes back on stdout. A remote server is reachable only by rcon over UDP, where
each command carries the password and the reply arrives in the same exchange.

Both are expressed as a :class:`ControlChannel` so that every admin action --
kick, map change, live cvar edit -- is written once and works in both cases.
The difference that does leak through is :attr:`ControlChannel.echoes`: a local
channel gets output asynchronously on the console stream, while rcon returns the
reply directly from :meth:`send`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .query import QueryError, send_oob


class ChannelError(Exception):
    """A command could not be delivered."""


class ControlChannel(ABC):
    """A way to issue console commands to a server."""

    #: True when replies arrive on a separate output stream rather than being
    #: returned by :meth:`send`.
    echoes: bool = False

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether commands can be sent right now."""

    @abstractmethod
    def send(self, command: str, quiet: bool = False) -> str | None:
        """Run one console command. Returns the reply when the transport has one.

        ``quiet`` marks housekeeping the user did not ask for -- the periodic
        ``status`` poll behind the player list, for instance -- so it is not
        echoed into the console alongside their own commands.
        """

    # -- Shared admin vocabulary --------------------------------------------
    #
    # These are the commands the game module registers, confirmed against the
    # command table in qagame.qvm.

    def say(self, message: str) -> str | None:
        return self.send(f'say "{_clean(message)}"')

    def tell(self, client: int, message: str) -> str | None:
        return self.send(f'tell {int(client)} "{_clean(message)}"')

    def bigtext(self, message: str) -> str | None:
        return self.send(f'bigtext "{_clean(message)}"')

    def set_cvar(self, name: str, value: str) -> str | None:
        return self.send(f'set {name} "{_clean(str(value))}"')

    def change_map(self, mapname: str) -> str | None:
        return self.send(f"map {_token(mapname)}")

    def cycle_map(self) -> str | None:
        return self.send("cyclemap")

    def restart_map(self) -> str | None:
        return self.send("map_restart")

    def reload_map(self) -> str | None:
        return self.send("reload")

    def kick(self, client: int, reason: str = "") -> str | None:
        if reason:
            return self.send(f'clientkickreason {int(client)} "{_clean(reason)}"')
        return self.send(f"clientkick {int(client)}")

    def ban(self, client: int) -> str | None:
        return self.send(f"addip {int(client)}")

    def ban_for(self, client: int, minutes: int) -> str | None:
        return self.send(f"addipexpire {int(client)} {int(minutes)}")

    def unban(self, address: str) -> str | None:
        return self.send(f"removeip {_token(address)}")

    def mute(self, client: int, seconds: int | None = None) -> str | None:
        if seconds is None:
            return self.send(f"mute {int(client)}")
        return self.send(f"mute {int(client)} {int(seconds)}")

    def slap(self, client: int) -> str | None:
        return self.send(f"slap {int(client)}")

    def nuke(self, client: int) -> str | None:
        return self.send(f"nuke {int(client)}")

    def smite(self, client: int) -> str | None:
        return self.send(f"smite {int(client)}")

    def force_team(self, client: int, team: str) -> str | None:
        return self.send(f"forceteam {int(client)} {_token(team)}")

    def swap_teams(self) -> str | None:
        return self.send("swapteams")

    def shuffle_teams(self) -> str | None:
        return self.send("shuffleteams")

    def balance_teams(self) -> str | None:
        return self.send("balanceteams")

    def pause(self) -> str | None:
        return self.send("pause")

    def veto(self) -> str | None:
        return self.send("veto")

    def status(self) -> str | None:
        return self.send("status")


def _clean(text: str) -> str:
    """Strip characters that would end the quoted argument or chain a command."""
    return str(text).replace('"', "'").replace(";", ",").replace("\n", " ").replace("\r", " ")


def _token(text: str) -> str:
    """Reduce a value to a bare single token, for unquoted arguments."""
    return _clean(text).split()[0] if str(text).strip() else ""


class NullChannel(ControlChannel):
    """Stands in for a server that is not running. Sending is a no-op."""

    @property
    def available(self) -> bool:
        return False

    def send(self, command: str, quiet: bool = False) -> str | None:
        raise ChannelError("The server is not running.")


class RconChannel(ControlChannel):
    """Commands over UDP rcon, for servers this manager did not launch.

    The password travels in clear text in every packet, which is how the Quake 3
    rcon protocol works and cannot be fixed here. It is worth knowing before
    pointing this at a server across the public internet.
    """

    def __init__(self, host: str, port: int, password: str, timeout: float = 2.0):
        self.host = host
        self.port = int(port)
        self.password = password
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.password)

    def send(self, command: str, quiet: bool = False) -> str | None:
        # Rcon replies come back on the socket rather than the console, so a
        # quiet command needs no special handling here.
        if not self.password:
            raise ChannelError("No rcon password is set for this server.")
        payload = f"rcon {self.password} {command}".encode("utf-8", errors="replace")
        try:
            reply = send_oob(self.host, self.port, payload, self.timeout, expect_multiple=True)
        except QueryError as exc:
            raise ChannelError(str(exc)) from exc

        text = reply.replace(b"\xff\xff\xff\xff", b"").decode("utf-8", errors="replace")
        if text.startswith("print\n"):
            text = text[len("print\n"):]
        if "Bad rconpassword" in text or "Invalid password" in text:
            raise ChannelError("The server rejected the rcon password.")
        return text
