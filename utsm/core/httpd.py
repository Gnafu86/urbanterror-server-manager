"""A small HTTP server for client-side map auto-download.

When a player joins a server running a map they do not have, the client fetches
it over HTTP from ``sv_dlURL``. The engine advertises its paks with the mod
prefix attached -- ``sv_referencedPakNames`` reads ``q3ut4/zUrT43_008`` -- so the
client requests::

    <sv_dlURL>/q3ut4/<pakname>.pk3

Serving a profile's own ``fs_homepath`` therefore satisfies both jobs at once:
the game server loads custom maps from ``<homepath>/q3ut4/`` and the client
downloads them from the same place.

Only ``.pk3`` files are served, and that restriction is load-bearing rather than
tidiness. That same directory holds the generated server config, which contains
``rconpassword`` in clear text. A general-purpose static file server pointed at
it would hand out administrative control of the server to anyone who guessed the
filename.
"""

from __future__ import annotations

import socket
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QObject, Signal

#: The only extension ever served. See the module docstring.
ALLOWED_SUFFIX = ".pk3"

DEFAULT_PORT = 8000

WINDOWS = sys.platform.startswith("win")


def _claim_port(sock: socket.socket) -> None:
    """Apply the socket options that make a port clash fail loudly.

    ``SO_REUSEADDR`` means opposite things on the two platforms. On Unix it only
    permits rebinding a port stuck in TIME_WAIT, which is what a restarted
    server wants. On Windows it permits binding a port another socket is
    *actively listening on*, so two servers would silently share it and requests
    would land unpredictably on either. Windows' ``SO_EXCLUSIVEADDRUSE`` asks
    for the exclusive ownership Unix gives by default.
    """
    if WINDOWS:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def local_ip() -> str:
    """The address of the interface used to reach the outside world.

    Opening a UDP socket to a public address makes the routing table pick the
    interface; no packet is actually sent. Falls back to loopback when there is
    no route, which at least keeps the URL well-formed.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("198.51.100.1", 9))   # TEST-NET-3, guaranteed unroutable
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def port_available(port: int, host: str = "0.0.0.0") -> bool:
    """Whether a TCP port can be bound right now.

    Uses the same socket options as the real server, so the answer predicts what
    an actual start would do rather than being a separate, looser check.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _claim_port(sock)
        sock.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


class _Pk3Handler(BaseHTTPRequestHandler):
    """Serves .pk3 files out of one directory, and nothing else."""

    server_version = "UTSM-DL"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # Injected by the owning server.
    root: Path
    log_line = None

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._serve(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
        self._serve(send_body=False)

    # -- Resolution ---------------------------------------------------------

    def _resolve(self) -> Path | None:
        """Map a request path to a file, or None if it must not be served."""
        raw = unquote(urlparse(self.path).path).lstrip("/")
        if not raw:
            return None

        # Extension whitelist first: nothing else in this tree is public, and
        # the server config sitting beside the maps holds the rcon password.
        if not raw.lower().endswith(ALLOWED_SUFFIX):
            return None

        root = self.root.resolve()
        candidate = (root / raw).resolve()

        # Containment check, so "../" or an absolute path cannot escape.
        if not candidate.is_relative_to(root):
            return None
        if not candidate.is_file():
            return None
        return candidate

    # -- Serving ------------------------------------------------------------

    def _serve(self, send_body: bool) -> None:
        path = self._resolve()
        if path is None:
            self._deny(HTTPStatus.NOT_FOUND)
            self._note(f"404 {self.path}")
            return

        try:
            size = path.stat().st_size
        except OSError:
            self._deny(HTTPStatus.NOT_FOUND)
            return

        start, end = self._range(size)
        if start is None:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        partial = (start, end) != (0, size - 1)
        length = end - start + 1

        self.send_response(
            HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK
        )
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        self._note(f"{'206' if partial else '200'} {path.name} ({length} bytes)")

        if not send_body:
            return
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # A player cancelled the download or dropped out; not an error.
            pass

    def _range(self, size: int) -> tuple[int | None, int]:
        """Parse a single byte range, so interrupted downloads can resume."""
        header = self.headers.get("Range")
        if not header or not header.startswith("bytes="):
            return 0, max(size - 1, 0)
        spec = header[len("bytes="):].split(",")[0].strip()
        try:
            if spec.startswith("-"):
                length = int(spec[1:])
                if length <= 0:
                    return None, 0
                return max(size - length, 0), size - 1
            first, _, last = spec.partition("-")
            start = int(first)
            end = int(last) if last else size - 1
        except ValueError:
            return None, 0
        if start >= size or start > end:
            return None, 0
        return start, min(end, size - 1)

    def _deny(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _note(self, message: str) -> None:
        if callable(self.log_line):
            self.log_line(f"{self.client_address[0]}  {message}")

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - http.server API
        """Silence the default stderr logging; output goes through _note."""


class _DownloadHTTPServer(ThreadingHTTPServer):
    """The listening socket, with port clashes made to fail on both platforms."""

    daemon_threads = True
    # socketserver applies this before bind; see _claim_port for why Windows
    # must not get SO_REUSEADDR.
    allow_reuse_address = not WINDOWS

    def server_bind(self) -> None:
        _claim_port(self.socket)
        super().server_bind()


class DownloadServer(QObject):
    """Runs the map download server on a background thread."""

    started = Signal(int)
    stopped = Signal()
    failed = Signal(str)
    #: One line describing a served request, for the console.
    activity = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._root: Path | None = None
        self._port = 0

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._port

    @property
    def root(self) -> Path | None:
        return self._root

    def start(self, root: Path, port: int = DEFAULT_PORT, host: str = "0.0.0.0") -> bool:
        if self.is_running:
            return True

        root = Path(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.failed.emit(f"Could not use {root}: {exc}")
            return False

        # staticmethod, or Python binds the callable as a method and passes the
        # handler instance as the first argument.
        handler = type(
            "_BoundPk3Handler",
            (_Pk3Handler,),
            {
                "root": root,
                "log_line": staticmethod(lambda message: self.activity.emit(message)),
            },
        )

        try:
            httpd = _DownloadHTTPServer((host, int(port)), handler)
        except OSError as exc:
            self.failed.emit(
                f"Could not listen on port {port}: {exc}. "
                "Another program may already be using it."
            )
            return False

        self._httpd = httpd
        self._root = root
        self._port = httpd.server_address[1]

        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name=f"utsm-download-{self._port}",
            daemon=True,
        )
        self._thread.start()
        self.started.emit(self._port)
        return True

    def stop(self) -> None:
        if self._httpd is None:
            return
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        httpd.shutdown()
        httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._port = 0
        self.stopped.emit()

    def url(self, host: str | None = None) -> str:
        """The value to put in ``sv_dlURL`` for this server."""
        return f"http://{host or local_ip()}:{self._port or DEFAULT_PORT}"
