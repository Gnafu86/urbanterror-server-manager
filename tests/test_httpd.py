"""Tests for the custom map download server.

The security tests here are not incidental. The directory served is a profile's
own ``fs_homepath``, which holds the generated server config containing
``rconpassword`` in clear text. If anything but a ``.pk3`` can be fetched, the
download server hands out administrative control of the game server.
"""

from __future__ import annotations

import socket
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utsm.core import httpd  # noqa: E402
from utsm.model import maps  # noqa: E402


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def make_pk3(path: Path, map_names: tuple[str, ...] = ("ut4_custom",)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name in map_names:
            archive.writestr(f"maps/{name}.bsp", b"BSP\x00" + b"x" * 512)
    return path


@pytest.fixture
def served(app, tmp_path):
    """A running download server over a realistic profile directory."""
    root = tmp_path / "profile"
    mod = root / "q3ut4"
    make_pk3(mod / "custom_maps.pk3", ("ut4_custom", "ut4_another"))

    # The things that must never be reachable.
    (mod / "utsm_abc123.cfg").write_text(
        'set rconpassword "super-secret"\nset sv_hostname "X"\n', encoding="utf-8"
    )
    (root / "secrets.txt").write_text("do not serve me", encoding="utf-8")

    server = httpd.DownloadServer()
    port = free_port()
    assert server.start(root, port), "server did not start"
    yield server, root, port
    server.stop()


def fetch(port: int, path: str, headers: dict | None = None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    return urllib.request.urlopen(request, timeout=5)


# -- Serving -----------------------------------------------------------------

def test_serves_a_pk3_at_the_path_the_client_asks_for(served):
    """The engine advertises paks as 'q3ut4/<name>', so that is the URL."""
    _server, root, port = served
    response = fetch(port, "/q3ut4/custom_maps.pk3")
    assert response.status == 200
    body = response.read()
    assert body == (root / "q3ut4" / "custom_maps.pk3").read_bytes()
    assert response.headers["Content-Type"] == "application/octet-stream"


def test_head_returns_the_size_without_a_body(served):
    _server, root, port = served
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/q3ut4/custom_maps.pk3", method="HEAD"
    )
    response = urllib.request.urlopen(request, timeout=5)
    expected = (root / "q3ut4" / "custom_maps.pk3").stat().st_size
    assert int(response.headers["Content-Length"]) == expected
    assert response.read() == b""


def test_missing_pk3_is_a_404(served):
    _server, _root, port = served
    with pytest.raises(urllib.error.HTTPError) as exc:
        fetch(port, "/q3ut4/not_here.pk3")
    assert exc.value.code == 404


def test_range_request_allows_resuming(served):
    """Large map packs over flaky connections get resumed rather than restarted."""
    _server, root, port = served
    whole = (root / "q3ut4" / "custom_maps.pk3").read_bytes()

    response = fetch(port, "/q3ut4/custom_maps.pk3", {"Range": "bytes=10-19"})
    assert response.status == 206
    assert response.read() == whole[10:20]
    assert response.headers["Content-Range"] == f"bytes 10-19/{len(whole)}"


def test_advertises_range_support(served):
    _server, _root, port = served
    assert fetch(port, "/q3ut4/custom_maps.pk3").headers["Accept-Ranges"] == "bytes"


# -- Security ----------------------------------------------------------------

def test_never_serves_the_server_config(served):
    """That file contains the rcon password."""
    _server, _root, port = served
    with pytest.raises(urllib.error.HTTPError) as exc:
        fetch(port, "/q3ut4/utsm_abc123.cfg")
    assert exc.value.code == 404


def test_never_serves_a_non_pk3_file(served):
    _server, _root, port = served
    for path in ("/secrets.txt", "/q3ut4/../secrets.txt", "/q3ut4/custom_maps.pk3.bak"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch(port, path)
        assert exc.value.code == 404, f"{path} must not be served"


def test_rejects_path_traversal(served):
    _server, _root, port = served
    for path in (
        "/../../../../etc/passwd",
        "/q3ut4/../../../../etc/hosts",
        "/%2e%2e/%2e%2e/etc/passwd",
    ):
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch(port, path)
        assert exc.value.code == 404, f"{path} must not escape the root"


def test_traversal_cannot_reach_a_pk3_outside_the_root(app, tmp_path):
    """The extension whitelist alone is not enough; containment is checked too."""
    outside = tmp_path / "outside"
    make_pk3(outside / "private.pk3")

    root = tmp_path / "profile"
    (root / "q3ut4").mkdir(parents=True)

    server = httpd.DownloadServer()
    port = free_port()
    assert server.start(root, port)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch(port, "/../outside/private.pk3")
        assert exc.value.code == 404
    finally:
        server.stop()


def test_directory_listing_is_not_offered(served):
    _server, _root, port = served
    for path in ("/", "/q3ut4/", "/q3ut4"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch(port, path)
        assert exc.value.code == 404


# -- Lifecycle ---------------------------------------------------------------

def test_start_stop_frees_the_port(app, tmp_path):
    server = httpd.DownloadServer()
    port = free_port()
    assert server.start(tmp_path, port)
    assert server.is_running
    assert not httpd.port_available(port, "127.0.0.1")

    server.stop()
    assert not server.is_running


def test_starting_on_a_busy_port_reports_instead_of_raising(app, tmp_path):
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]

    failures: list[str] = []
    server = httpd.DownloadServer()
    server.failed.connect(failures.append)
    try:
        assert server.start(tmp_path, port) is False
        assert failures and str(port) in failures[0]
        assert not server.is_running
    finally:
        blocker.close()
        server.stop()


def test_url_uses_the_bound_port(app, tmp_path):
    server = httpd.DownloadServer()
    port = free_port()
    server.start(tmp_path, port)
    try:
        assert server.url("example.com") == f"http://example.com:{port}"
    finally:
        server.stop()


def test_local_ip_is_an_address():
    parts = httpd.local_ip().split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)


# -- Installing custom maps --------------------------------------------------

def test_install_pk3_copies_and_keeps_the_name(tmp_path):
    """The client asks for the exact filename the server advertises."""
    source = make_pk3(tmp_path / "incoming" / "ut4_mymap.pk3")
    mod = tmp_path / "profile" / "q3ut4"
    installed = maps.install_pk3(source, mod)
    assert installed == mod / "ut4_mymap.pk3"
    assert installed.read_bytes() == source.read_bytes()


def test_install_pk3_rejects_a_non_pk3(tmp_path):
    bogus = tmp_path / "notamap.txt"
    bogus.write_text("nope")
    with pytest.raises(maps.MapInstallError):
        maps.install_pk3(bogus, tmp_path / "mod")


def test_install_pk3_rejects_a_corrupt_archive(tmp_path):
    """A .pk3 is a zip; something merely renamed would break the server."""
    fake = tmp_path / "broken.pk3"
    fake.write_bytes(b"this is not a zip file")
    with pytest.raises(maps.MapInstallError) as exc:
        maps.install_pk3(fake, tmp_path / "mod")
    assert "not a valid" in str(exc.value)


def test_install_pk3_refuses_to_overwrite_silently(tmp_path):
    source = make_pk3(tmp_path / "in" / "m.pk3")
    mod = tmp_path / "mod"
    maps.install_pk3(source, mod)
    with pytest.raises(maps.MapInstallError) as exc:
        maps.install_pk3(source, mod)
    assert "already installed" in str(exc.value)
    maps.install_pk3(source, mod, overwrite=True)   # explicit is fine


def test_list_custom_reports_the_maps_inside(tmp_path):
    mod = tmp_path / "q3ut4"
    make_pk3(mod / "pack.pk3", ("ut4_one", "ut4_two"))
    packs = maps.list_custom(mod)
    assert len(packs) == 1
    assert packs[0].maps == ("ut4_one", "ut4_two")
    assert packs[0].download_path == "/q3ut4/pack.pk3"
    assert packs[0].size > 0


def test_remove_pk3_refuses_other_files(tmp_path):
    victim = tmp_path / "important.cfg"
    victim.write_text("x")
    with pytest.raises(maps.MapInstallError):
        maps.remove_pk3(victim)
    assert victim.exists()
