"""Read-only stdlib HTTP shell for the browser's direct Renode connection."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from cuberange import ports
from . import catalog

_ASSETS = Path(__file__).parent / "web_assets"
_TAILNET = ipaddress.IPv4Network("100.64.0.0/10")


def validate_host(value: str) -> str:
    """Accept explicit loopback or Tailscale IPv4 literals, like flatsat.web."""
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise ValueError("host must be a literal loopback or Tailscale IPv4 address") from exc
    if value != "127.0.0.1" and address not in _TAILNET:
        raise ValueError("host must be 127.0.0.1 or a Tailscale IPv4 address")
    return str(address)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if ipaddress.IPv4Address(self.server.server_address[0]) in _TAILNET:
            try:
                validate_host(self.client_address[0])
            except ValueError:
                self._send(403, {"error": "Peer must use Tailscale or loopback IPv4"})
                return
        authority = f"{self.server.server_address[0]}:{self.server.server_port}"
        if (self.headers.get_all("Host", []) != [authority] or
                self.headers.get_all("Origin", []) not in ([], [f"http://{authority}"]) or
                self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")):
            self._send(403, {"error": "Host or origin does not match server"})
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if parsed.scheme or parsed.netloc or ".." in path.split("/") or "\\" in path:
            self._send(404, {"error": "Not found"})
        elif path == "/api/config":
            base = f"127.0.0.1:{ports.server_mode()}"
            self._send(200, {"renode_ws_url": f"ws://{base}",
                             "renode_terminal_base": f"http://{base}", "gui_port": ports.gui()})
        elif path == "/api/exercises":
            self._send(200, catalog.list_exercises())
        elif path.startswith("/api/exercises/"):
            entry = catalog.get_exercise(path.removeprefix("/api/exercises/"))
            self._send(200 if entry else 404, entry or {"error": "Unknown exercise"})
        else:
            assets = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                      "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
            if path not in assets:
                self._send(404, {"error": "Not found"})
                return
            filename, kind = assets[path]
            self._send(200, (_ASSETS / filename).read_bytes(), kind + "; charset=utf-8")


def make_server(*, host="127.0.0.1", port=None):
    if port is None:
        port = ports.gui()
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("invalid port")
    return ThreadingHTTPServer((validate_host(host), port), Handler)
