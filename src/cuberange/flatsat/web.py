"""Local FlatSat experiment console; USB access stays in the bounded adapter.

The server binds to loopback by default or an explicitly chosen Tailscale IPv4
address. Its mutation API requires a per-server token and accepts typed
operations, never shell commands, arbitrary port paths, or host file paths.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
import uuid

from . import __main__ as cli
from .alerts import evaluate_capture, parse_limit, validate_limits
from .report import _render
from .room_watch import RoomDetector, compact_events, load_room_capture, run_room_watch, validate_config
from .telemetry import METRICS, load_capture
from .usb import FlatSatError, QUERY_COMMANDS, discover_ports, select_ports

MAX_BODY = 2 * 1024 * 1024
MAX_CAPTURE = 32 * 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}\Z")
_USB_LOCK = threading.Lock()
_ASSETS = Path(__file__).parent / "web_assets"
_TAILNET = ipaddress.IPv4Network("100.64.0.0/10")


class RequestError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validate_host(value: str) -> str:
    """Accept one explicit loopback or Tailscale IPv4 interface address."""
    if not isinstance(value, str):
        raise ValueError("host must be a literal IPv4 address: 127.0.0.1 or 100.64.0.0/10")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError("host must be a literal IPv4 address: 127.0.0.1 or 100.64.0.0/10") from exc
    if value != "127.0.0.1" and address not in _TAILNET:
        raise ValueError("host must be 127.0.0.1 or a Tailscale IPv4 address in 100.64.0.0/10")
    return str(address)


def default_data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "cuberange" / "flatsat" / "web"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError(f"{name} must be a number")
    try:
        valid = math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise RequestError(f"{name} must be between {minimum:g} and {maximum:g}")
    return float(value)


def _text(value, name: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise RequestError(f"{name} must be a string of at most {maximum} characters")
    return value


def _strict_json(text: str):
    def constant(value):
        raise ValueError(f"Non-finite JSON value: {value}")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("JSON number is outside the finite range")
        return number

    try:
        return json.loads(text, parse_constant=constant, parse_float=finite_float,
                          object_pairs_hook=object_pairs)
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc


def _job_request(body: dict) -> dict:
    task = body.get("task")
    if task not in ("info", "watch", "monitor", "room_watch"):
        raise RequestError("task must be info, watch, monitor, or room_watch")
    allowed = {"task", "serial", "port"}
    allowed |= {"duration"} if task == "monitor" else {"timeout"}
    if task == "info":
        allowed |= {"commands"}
    elif task == "watch":
        allowed |= {"duration", "interval", "label", "notes", "limits"}
    elif task == "room_watch":
        allowed |= {"duration", "interval", "label", "baseline_samples", "confirm_samples", "thresholds"}
    unknown = body.keys() - allowed
    if unknown:
        raise RequestError(f"Unknown fields: {', '.join(sorted(unknown))}")
    result = {"task": task, "port": body.get("port", "all" if task == "monitor" else "shell"),
              "serial": None}
    if result["port"] not in ("shell", "radio0", "radio1", "all"):
        raise RequestError("port must be shell, radio0, radio1, or all")
    if task != "monitor" and result["port"] != "shell":
        raise RequestError("info and watch require the shell interface")
    if "serial" in body:
        result["serial"] = _text(body["serial"], "serial", 128)
        if not result["serial"]:
            raise RequestError("serial must not be empty")
    if task in ("watch", "monitor"):
        result["duration"] = _finite(body.get("duration", 10), "duration", 0.2, 60)
    if task in ("watch", "info"):
        result["timeout"] = _finite(body.get("timeout", 2), "timeout", 0.05, 5)
    if task == "room_watch":
        result.update(duration=_finite(body.get("duration", 3600), "duration", 0.2, 86400),
                      interval=_finite(body.get("interval", 1), "interval", 0.2, 60),
                      timeout=_finite(body.get("timeout", 2), "timeout", 0.05, 5),
                      label=_text(body.get("label", ""), "label", 200))
        try:
            result.update(validate_config(baseline_samples=body.get("baseline_samples", 20),
                                          confirm_samples=body.get("confirm_samples", 3),
                                          thresholds=body.get("thresholds", {})))
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
    if task == "info":
        commands = body.get("commands", list(QUERY_COMMANDS[:3]))
        if (not isinstance(commands, list) or not 1 <= len(commands) <= 4 or
                any(not isinstance(command, str) or command not in QUERY_COMMANDS for command in commands) or
                len(set(commands)) != len(commands)):
            raise RequestError("commands must be unique fw_version/status/sensors/help queries")
        result["commands"] = commands
    if task == "watch":
        result["interval"] = _finite(body.get("interval", 1), "interval", 0.02, 60)
        result["label"] = _text(body.get("label", ""), "label", 200)
        notes = body.get("notes", [])
        if not isinstance(notes, list) or len(notes) > 16:
            raise RequestError("notes must be a list of at most 16 strings")
        result["notes"] = [_text(note, "note", 1000) for note in notes]
        limits = body.get("limits", [])
        if not isinstance(limits, list) or len(limits) > len(METRICS):
            raise RequestError("limits must be a list of metric:min:max strings")
        try:
            result["limits"] = validate_limits([parse_limit(_text(limit, "limit", 160)) for limit in limits])
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
    return result


class USBAdapter:
    """Injectable facade over the same operations as the FlatSat CLI."""
    def discover(self):
        return discover_ports()

    def run(self, request: dict, output: Path) -> int:
        task = request["task"]
        available = self.discover()
        if task == "monitor":
            # Unknown CDC interfaces are shown for diagnosis, but cannot be
            # opened through the console, including an "all" selection.
            available = [port for port in available if port.role in ("shell", "radio0", "radio1")]
        ports = select_ports(available, request["port"], request["serial"])
        if task == "info":
            return cli.run_info(ports[0], request["commands"], request["timeout"], output)
        if task == "watch":
            return cli.run_watch(ports[0], request["duration"], request["interval"],
                                 request["timeout"], output, label=request["label"],
                                 notes=request["notes"], limits=request["limits"])
        return cli.run_monitor(ports, request["duration"], output)

    def run_room_watch(self, request, output, stop_event, progress):
        port = select_ports(self.discover(), "shell", request["serial"])[0]
        return run_room_watch(port, request["duration"], request["interval"], request["timeout"], output,
                              baseline_samples=request["baseline_samples"],
                              confirm_samples=request["confirm_samples"], thresholds=request["thresholds"],
                              label=request["label"], stop_event=stop_event, progress=progress)


class FlatSatServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, data_dir: Path, adapter=None, *, host: str = "127.0.0.1"):
        self.host = validate_host(host)
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.adapter = adapter if adapter is not None else USBAdapter()
        self.csrf_token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.jobs: list[dict] = []
        self.worker: threading.Thread | None = None
        self.closing = False
        self.capture_cache: dict[str, tuple[int, int, dict]] = {}
        try:
            super().__init__((self.host, port), Handler)
        except OSError as exc:
            raise OSError(exc.errno, f"Cannot bind FlatSat web console to {self.host}:{port}: {exc.strerror}") from exc
        self.authority = f"{self.host}:{self.server_address[1]}"
        self.url = f"http://{self.authority}/"

    def assert_capture_ready(self, capture_id: str) -> None:
        with self.lock:
            if any(job["capture_id"] == capture_id and job["status"] == "running"
                   for job in self.jobs):
                raise RequestError("Recording is still in progress", 409)

    def capture_path(self, capture_id: str) -> Path:
        if not _ID.fullmatch(capture_id):
            raise RequestError("Capture not found", 404)
        path = self.data_dir / f"{capture_id}.jsonl"
        if path.is_symlink() or not path.is_file():
            raise RequestError("Capture not found", 404)
        if path.stat().st_size > MAX_CAPTURE:
            raise RequestError("Capture exceeds the 32 MiB analysis limit", 413)
        return path

    def _capture_data(self, capture_id: str) -> dict:
        path = self.capture_path(capture_id)
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        with self.lock:
            cached = self.capture_cache.get(capture_id)
            if cached and cached[:2] == signature:
                return cached[2]
        try:
            # Strict JSON validation also covers metadata fields which the
            # existing sample validator intentionally leaves untouched.
            records = [_strict_json(line) for line in path.read_text(encoding="utf-8").splitlines()]
            data = load_capture(path)
            session = data["session"]
            if not session:
                raise ValueError("Capture requires a session event")
            if session.get("mode") not in ("watch", "info", "monitor", "room_watch"):
                raise ValueError("Capture session mode must be watch, info, monitor, or room_watch")
            if "label" in session and session["label"] is not None:
                _text(session["label"], "label", 200)
            if not isinstance(session.get("ports", []), list) or any(
                    not isinstance(port, dict) for port in session.get("ports", [])):
                raise ValueError("session.ports must be a list of objects")
            if not isinstance(session.get("notes", []), list) or any(
                    not isinstance(note, str) for note in session.get("notes", [])):
                raise ValueError("session.notes must be a list of strings")
            alerts = evaluate_capture(data) if session.get("limits") else None
            queries = [record for record in records if record.get("event") == "query_result"]
            for query in queries:
                for field, expected in (("answered", bool), ("text", str), ("command", str)):
                    if field in query and not isinstance(query[field], expected):
                        raise ValueError(f"query_result.{field} has an invalid type")
            received_bytes = 0
            for record in records:
                if record.get("event") != "rx":
                    continue
                raw_hex = record.get("raw_hex")
                if (not isinstance(raw_hex, str) or len(raw_hex) % 2 or
                        re.fullmatch(r"[0-9a-fA-F]*", raw_hex) is None):
                    raise ValueError("rx.raw_hex must be an even-length hexadecimal string")
                received_bytes += len(raw_hex) // 2
            result = {"id": capture_id, "session": session, "summary": data["summary"],
                      "alerts": alerts, "samples": data["samples"], "queries": queries,
                      "received_bytes": received_bytes,
                      "report_url": f"/reports/{capture_id}",
                      "raw_url": f"/api/captures/{capture_id}/raw", "_data": data}
            if session["mode"] == "room_watch":
                room = load_room_capture(path)
                data["room_watch"] = room
                result["room_watch"] = {"snapshot": room["snapshot"], "events": compact_events(room["events"])}
            # Check all API values before admitting a capture into the index.
            json.dumps(result, allow_nan=False)
        except (FlatSatError, OSError, ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise RequestError(f"Invalid capture: {exc}") from exc
        with self.lock:
            self.capture_cache[capture_id] = (*signature, result)
            if len(self.capture_cache) > 200:
                self.capture_cache.pop(next(iter(self.capture_cache)))
        return result

    def capture_entry(self, capture_id: str) -> dict:
        data = self._capture_data(capture_id)
        summary, session = data["summary"], data["session"]
        entry = {"id": capture_id, "label": session.get("label") or session.get("mode"),
                "mode": session.get("mode"), "time_utc": session.get("time_utc"),
                "sample_count": summary["sample_count"], "valid_count": summary["valid_count"],
                "failed_count": summary["failed_count"], "completion": summary["quality"]["completion"],
                "report_url": data["report_url"], "raw_url": data["raw_url"]}
        if "room_watch" in data:
            entry["event_count"] = data["room_watch"]["snapshot"]["event_count"]
        return entry

    def _job_snapshot(self, job: dict) -> dict:
        result = {key: value for key, value in job.items() if not key.startswith("_")}
        result["elapsed_s"] = round((job.get("_finished", time.monotonic()) - job["_started"]), 3)
        result["progress"] = {"sample_count": 0, "valid_count": 0, "last_sample": None}
        if job["task"] == "room_watch":
            return result
        if job["status"] == "running":
            # Read only a bounded tail. A partial last line is ignored until the
            # CLI flushes it, and no raw USB text enters the live API.
            path = self.data_dir / f"{job['capture_id']}.jsonl"
            try:
                first_index = None
                covered_valid = 0
                if path.is_symlink():
                    return result
                with path.open("rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 65536))
                    for line in stream.read(65536).splitlines():
                        try:
                            record = _strict_json(line.decode("utf-8"))
                        except (ValueError, UnicodeError):
                            continue
                        if isinstance(record, dict) and record.get("event") == "sensor_sample":
                            if first_index is None:
                                first_index = record.get("index")
                            result["progress"]["sample_count"] = record.get("index", 0)
                            result["progress"]["last_sample"] = record
                            covered_valid += record.get("status") == "ok"
                # A truncated tail cannot say how many earlier samples failed.
                result["progress"]["valid_count"] = covered_valid if first_index in (None, 1) else None
            except OSError:
                pass
        else:
            try:
                data = self._capture_data(job["capture_id"])
                result["progress"] = {"sample_count": data["summary"]["sample_count"],
                                      "valid_count": data["summary"]["valid_count"],
                                      "last_sample": data["samples"][-1] if data["samples"] else None}
            except RequestError:
                pass
        return result

    def state(self) -> dict:
        discovery_error = None
        try:
            devices = [{**asdict(port), "board_id": port.board_id} for port in self.adapter.discover()]
        except (FlatSatError, OSError, ValueError) as exc:
            devices, discovery_error = [], str(exc)
        with self.lock:
            jobs = [self._job_snapshot(job) for job in reversed(self.jobs)]
            active_ids = {job["capture_id"] for job in jobs if job["status"] == "running"}
        captures = []
        for path in sorted(self.data_dir.glob("*.jsonl"), key=lambda path: path.name, reverse=True):
            if path.stem in active_ids or not _ID.fullmatch(path.stem):
                continue
            try:
                captures.append(self.capture_entry(path.stem))
            except RequestError:
                continue
        captures.sort(key=lambda capture: str(capture["time_utc"] or ""), reverse=True)
        return {"devices": devices, "discovery_error": discovery_error,
                "active_job": next((job for job in jobs if job["status"] == "running"), None),
                "recent_jobs": jobs[:20], "captures": captures[:200],
                "csrf_token": self.csrf_token, "metrics": METRICS}

    def start_job(self, request: dict) -> dict:
        with self.lock:
            if self.closing:
                raise RequestError("Server is shutting down", 503)
            if not _USB_LOCK.acquire(blocking=False):
                raise RequestError("A USB operation is already running", 409)
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "task": request["task"], "status": "running",
                   "started_at": _now(), "ended_at": None, "duration_s": request.get("duration"),
                   "capture_id": job_id, "error": None, "exit_code": None,
                   "_started": time.monotonic()}
            if request["task"] == "room_watch":
                job.update(stop_requested=False, _stop_event=threading.Event(),
                           room_watch=RoomDetector(baseline_samples=request["baseline_samples"],
                                                   confirm_samples=request["confirm_samples"],
                                                   thresholds=request["thresholds"]).snapshot())
            self.jobs.append(job)
            self.jobs = self.jobs[-100:]
            self.worker = threading.Thread(target=self._run_job, args=(job, request),
                                           name=f"flatsat-{job_id[:8]}", daemon=False)
            try:
                self.worker.start()
            except BaseException:
                self.jobs.remove(job)
                _USB_LOCK.release()
                raise
            return self._job_snapshot(job)

    def _run_job(self, job: dict, request: dict) -> None:
        code, error = None, None
        try:
            output = self.data_dir / f"{job['capture_id']}.jsonl"
            if request["task"] == "room_watch":
                def progress(snapshot):
                    with self.lock:
                        job["room_watch"] = snapshot
                code = self.adapter.run_room_watch(request, output, job["_stop_event"], progress)
            else:
                code = self.adapter.run(request, output)
            if code != 0:
                error = "USB operation did not complete successfully; inspect its recording"
            if (self.data_dir / f"{job['capture_id']}.jsonl").exists():
                data = self._capture_data(job["capture_id"])
                if "room_watch" in data:
                    with self.lock:
                        job["room_watch"] = data["room_watch"]["snapshot"]
                    if code != 0:
                        error = "Room watch ended: " + str(data["room_watch"]["snapshot"].get("reason"))
        except Exception as exc:
            error = str(exc)
            if request["task"] == "room_watch":
                with self.lock:
                    job["room_watch"] = {**job["room_watch"], "phase": "stopped", "reason": "device_error"}
        finally:
            with self.lock:
                job.update(status="failed" if error else "completed", ended_at=_now(),
                           exit_code=code, error=error, _finished=time.monotonic())
                _USB_LOCK.release()

    def stop_job(self, job_id):
        if not _ID.fullmatch(job_id):
            raise RequestError("Job not found", 404)
        with self.lock:
            job = next((job for job in self.jobs if job["id"] == job_id), None)
            if job is None:
                raise RequestError("Job not found", 404)
            if job["task"] != "room_watch":
                raise RequestError("Only room watch jobs support stopping", 409)
            if job["status"] == "running":
                job["stop_requested"] = True
                job["_stop_event"].set()
            return self._job_snapshot(job)

    def import_capture(self, body: dict) -> dict:
        unknown = body.keys() - {"name", "content"}
        if unknown:
            raise RequestError(f"Unknown fields: {', '.join(sorted(unknown))}")
        if "name" in body:
            _text(body["name"], "name", 200)
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RequestError("content must contain a JSONL capture")
        if len(content.encode("utf-8")) > MAX_BODY:
            raise RequestError("Capture upload exceeds 2 MiB", 413)
        capture_id = uuid.uuid4().hex
        path = self.data_dir / f"{capture_id}.jsonl"
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
            return self.capture_entry(capture_id)
        except BaseException:
            path.unlink(missing_ok=True)
            with self.lock:
                self.capture_cache.pop(capture_id, None)
            raise

    def server_close(self):
        with self.lock:
            self.closing = True
            for job in self.jobs:
                if job["task"] == "room_watch" and job["status"] == "running":
                    job["stop_requested"] = True
                    job["_stop_event"].set()
            worker = self.worker
        super().server_close()
        if worker and worker is not threading.current_thread():
            worker.join()


class Handler(BaseHTTPRequestHandler):
    server: FlatSatServer
    protocol_version = "HTTP/1.0"

    def log_message(self, *_):
        # Tokens and imported USB content never enter access logs.
        pass

    def _guard(self, *, mutation: bool = False) -> None:
        if ipaddress.IPv4Address(self.server.host) in _TAILNET:
            try:
                validate_host(self.client_address[0])
            except (ValueError, TypeError, IndexError) as exc:
                raise RequestError("Peer must use a Tailscale IPv4 or local loopback address", 403) from exc
        hosts = self.headers.get_all("Host", [])
        if hosts != [self.server.authority]:
            raise RequestError("Host must match the configured server address", 403)
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [f"http://{self.server.authority}"]:
            raise RequestError("Cross-origin requests are not allowed", 403)
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            raise RequestError("Cross-site requests are not allowed", 403)
        if mutation:
            tokens = self.headers.get_all("X-CubeRange-Token", [])
            if len(tokens) != 1 or not secrets.compare_digest(tokens[0], self.server.csrf_token):
                raise RequestError("Invalid local session token", 403)

    def _send(self, status: int, body: bytes, content_type: str, *, filename=None,
              standalone: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        policy = "'unsafe-inline'" if standalone else "'self'"
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; "
                         f"script-src {policy}; style-src {policy}; "
                         "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: dict) -> None:
        self._send(status, json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        if self.headers.get("Transfer-Encoding") is not None:
            raise RequestError("Transfer-Encoding is not supported")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise RequestError("Content-Type must be application/json", 415)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            raise RequestError("One Content-Length header is required", 411)
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise RequestError("Invalid Content-Length") from exc
        if not 1 <= length <= MAX_BODY:
            raise RequestError("Request body must be between 1 byte and 2 MiB", 413)
        self.connection.settimeout(5)
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request body")
            body = _strict_json(raw.decode("utf-8"))
        except (ValueError, UnicodeError, OSError) as exc:
            raise RequestError(f"Invalid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise RequestError("JSON body must be an object")
        return body

    def do_GET(self):
        try:
            self._guard()
            parsed = urlsplit(self.path)
            path = parsed.path
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise RequestError("Unsupported URL", 404)
            if path == "/api/exercises" and not parsed.query:
                from .catalog import list_exercises
                self._json(200, {"exercises": list_exercises()})
                return
            if match := re.fullmatch(r"/api/exercises/(EX-[A-Z][0-9]{2})/readme", path):
                from .catalog import read_exercise
                if parsed.query not in ("", "lang=ja", "lang=en"):
                    raise RequestError("lang must be ja or en")
                try:
                    markdown = read_exercise(match[1], "en" if parsed.query == "lang=en" else "ja")
                except ValueError as exc:
                    raise RequestError(str(exc), 404) from exc
                self._send(200, markdown.encode("utf-8"), "text/plain; charset=utf-8")
                return
            if parsed.query:
                raise RequestError("Unsupported URL", 404)
            if path == "/api/state":
                self._json(200, self.server.state())
                return
            if match := re.fullmatch(r"/api/captures/([0-9a-f]{32})(/raw)?", path):
                capture_id, raw = match.groups()
                self.server.assert_capture_ready(capture_id)
                if raw:
                    self._send(200, self.server.capture_path(capture_id).read_bytes(),
                               "application/x-ndjson", filename=f"flatsat-{capture_id}.jsonl")
                else:
                    data = self.server._capture_data(capture_id)
                    self._json(200, {key: value for key, value in data.items() if key != "_data"})
                return
            if match := re.fullmatch(r"/reports/([0-9a-f]{32})(\.jsonl)?", path):
                capture_id = match[1]
                self.server.assert_capture_ready(capture_id)
                if match[2]:
                    self._send(200, self.server.capture_path(capture_id).read_bytes(),
                               "application/x-ndjson", filename=f"flatsat-{capture_id}.jsonl")
                    return
                data = self.server._capture_data(capture_id)
                document = dict(data["_data"])
                if data["alerts"]:
                    document["alerts"] = data["alerts"]
                html = _render(document, self.server.capture_path(capture_id),
                               self.server.data_dir / f"{capture_id}.html")
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", standalone=True)
                return
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in assets:
                filename, content_type = assets[path]
                asset = _ASSETS / filename
                if asset.is_file():
                    self._send(200, asset.read_bytes(), content_type)
                    return
            raise RequestError("Not found", 404)
        except RequestError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (OSError, ValueError, TypeError) as exc:
            self._json(500, {"error": str(exc)})

    def do_POST(self):
        try:
            self._guard(mutation=True)
            body = self._body()
            if self.path == "/api/jobs":
                self._json(202, {"job": self.server.start_job(_job_request(body))})
            elif match := re.fullmatch(r"/api/jobs/([0-9a-f]{32})/stop", self.path):
                if body:
                    raise RequestError("Stop body must be an empty object")
                job = self.server.stop_job(match[1])
                self._json(202 if job["status"] == "running" else 200, {"job": job})
            elif self.path == "/api/captures/import":
                self._json(201, {"capture": self.server.import_capture(body)})
            else:
                raise RequestError("Not found", 404)
        except RequestError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (OSError, ValueError, TypeError) as exc:
            self._json(500, {"error": str(exc)})

    def do_OPTIONS(self):
        self._json(405, {"error": "CORS is not enabled"})


def make_server(*, host: str = "127.0.0.1", port: int = 0,
                data_dir: Path | None = None, adapter=None) -> FlatSatServer:
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    return FlatSatServer(port, data_dir if data_dir is not None else default_data_dir(), adapter, host=host)


def serve(*, host: str = "127.0.0.1", port: int = 8765, data_dir: Path | None = None) -> int:
    server = make_server(host=host, port=port, data_dir=data_dir)
    print(f"FlatSat web console: {server.url}", flush=True)
    print(f"Recording directory: {server.data_dir}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nWaiting for the bounded USB operation to finish…", flush=True)
    finally:
        server.server_close()
    return 0
