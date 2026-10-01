"""Real HTTP tests of the local console, using a USB facade that never opens USB."""
from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urljoin, urlsplit

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.flatsat import web  # noqa: E402
from cuberange.flatsat.telemetry import parse_sensor_response  # noqa: E402
from cuberange.flatsat.usb import FlatSatError, FlatSatPort  # noqa: E402

SENSOR_TEXT = "Accel: x=1 mg y=2 mg z=1000 mg\nTemp: 28 C\nPress: 101000 Pa\nHumid: 45 %"
PORTS = [FlatSatPort(f"/dev/fake-{role}", role, "BOARD-A", f"Cat-{role}", "1-1", True)
         for role in ("shell", "radio0", "radio1")]


def capture_text(*, mode="watch", label="test run", limits=None, samples=2):
    records = [{"event": "session", "time_utc": "2026-10-01T00:00:00+00:00",
                "mode": mode, "label": label, "ports": [], "notes": [], "limits": limits or []}]
    if mode == "watch":
        for index in range(1, samples + 1):
            records.append({"event": "sensor_sample", "time_utc": "2026-10-01T00:00:01+00:00",
                            "index": index, "elapsed_s": index / 10, "response_ms": 3,
                            "completion": "complete", **parse_sensor_response(SENSOR_TEXT)})
    elif mode == "info":
        records.append({"event": "query_result", "command": "status", "answered": True,
                        "text": "status\r\nRadioManager State: LISTENING", "completion": "quiet",
                        "response_ms": 5})
    else:
        records.append({"event": "rx", "role": "radio0", "raw_hex": "6162"})
    records.append({"event": "end", "received_bytes": 2 if mode == "monitor" else 0,
                    "reason": "duration_complete"})
    return "\n".join(json.dumps(record) for record in records) + "\n"


class FakeAdapter:
    def __init__(self, gate=None, failure=None, code=0):
        self.calls = []
        self.gate = gate
        self.failure = failure
        self.code = code
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.discovery_failure = None

    def discover(self):
        if self.discovery_failure:
            raise FlatSatError(self.discovery_failure)
        return PORTS

    def run(self, request, output):
        self.calls.append(request)
        self.opened.set()
        try:
            output.write_text(capture_text(mode=request["task"], label=request.get("label", "fake"),
                                          limits=[limit.as_dict() for limit in request.get("limits", [])]),
                              encoding="utf-8")
            if self.gate:
                assert self.gate.wait(5), "test did not release fake USB operation"
            if self.failure:
                raise FlatSatError(self.failure)
            return self.code
        finally:
            self.closed.set()


@contextmanager
def running_server(tmp_path, adapter=None):
    adapter = adapter if adapter is not None else FakeAdapter()
    server = web.make_server(port=0, data_dir=tmp_path, adapter=adapter)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    try:
        yield server, adapter
    finally:
        if getattr(adapter, "gate", None):
            adapter.gate.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def request(server, method="GET", path="/api/state", body=None, headers=None, *, raw=None):
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    supplied = dict(headers or {})
    if body is not None:
        raw = json.dumps(body).encode()
        supplied.setdefault("Content-Type", "application/json")
    if method == "POST":
        supplied.setdefault("X-CubeRange-Token", server.csrf_token)
    try:
        connection.request(method, path, body=raw, headers=supplied)
        response = connection.getresponse()
        data = response.read()
        result = json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data
        return response.status, result, dict(response.getheaders())
    finally:
        connection.close()


def wait_job(server):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status, state, _ = request(server)
        assert status == 200
        if state["recent_jobs"] and state["active_job"] is None:
            return state
        time.sleep(0.01)
    raise AssertionError("fake USB job did not finish")


def test_state_discovery_is_read_only_and_server_binds_literal_loopback(tmp_path):
    with running_server(tmp_path) as (server, adapter):
        status, state, headers = request(server)
        assert status == 200
        assert server.server_address[0] == "127.0.0.1"
        assert state["csrf_token"] == server.csrf_token
        assert len(state["csrf_token"]) >= 40
        assert {port["role"] for port in state["devices"]} == {"shell", "radio0", "radio1"}
        assert state["devices"][0]["board_id"] == "BOARD-A"
        assert state["active_job"] is None and state["captures"] == []
        assert adapter.calls == []
        assert headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        adapter.discovery_failure = "pyserial is missing"
        status, state, _ = request(server)
        assert status == 200 and state["devices"] == []
        assert state["discovery_error"] == "pyserial is missing"


@pytest.mark.parametrize("task", ["watch", "info", "monitor"])
def test_typed_job_creates_capture_and_reuses_summary_report_and_raw(tmp_path, task):
    with running_server(tmp_path) as (server, adapter):
        body = {"task": task, "serial": "BOARD-A"}
        if task == "watch":
            body.update(duration=0.2, interval=0.02, timeout=0.3,
                        label="trial <script>alert(1)</script>", notes=["desk"],
                        limits=["temperature_c::20"])
        status, result, _ = request(server, "POST", "/api/jobs", body)
        assert status == 202
        capture_id = result["job"]["capture_id"]
        state = wait_job(server)
        assert state["recent_jobs"][0]["status"] == "completed"
        assert state["recent_jobs"][0]["exit_code"] == 0
        assert state["captures"][0]["id"] == capture_id
        assert adapter.calls[0]["port"] == ("all" if task == "monitor" else "shell")
        status, detail, _ = request(server, path=f"/api/captures/{capture_id}")
        assert status == 200 and detail["session"]["mode"] == task
        if task == "watch":
            assert detail["summary"]["valid_count"] == 2
            assert detail["alerts"]["trigger_count"] == 1
            assert state["recent_jobs"][0]["progress"]["valid_count"] == 2
        elif task == "info":
            assert detail["queries"][0]["command"] == "status"
        else:
            assert detail["received_bytes"] == 2
        status, report, headers = request(server, path=detail["report_url"])
        assert status == 200 and b"<!doctype html>" in report.lower()
        assert b"trial <script>" not in report
        assert "script-src 'unsafe-inline'" in headers["Content-Security-Policy"]
        # The existing standalone report's relative raw link also works in HTTP.
        href = re.search(rb'href="([^"]+\.jsonl)"', report)
        assert href is not None
        relative_raw = urlsplit(urljoin(server.url.rstrip("/") + detail["report_url"], href[1].decode())).path
        status, data, _ = request(server, path=relative_raw)
        assert status == 200 and b'"event": "session"' in data
        status, data, headers = request(server, path=detail["raw_url"])
        assert status == 200 and b'"event": "end"' in data
        assert headers["Content-Disposition"].endswith(f'{capture_id}.jsonl"')


@pytest.mark.parametrize("body", [
    {"task": "flash"}, {"task": "watch", "command": "rm -rf"},
    {"task": "watch", "port": "/dev/ttyACM0"}, {"task": "watch", "port": "radio0"},
    {"task": "info", "port": "all"}, {"task": "info", "commands": ["reboot"]},
    {"task": "info", "commands": ["status", "status"]}, {"task": "info", "commands": []},
    {"task": "info", "commands": [False]}, {"task": "monitor", "timeout": 1},
    {"task": "watch", "duration": True}, {"task": "watch", "duration": 0},
    {"task": "watch", "duration": 60.01}, {"task": "watch", "interval": 0.019},
    {"task": "watch", "interval": "1"}, {"task": "watch", "timeout": 5.1},
    {"task": "watch", "timeout": 0}, {"task": "watch", "limits": ["temperature_c:10:0"]},
    {"task": "watch", "limits": ["temperature_c::30", "temperature_c::40"]},
    {"task": "watch", "notes": "text"}, {"task": "watch", "notes": [False]},
    {"task": "watch", "label": "x" * 201}, {"task": "watch", "serial": ""},
])
def test_unsafe_or_out_of_bound_requests_are_rejected_before_usb(tmp_path, body):
    with running_server(tmp_path) as (server, adapter):
        status, error, _ = request(server, "POST", "/api/jobs", body)
        assert status == 400 and error["error"]
        assert adapter.calls == []
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example:1234"}, {"Host": "localhost:1234"},
    {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
    {"X-CubeRange-Token": ""}, {"X-CubeRange-Token": "wrong"},
])
def test_host_origin_site_and_session_token_block_mutation(tmp_path, headers):
    with running_server(tmp_path) as (server, adapter):
        status, error, _ = request(server, "POST", "/api/jobs", {"task": "info"}, headers)
        assert status == 403 and "error" in error
        assert adapter.calls == []


def test_mutation_without_token_and_read_from_foreign_origin_are_denied(tmp_path):
    with running_server(tmp_path) as (server, adapter):
        connection = HTTPConnection(*server.server_address)
        connection.request("POST", "/api/jobs", body=b'{"task":"info"}',
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.close()
        for headers in ({"Origin": "http://evil.example"}, {"Host": "evil.example"}):
            assert request(server, headers=headers)[0] == 403
        assert adapter.calls == []


@pytest.mark.parametrize(("raw", "headers", "expected"), [
    (b'{}', {"Content-Type": "text/plain"}, 415),
    (b'[]', {"Content-Type": "application/json"}, 400),
    (b'{"task":"watch","duration":NaN}', {"Content-Type": "application/json"}, 400),
    (b'{"task":"watch","duration":1e999}', {"Content-Type": "application/json"}, 400),
    (b'{"task":"watch","task":"info"}', {"Content-Type": "application/json"}, 400),
    (b'{"task":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}', {"Content-Type": "application/json"}, 400),
    (b'{}', {"Content-Type": "application/json", "Content-Length": str(web.MAX_BODY + 1)}, 413),
    (b'{}', {"Content-Type": "application/json", "Content-Length": "-1"}, 413),
    (b'{}', {"Content-Type": "application/json", "Content-Length": "two"}, 400),
    (b'{}', {"Content-Type": "application/json", "Transfer-Encoding": "chunked"}, 400),
])
def test_json_transport_has_finite_values_duplicate_and_size_bounds(tmp_path, raw, headers, expected):
    with running_server(tmp_path) as (server, adapter):
        status, error, _ = request(server, "POST", "/api/jobs", raw=raw, headers=headers)
        assert status == expected and "error" in error
        assert adapter.calls == []
        assert request(server)[0] == 200  # Rejected input does not kill the server.


def test_running_job_reservation_is_global_and_in_progress_captures_are_hidden(tmp_path):
    gate = threading.Event()
    with running_server(tmp_path / "a", FakeAdapter(gate)) as (server, adapter), \
            running_server(tmp_path / "b") as (other, other_adapter):
        status, result, _ = request(server, "POST", "/api/jobs", {"task": "watch"})
        assert status == 202 and adapter.opened.wait(1)
        capture_id = result["job"]["capture_id"]
        assert request(server)[1]["captures"] == []
        for path in (f"/api/captures/{capture_id}", f"/api/captures/{capture_id}/raw",
                     f"/reports/{capture_id}", f"/reports/{capture_id}.jsonl"):
            assert request(server, path=path)[0] == 409
        assert request(server, "POST", "/api/jobs", {"task": "info"})[0] == 409
        assert request(other, "POST", "/api/jobs", {"task": "monitor"})[0] == 409
        assert other_adapter.calls == []
        gate.set()
        assert wait_job(server)["recent_jobs"][0]["status"] == "completed"
        assert request(other, "POST", "/api/jobs", {"task": "info"})[0] == 202
        assert wait_job(other)["recent_jobs"][0]["status"] == "completed"


def test_shutdown_waits_for_usb_close_and_does_not_release_job_early(tmp_path):
    gate = threading.Event()
    with running_server(tmp_path, FakeAdapter(gate)) as (server, adapter):
        assert request(server, "POST", "/api/jobs", {"task": "monitor"})[0] == 202
        assert adapter.opened.wait(1)
        closer = threading.Thread(target=server.server_close)
        closer.start()
        time.sleep(0.03)
        assert closer.is_alive() and not adapter.closed.is_set()
        with pytest.raises(web.RequestError, match="shutting down"):
            server.start_job(web._job_request({"task": "info"}))
        gate.set()
        closer.join(2)
        assert not closer.is_alive() and adapter.closed.is_set()


@pytest.mark.parametrize(("failure", "code"), [("Permission denied", 0), (None, 1)])
def test_usb_failure_is_retained_in_job_and_cannot_poison_next_run(tmp_path, failure, code):
    adapter = FakeAdapter(failure=failure, code=code)
    with running_server(tmp_path, adapter) as (server, _):
        assert request(server, "POST", "/api/jobs", {"task": "info"})[0] == 202
        state = wait_job(server)
        job = state["recent_jobs"][0]
        assert job["status"] == "failed" and job["error"]
        assert len(state["captures"]) == 1
        adapter.failure, adapter.code = None, 0
        assert request(server, "POST", "/api/jobs", {"task": "info"})[0] == 202
        assert wait_job(server)["recent_jobs"][0]["status"] == "completed"


def test_offline_import_validates_and_indexes_only_generated_ids(tmp_path):
    with running_server(tmp_path) as (server, adapter):
        status, result, _ = request(server, "POST", "/api/captures/import",
                                    {"name": "../../outside.jsonl", "content": capture_text()})
        assert status == 201
        capture_id = result["capture"]["id"]
        assert re.fullmatch(r"[0-9a-f]{32}", capture_id)
        assert {path.name for path in tmp_path.iterdir()} == {f"{capture_id}.jsonl"}
        assert request(server)[1]["captures"][0]["id"] == capture_id
        assert adapter.calls == []
        for content in ("not json\n", '{"event":"end"}\n',
                        capture_text().replace('"mode": "watch"', '"mode": "flash"'),
                        capture_text().replace('"label": "test run"', '"label": NaN'),
                        '{"event":"session","mode":"watch","ports":[[]]}\n'):
            status, _, _ = request(server, "POST", "/api/captures/import", {"content": content})
            assert status == 400
            assert len(list(tmp_path.iterdir())) == 1
        assert request(server, "POST", "/api/captures/import",
                       {"content": capture_text(), "path": "/etc/passwd"})[0] == 400


def test_capture_routes_reject_symlinks_large_files_and_traversal(tmp_path):
    with running_server(tmp_path) as (server, _):
        capture_id = "a" * 32
        target = tmp_path.parent / "secret.jsonl"
        target.write_text(capture_text(), encoding="utf-8")
        (tmp_path / f"{capture_id}.jsonl").symlink_to(target)
        for path in (f"/api/captures/{capture_id}", f"/api/captures/{capture_id}/raw", f"/reports/{capture_id}"):
            assert request(server, path=path)[0] == 404
        assert request(server)[1]["captures"] == []
        for path in ("/api/captures/../secret", "/reports/%2e%2e/secret", "/app.js/../web.py",
                     "/api/state?path=/etc/passwd", "/src/cuberange/flatsat/web.py"):
            assert request(server, path=path)[0] == 404
        (tmp_path / f"{capture_id}.jsonl").unlink()
        with (tmp_path / f"{capture_id}.jsonl").open("wb") as stream:
            stream.truncate(web.MAX_CAPTURE + 1)
        assert request(server, path=f"/api/captures/{capture_id}")[0] == 413
        assert request(server)[1]["captures"] == []


@pytest.mark.parametrize("raw_hex", ["test", "abc", ["61", "62"], None, "61 62", "６１"])
def test_monitor_import_rejects_malformed_received_hex_before_counting(tmp_path, raw_hex):
    records = [json.loads(line) for line in capture_text(mode="monitor").splitlines()]
    records[1]["raw_hex"] = raw_hex
    content = "\n".join(json.dumps(record) for record in records) + "\n"
    with running_server(tmp_path) as (server, adapter):
        status, error, _ = request(server, "POST", "/api/captures/import", {"content": content})
        assert status == 400 and "rx.raw_hex" in error["error"]
        assert request(server)[1]["captures"] == []
        assert list(tmp_path.iterdir()) == [] and adapter.calls == []


def test_monitor_import_retains_valid_hex_bytes_in_an_interrupted_capture(tmp_path):
    records = [json.loads(line) for line in capture_text(mode="monitor").splitlines()]
    records[1]["raw_hex"] = "CAFE"
    records[-1] = {"event": "error", "message": "device disconnected"}
    records.append({"event": "rx", "role": "radio1", "raw_hex": ""})
    content = "\n".join(json.dumps(record) for record in records) + "\n"
    with running_server(tmp_path) as (server, adapter):
        status, imported, _ = request(server, "POST", "/api/captures/import", {"content": content})
        assert status == 201 and imported["capture"]["completion"] == "interrupted"
        capture_id = imported["capture"]["id"]
        status, detail, _ = request(server, path=f"/api/captures/{capture_id}")
        assert status == 200 and detail["received_bytes"] == 2
        assert detail["summary"]["quality"]["completion"] == "interrupted"
        assert adapter.calls == []


@pytest.mark.parametrize(("field", "value"), [("answered", "false"), ("answered", 0),
                                               ("answered", None), ("text", []),
                                               ("text", False), ("command", {})])
def test_info_import_rejects_wrong_query_result_metadata_types(tmp_path, field, value):
    records = [json.loads(line) for line in capture_text(mode="info").splitlines()]
    records[1][field] = value
    content = "\n".join(json.dumps(record) for record in records) + "\n"
    with running_server(tmp_path) as (server, adapter):
        status, error, _ = request(server, "POST", "/api/captures/import", {"content": content})
        assert status == 400 and f"query_result.{field}" in error["error"]
        assert request(server)[1]["captures"] == []
        assert list(tmp_path.iterdir()) == [] and adapter.calls == []


@pytest.mark.parametrize("missing", [False, True])
def test_info_import_preserves_false_and_missing_optional_query_metadata(tmp_path, missing):
    records = [json.loads(line) for line in capture_text(mode="info").splitlines()]
    if missing:
        for field in ("answered", "text", "command"):
            del records[1][field]
    else:
        records[1]["answered"] = False
    content = "\n".join(json.dumps(record) for record in records) + "\n"
    with running_server(tmp_path) as (server, adapter):
        status, imported, _ = request(server, "POST", "/api/captures/import", {"content": content})
        assert status == 201
        status, detail, _ = request(server, path=f"/api/captures/{imported['capture']['id']}")
        assert status == 200
        query = detail["queries"][0]
        if missing:
            assert all(field not in query for field in ("answered", "text", "command"))
        else:
            assert query["answered"] is False and query["command"] == "status"
        assert adapter.calls == []


def test_live_progress_does_not_invent_cumulative_valid_count_from_tail(tmp_path):
    gate = threading.Event()
    with running_server(tmp_path, FakeAdapter(gate)) as (server, adapter):
        status, result, _ = request(server, "POST", "/api/jobs", {"task": "watch"})
        assert status == 202 and adapter.opened.wait(1)
        capture_id = result["job"]["capture_id"]
        records = []
        for index in range(1, 501):
            records.append({"event": "sensor_sample", "index": index, "completion": "complete",
                            "elapsed_s": index / 10, **parse_sensor_response(SENSOR_TEXT)})
        (tmp_path / f"{capture_id}.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n")
        progress = request(server)[1]["active_job"]["progress"]
        assert progress["sample_count"] == 500
        assert progress["valid_count"] is None
        assert progress["last_sample"]["index"] == 500
        # Restore a complete recording so final validation is meaningful.
        (tmp_path / f"{capture_id}.jsonl").write_text(capture_text())
        gate.set()
        assert wait_job(server)["recent_jobs"][0]["status"] == "completed"


def test_catalog_routes_only_read_allowlisted_repository_exercises(tmp_path):
    with running_server(tmp_path) as (server, adapter):
        status, result, _ = request(server, path="/api/exercises")
        assert status == 200 and len(result["exercises"]) >= 23
        exercise = result["exercises"][0]
        status, markdown, headers = request(server, path=exercise["readme_url"])
        assert status == 200 and b"# " in markdown and exercise["id"].encode() in markdown
        assert headers["Content-Type"] == "text/plain; charset=utf-8"
        assert request(server, path=f"/api/exercises/{exercise['id']}/readme?lang=en")[0] == 200
        assert request(server, path="/api/exercises/EX-Z99/readme")[0] == 404
        assert request(server, path=f"/api/exercises/{exercise['id']}/readme?lang=../..")[0] == 400
        assert request(server, path="/api/exercises/../../README.md")[0] == 404
        assert adapter.calls == []


def test_default_adapter_dispatches_only_existing_query_and_receive_operations(tmp_path, monkeypatch):
    calls = []
    adapter = web.USBAdapter()
    monkeypatch.setattr(adapter, "discover", lambda: PORTS)
    monkeypatch.setattr(web.cli, "run_info", lambda *args: calls.append(("info", args)) or 0)
    monkeypatch.setattr(web.cli, "run_watch", lambda *args, **kwargs: calls.append(("watch", args, kwargs)) or 0)
    monkeypatch.setattr(web.cli, "run_monitor", lambda *args: calls.append(("monitor", args)) or 0)
    for task in ("info", "watch", "monitor"):
        assert adapter.run(web._job_request({"task": task, "serial": "BOARD-A"}), tmp_path / "fake.jsonl") == 0
    assert calls[0][1][0].role == "shell"
    assert calls[0][1][1] == ["fw_version", "status", "sensors"]
    assert calls[1][1][0].role == "shell"
    assert {port.role for port in calls[2][1][0]} == {"radio0", "radio1", "shell"}


def test_monitor_all_never_opens_unknown_interfaces_or_selects_unknown_only_boards(tmp_path, monkeypatch):
    calls = []
    adapter = web.USBAdapter()
    unknown_a = FlatSatPort("/dev/fake-unclassified-a", "unknown", "BOARD-A", None, "1-1", True)
    unknown_b = FlatSatPort("/dev/fake-unclassified-b", "unknown", "BOARD-B", None, "1-2", True)
    monkeypatch.setattr(adapter, "discover", lambda: [*PORTS, unknown_a, unknown_b])
    monkeypatch.setattr(web.cli, "run_monitor", lambda ports, duration, output: calls.append(ports) or 0)

    # The unclassified interface on BOARD-B cannot turn the known board's
    # unqualified selection into an ambiguous multi-board USB operation.
    assert adapter.run(web._job_request({"task": "monitor"}), tmp_path / "fake.jsonl") == 0
    assert calls == [PORTS]
    for discovered, body in (([unknown_a], {"task": "monitor"}),
                             ([*PORTS, unknown_b], {"task": "monitor", "serial": "BOARD-B"})):
        monkeypatch.setattr(adapter, "discover", lambda: discovered)
        with pytest.raises(FlatSatError, match="No matching FlatSat port"):
            adapter.run(web._job_request(body), tmp_path / "fake.jsonl")
    assert calls == [PORTS]  # No second attempt reached the transport.


@pytest.mark.parametrize("port", [True, -1, 65536, "8765"])
def test_server_rejects_invalid_ports_before_binding(tmp_path, port):
    with pytest.raises(ValueError, match="port must be"):
        web.make_server(port=port, data_dir=tmp_path)
