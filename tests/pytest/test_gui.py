"""Local GUI contracts; these tests do not run Renode."""
from contextlib import contextmanager
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import threading

import pytest

from cuberange import ports
from cuberange.gui import catalog
from cuberange.gui.server import make_server, validate_host

ASSETS = Path(__file__).resolve().parents[2] / "src/cuberange/gui/web_assets"


def test_gui_ports_registered():
    assigned = ports.all_assigned()
    assert assigned[ports.server_mode()] == "server_mode"
    assert assigned[ports.gui()] == "gui"
    indexed = {function(sat) for function in (ports.link, ports.injector, ports.channel)
               for sat in range(ports.MAX_SATELLITES)}
    assert ports.server_mode() != ports.gui()
    assert not indexed & {ports.server_mode(), ports.gui()}


def test_catalog_reads_real_exercises():
    entries = catalog.exercises()
    assert {entry["dir"] for entry in entries} == {
        readme.parent.name for readme in catalog.EXERCISES.glob("*/README.md")}
    assert all(entry["body"].strip() for entry in entries)
    first = catalog.get_exercise("EX-B01")
    assert first["id"] == "EX-B01"
    assert first["title"] == "Kill the radio from the internal bus"
    assert first["layer"] == "internal bus"
    assert first["scenario"] == "scenario.resc"
    assert catalog.get_exercise("unknown") is None


@contextmanager
def running_server():
    server = make_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def request(server, path, **headers):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()
    finally:
        connection.close()


def test_http_routes_and_guards():
    with running_server() as server:
        status, kind, body = request(server, "/")
        assert status == 200 and "text/html" in kind and b"CubeRange" in body
        status, kind, body = request(server, "/api/config")
        assert status == 200 and "application/json" in kind
        assert json.loads(body) == {
            "renode_ws_url": f"ws://127.0.0.1:{ports.server_mode()}",
            "renode_terminal_base": f"http://127.0.0.1:{ports.server_mode()}",
            "gui_port": ports.gui()}
        assert "EX-B01" in {entry["id"] for entry in json.loads(request(server, "/api/exercises")[2])}
        assert json.loads(request(server, "/api/exercises/EX-B01")[2])["body"].strip()
        for path in ("/api/exercises/UNKNOWN", "/../server.py", "/%2e%2e/server.py", "/web_assets/../server.py"):
            assert request(server, path)[0] == 404
        assert request(server, "/app.js")[1].startswith("text/javascript")
        assert request(server, "/style.css")[1].startswith("text/css")
        assert request(server, "/", Host="example.com")[0] == 403
        assert request(server, "/", Origin="http://example.com")[0] == 403


@pytest.mark.parametrize("host", ["8.8.8.8", "0.0.0.0", "localhost", "::1", "127.0.0.2"])
def test_host_validation_rejects_other_interfaces(host):
    with pytest.raises(ValueError):
        validate_host(host)


def test_host_validation_accepts_explicit_interfaces():
    assert validate_host("127.0.0.1") == "127.0.0.1"
    assert validate_host("100.64.0.1") == "100.64.0.1"


def test_assets_cover_design_surface_and_use_config():
    html = (ASSETS / "index.html").read_text()
    for pane in ("satellite", "console", "terminal", "exercise"):
        assert f'id="{pane}-pane"' in html
    js = (ASSETS / "app.js").read_text()
    names = ("status command exec-renode exec-monitor term-resize spawn kill machines uarts buttons "
             "leds button-set sensors sensor-get sensor-set uart-opened led-state-changed "
             "button-state-changed renode-quitted fs/list fs/mkdir fs/stat fs/dwnl").split()
    for name in names:
        assert f'"{name}"' in js
    assert 'SENSOR_SET_BLOCKLIST = new Set(["magnetic-flux-density"])' in js
    assert 'fetch("/api/config")' in js
    for port in ports.all_assigned():
        assert not re.search(rf"(?<![\w.]){port}(?![\w])", js)


def test_http_handler_without_network():
    """Also exercise actual HTTP parsing where the sandbox forbids socket creation."""
    from io import BytesIO
    from types import SimpleNamespace
    from cuberange.gui.server import Handler

    class Wire:
        def __init__(self, path):
            self.incoming = BytesIO(f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1:12345\r\n\r\n".encode())
            self.outgoing = bytearray()

        def makefile(self, *_):
            return self.incoming

        def sendall(self, data):
            self.outgoing.extend(data)

    server = SimpleNamespace(server_address=("127.0.0.1", 12345), server_port=12345)
    for path, expected in (("/", 200), ("/api/config", 200), ("/api/exercises", 200),
                           ("/api/exercises/EX-B01", 200), ("/api/exercises/UNKNOWN", 404),
                           ("/../server.py", 404), ("/app.js", 200), ("/style.css", 200)):
        wire = Wire(path)
        Handler(wire, ("127.0.0.1", 12346), server)
        head, body = bytes(wire.outgoing).split(b"\r\n\r\n", 1)
        assert f" {expected} ".encode() in head.split(b"\r\n")[0]
        assert body
        if path == "/api/config":
            assert json.loads(body)["gui_port"] == ports.gui()
