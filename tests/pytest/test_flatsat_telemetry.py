"""Structured FlatSat observations and OODA capture inspection without hardware IO."""
from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.flatsat import telemetry, usb  # noqa: E402
from cuberange.flatsat.usb import FlatSatError  # noqa: E402

BASE_KEYS = (
    "temperature_c", "pressure_pa", "humidity_percent",
    "accel_x_mg", "accel_y_mg", "accel_z_mg",
)
ALL_KEYS = (*BASE_KEYS, "acceleration_norm_mg")
REAL_REPLY = (
    "sensors\rAccel: x=156 mg  y=-42 mg  z=972 mg\r\n"
    "Temp:  27.690 C\r\nPress: 101306 Pa\r\nHumid: 55%\r\n\n"
)


def reply(*, temperature=10, pressure=100000, humidity=50, accel=(3, -4, 12)):
    x, y, z = accel
    return (
        f"sensors\rAccel: x={x} mg  y={y} mg  z={z} mg\r\n"
        f"Temp: {temperature} C\r\nPress: {pressure} Pa\r\nHumid: {humidity}%\r\n\n"
    )


def write_capture(path, events):
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


def sample(index, text, **fields):
    return {
        "event": "sensor_sample", "index": index,
        "time_utc": f"2026-09-30T14:30:{index:02d}+00:00", "elapsed_s": float(index),
        "response_ms": 20.0, "completion": "complete",
        **telemetry.parse_sensor_response(text), **fields,
    }


def session(mode="watch"):
    return {"event": "session", "mode": mode, "ports": [{
        "path": "/dev/ttyACM8", "role": "shell", "serial_number": "TEST-BOARD",
        "interface": "Cat-Shell", "location": "2-1:1.4", "accessible": True,
    }]}


def test_metric_keys_and_units_are_explicit_and_stable():
    assert tuple(telemetry.METRICS) == ALL_KEYS
    assert all(metric["label"] and metric["unit"] for metric in telemetry.METRICS.values())


def test_parser_reads_actual_captured_reply_with_cr_only_echo_and_signed_acceleration():
    parsed = telemetry.parse_sensor_response(REAL_REPLY)
    assert parsed["status"] == "ok"
    assert parsed["missing_fields"] == []
    assert parsed["errors"] == []
    assert set(parsed["values"]) == set(ALL_KEYS)
    assert parsed["values"] == {
        "temperature_c": 27.69, "pressure_pa": 101306.0, "humidity_percent": 55.0,
        "accel_x_mg": 156.0, "accel_y_mg": -42.0, "accel_z_mg": 972.0,
        "acceleration_norm_mg": pytest.approx(math.sqrt(156**2 + 42**2 + 972**2)),
    }


@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
def test_line_ending_variants_have_the_same_measurements(newline):
    text = reply().replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
    parsed = telemetry.parse_sensor_response(text)
    assert parsed["status"] == "ok"
    assert parsed["values"]["acceleration_norm_mg"] == 13.0


@pytest.mark.parametrize("line,key", [
    ("Temp: 10 C\r\n", "temperature_c"),
    ("Press: 100000 Pa\r\n", "pressure_pa"),
    ("Humid: 50%\r\n", "humidity_percent"),
])
def test_missing_measurement_is_partial_and_never_zero_filled(line, key):
    parsed = telemetry.parse_sensor_response(reply().replace(line, ""))
    assert parsed["status"] == "partial"
    assert parsed["missing_fields"] == [key]
    assert parsed["values"][key] is None
    assert set(parsed["values"]) == set(ALL_KEYS)


def test_missing_acceleration_never_invents_an_acceleration_norm():
    text = reply().replace("Accel: x=3 mg  y=-4 mg  z=12 mg\r\n", "")
    parsed = telemetry.parse_sensor_response(text)
    assert parsed["status"] == "partial"
    assert set(parsed["missing_fields"]) == {"accel_x_mg", "accel_y_mg", "accel_z_mg"}
    assert parsed["values"]["acceleration_norm_mg"] is None


@pytest.mark.parametrize("replacement", ["nan", "inf", "-inf", "1e999"])
def test_nonfinite_sensor_values_are_rejected(replacement):
    parsed = telemetry.parse_sensor_response(reply().replace("Temp: 10 C", f"Temp: {replacement} C"))
    assert parsed["status"] == "invalid"
    assert parsed["errors"]
    assert parsed["values"]["temperature_c"] is None


@pytest.mark.parametrize("original,bad,key", [
    ("Temp: 10 C", "Temp: 10 F", "temperature_c"),
    ("Press: 100000 Pa", "Press: 100 kPa", "pressure_pa"),
    ("Humid: 50%", "Humid: 0.5", "humidity_percent"),
])
def test_wrong_units_are_invalid_and_not_silently_converted(original, bad, key):
    parsed = telemetry.parse_sensor_response(reply().replace(original, bad))
    assert parsed["status"] == "invalid"
    assert parsed["errors"]
    assert parsed["values"][key] is None


@pytest.mark.parametrize("humidity", [-1, 101])
def test_humidity_outside_physical_percentage_domain_is_invalid(humidity):
    parsed = telemetry.parse_sensor_response(reply(humidity=humidity))
    assert parsed["status"] == "invalid"
    assert parsed["values"]["humidity_percent"] is None


@pytest.mark.parametrize("pressure", [0, -1])
def test_nonpositive_pressure_is_invalid(pressure):
    parsed = telemetry.parse_sensor_response(reply(pressure=pressure))
    assert parsed["status"] == "invalid"
    assert parsed["values"]["pressure_pa"] is None


def test_duplicate_measurements_are_rejected_rather_than_picking_one():
    parsed = telemetry.parse_sensor_response(reply() + "Temp: 11 C\r\n")
    assert parsed["status"] == "invalid"
    assert parsed["errors"]
    assert parsed["values"]["temperature_c"] is None


def test_derived_acceleration_cannot_overflow_into_a_valid_nonfinite_metric():
    parsed = telemetry.parse_sensor_response(reply(accel=(1.7e308, 1.7e308, 1.7e308)))
    assert parsed["status"] == "invalid"
    assert parsed["errors"]
    assert parsed["values"]["acceleration_norm_mg"] is None


@pytest.mark.parametrize("text", ["", "sensors\r\n", "> ", "booting\r\n", "unknown command\r\n"])
def test_echo_prompt_banner_or_unknown_command_cannot_be_a_sensor_sample(text):
    parsed = telemetry.parse_sensor_response(text)
    assert parsed["status"] != "ok"
    assert set(parsed["missing_fields"]) == set(BASE_KEYS)
    assert all(value is None for value in parsed["values"].values())


def test_capture_summary_excludes_partial_samples_instead_of_zero_filling(tmp_path):
    path = write_capture(tmp_path / "mixed.jsonl", [
        session(), sample(1, reply(temperature=10)),
        sample(2, "Temp: 1000 C\r\n", completion="timeout"),
        sample(3, reply(temperature=14, pressure=100100, humidity=70, accel=(6, -8, 24))),
        {"event": "end", "success": False},
    ])
    loaded = telemetry.load_capture(path)
    assert loaded["source"] == str(path)
    assert loaded["session"]["mode"] == "watch"
    assert [s["index"] for s in loaded["samples"]] == [1, 2, 3]
    summary = loaded["summary"]
    assert summary["sample_count"] == 3
    assert summary["valid_count"] == 2
    assert summary["failed_count"] == 1
    assert {key: summary["metrics"]["temperature_c"][key] for key in ("count", "min", "max", "mean")} == {
        "count": 2, "min": 10.0, "max": 14.0, "mean": 12.0,
    }
    assert summary["metrics"]["pressure_pa"]["mean"] == 100050.0
    assert summary["metrics"]["humidity_percent"]["mean"] == 60.0
    assert summary["metrics"]["acceleration_norm_mg"]["mean"] == 19.5


def test_legacy_info_sensor_query_results_are_parsed_without_invented_timing(tmp_path):
    path = write_capture(tmp_path / "legacy.jsonl", [
        session("info"),
        {"event": "query_result", "command": "status", "answered": True,
         "text": "RadioManager State: LISTENING", "time_utc": "2026-09-30T14:30:00Z"},
        {"event": "query_result", "command": "sensors", "answered": True,
         "text": REAL_REPLY, "time_utc": "2026-09-30T14:30:01Z"},
    ])
    loaded = telemetry.load_capture(path)
    assert loaded["summary"]["sample_count"] == 1
    assert loaded["summary"]["valid_count"] == 1
    assert loaded["samples"][0]["values"]["temperature_c"] == 27.69
    assert loaded["samples"][0]["time_utc"] == "2026-09-30T14:30:01Z"
    assert loaded["samples"][0]["response_ms"] is None


def test_complete_values_with_a_timed_out_response_are_not_counted_valid(tmp_path):
    path = write_capture(tmp_path / "timed-out.jsonl", [
        session(), sample(1, reply(), completion="timeout"),
    ])
    loaded = telemetry.load_capture(path)
    assert loaded["summary"]["sample_count"] == 1
    assert loaded["summary"]["valid_count"] == 0
    assert loaded["summary"]["failed_count"] == 1
    assert loaded["summary"]["metrics"]["temperature_c"]["count"] == 0


def test_imported_acceleration_norm_is_recomputed_from_the_saved_axes(tmp_path):
    event = sample(1, reply())
    event["values"]["acceleration_norm_mg"] = 999999.0
    path = write_capture(tmp_path / "edited-norm.jsonl", [session(), event])
    loaded = telemetry.load_capture(path)
    assert loaded["samples"][0]["values"]["acceleration_norm_mg"] == 13.0
    assert loaded["summary"]["metrics"]["acceleration_norm_mg"]["mean"] == 13.0


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), True, "10"])
def test_edited_capture_cannot_promote_invalid_numeric_values_to_valid_samples(tmp_path, bad_value):
    event = sample(1, reply())
    event["values"]["temperature_c"] = bad_value
    path = write_capture(tmp_path / "edited-value.jsonl", [session(), event])
    loaded = telemetry.load_capture(path)
    assert loaded["samples"][0]["status"] != "ok"
    assert loaded["samples"][0]["values"]["temperature_c"] is None
    assert loaded["summary"]["valid_count"] == 0
    assert {key: loaded["summary"]["metrics"]["temperature_c"][key] for key in ("count", "min", "max", "mean")} == {
        "count": 0, "min": None, "max": None, "mean": None,
    }


def test_raw_only_monitor_capture_is_zero_samples_not_sensor_measurement(tmp_path):
    path = write_capture(tmp_path / "monitor.jsonl", [
        session("monitor"), {"event": "rx", "role": "shell", "raw_hex": REAL_REPLY.encode().hex()},
        {"event": "end", "received_bytes": len(REAL_REPLY)},
    ])
    loaded = telemetry.load_capture(path)
    assert loaded["samples"] == []
    assert loaded["summary"]["sample_count"] == 0
    assert loaded["summary"]["valid_count"] == 0
    assert loaded["summary"]["failed_count"] == 0
    for stats in loaded["summary"]["metrics"].values():
        assert {key: stats[key] for key in ("count", "min", "max", "mean")} == {
            "count": 0, "min": None, "max": None, "mean": None,
        }


@pytest.mark.parametrize("bad_line", ["{", '{"event":"sensor_sample"', "[1,2]"])
def test_bad_or_truncated_jsonl_names_the_source_line(tmp_path, bad_line):
    path = tmp_path / "broken.jsonl"
    path.write_text(json.dumps(session()) + "\n" + bad_line + "\n")
    with pytest.raises(FlatSatError) as exc:
        telemetry.load_capture(path)
    assert "2" in str(exc.value)
    assert str(path) in str(exc.value) or path.name in str(exc.value)


def cli():
    return importlib.import_module("cuberange.flatsat.__main__")


def forbid_usb(monkeypatch, module):
    def forbidden(*args, **kwargs):
        pytest.fail("offline capture inspection attempted USB discovery or IO")
    monkeypatch.setattr(module, "discover_ports", forbidden)
    monkeypatch.setattr(module, "SerialSession", forbidden)


def test_summary_cli_is_offline_and_returns_the_saved_measurements(monkeypatch, tmp_path, capsys):
    module = cli()
    forbid_usb(monkeypatch, module)
    path = write_capture(tmp_path / "watch.jsonl", [session(), sample(1, REAL_REPLY)])
    assert module.main(["summary", str(path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    # Summary may include source context; metric values are established by the capture.
    summary = result.get("summary", result)
    assert summary["sample_count"] == 1
    assert summary["metrics"]["temperature_c"]["mean"] == 27.69


def test_report_cli_is_offline_and_keeps_the_original_capture(monkeypatch, tmp_path):
    module = cli()
    forbid_usb(monkeypatch, module)
    path = write_capture(tmp_path / "watch.jsonl", [session(), sample(1, REAL_REPLY)])
    original = path.read_bytes()
    output = tmp_path / "report.html"
    assert module.main(["report", str(path), "--output", str(output)]) == 0
    document = output.read_text(encoding="utf-8")
    assert "<!doctype html>" in document.lower()
    assert "27.69" in document
    assert "application/json" in document
    assert path.read_bytes() == original
    assert module.main(["report", str(path), "--output", str(path)]) == 1
    assert path.read_bytes() == original


class Clock:
    """Advance only on reads and sleeps, so polling tests have no wall-clock delays."""
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


def shell_port():
    return usb.FlatSatPort("/dev/ttyACM8", "shell", "TEST-BOARD", "Cat-Shell", "2-1:1.4", True)


def scripted_shell(monkeypatch, module, plans, *, read_step=0.01, fail_query=None, error=None):
    clock = Clock()
    writes, finishes, opened, closed = [], [], [], []
    monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(module, "discover_ports", lambda: [shell_port()])

    class ScriptedShell:
        def __init__(self, port):
            self.port = port
            self.chunks = []
            self.active = False
            self.last_rx = None
            self.banner = b"boot\x00\xff\r\n"

        def __enter__(self):
            opened.append(self.port.path)
            return self

        def __exit__(self, *_):
            closed.append(self.port.path)

        def write(self, data):
            assert self.port.role == "shell"
            assert data == b"sensors\r\n", "watch attempted a different shell command"
            assert not self.active, "watch overlapped sensor requests"
            writes.append(clock.now)
            self.active = True
            self.last_rx = None
            self.chunks = list(plans[min(len(writes) - 1, len(plans) - 1)])

        def read(self):
            clock.sleep(read_step)
            if self.active and fail_query == len(writes):
                raise error
            if self.banner:
                result, self.banner = self.banner, b""
                return result
            if self.chunks:
                self.last_rx = clock.now
                result = self.chunks.pop(0)
                if not self.chunks and result.replace(b"\r\n", b"\n").replace(b"\r", b"\n").endswith(b"\n\n"):
                    self.active = False
                    finishes.append(clock.now)
                return result
            if self.active and self.last_rx is not None and clock.now - self.last_rx >= 0.2:
                self.active = False
                finishes.append(clock.now)
            return b""

    monkeypatch.setattr(module, "SerialSession", ScriptedShell)
    return SimpleNamespace(clock=clock, writes=writes, finishes=finishes, opened=opened, closed=closed)


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_watch_reuses_one_shell_and_handles_a_command_echo_split_across_reads(monkeypatch, tmp_path):
    module = cli()
    raw = reply().encode()
    transport = scripted_shell(monkeypatch, module, [[raw[:3], raw[3:12], raw[12:]]])
    output = tmp_path / "watch.jsonl"
    assert module.main(["watch", "--duration", "1.5", "--interval", "0.5", "--timeout", "0.4",
                        "--output", str(output)]) == 0
    data = telemetry.load_capture(output)
    assert len(transport.writes) == 3
    assert transport.opened == transport.closed == ["/dev/ttyACM8"]
    assert data["summary"]["sample_count"] == data["summary"]["valid_count"] == 3
    assert all(s["values"]["acceleration_norm_mg"] == 13 for s in data["samples"])
    assert all(second - first >= 0.5 - 1e-9 for first, second in zip(transport.writes, transport.writes[1:]))
    assert transport.clock.now <= 1.5 + 0.01
    rx = [e for e in events(output) if e["event"] == "rx"]
    assert b"".join(bytes.fromhex(e["raw_hex"]) for e in rx) == b"boot\x00\xff\r\n" + raw * 3


def test_watch_skips_catch_up_instead_of_bursting_after_slow_queries(monkeypatch, tmp_path):
    module = cli()
    # No blank-line terminator: completion still waits for a quiet interval.
    raw = reply().encode().rstrip(b"\r\n") + b"\r\n"
    transport = scripted_shell(monkeypatch, module, [[raw[:3], raw[3:12], raw[12:]]], read_step=0.1)
    output = tmp_path / "slow-watch.jsonl"
    module.main(["watch", "--duration", "2", "--interval", "0.1", "--timeout", "0.8",
                 "--output", str(output)])
    assert len(transport.writes) >= 2
    for next_start, previous_finish in zip(transport.writes[1:], transport.finishes):
        assert next_start >= previous_finish + 0.1 - 1e-9
    assert len(transport.writes) <= 3
    assert transport.opened == transport.closed == ["/dev/ttyACM8"]


def test_timeout_stops_watch_before_a_late_prior_response_can_answer_a_new_query(monkeypatch, tmp_path):
    module = cli()
    # The first query never returns; a future query would appear to get a complete
    # old reply. With no request ID the tool must stop instead of sending again.
    transport = scripted_shell(monkeypatch, module, [[], [reply().encode()]])
    output = tmp_path / "timed-out-watch.jsonl"
    assert module.main(["watch", "--duration", "2", "--interval", "0.1", "--timeout", "0.3",
                        "--output", str(output)]) == 1
    assert len(transport.writes) == 1
    data = telemetry.load_capture(output)
    assert data["summary"]["sample_count"] == 1
    assert data["summary"]["valid_count"] == 0
    assert data["summary"]["failed_count"] == 1
    assert data["samples"][0]["status"] == "timeout"
    assert events(output)[-1]["reason"] == "response_timeout"
    assert transport.closed == ["/dev/ttyACM8"]


@pytest.mark.parametrize("fail_on_alert", [False, True])
def test_watch_limit_transitions_are_saved_and_replay_matches_them(monkeypatch, tmp_path, fail_on_alert):
    module = cli()
    plans = [[reply(temperature=value).encode()] for value in (10, 11, 10, 9)]
    transport = scripted_shell(monkeypatch, module, plans)
    output = tmp_path / "limits.jsonl"
    args = ["watch", "--duration", "2", "--interval", "0.5", "--timeout", "0.4",
            "--limit", "temperature_c:9:10", "--output", str(output)]
    if fail_on_alert:
        args.append("--fail-on-alert")
    assert module.main(args) == (3 if fail_on_alert else 0)
    recorded = events(output)
    configured = recorded[0]["limits"]
    assert configured == [{"metric": "temperature_c", "minimum": 9.0, "maximum": 10.0}]
    transitions = [event for event in recorded if event["event"] == "alert"]
    assert [(e["index"], e["transition"], e["state"]) for e in transitions] == [
        (2, "triggered", "above"), (3, "recovered", "normal")]
    replay = module.evaluate_capture(telemetry.load_capture(output))
    assert replay["events"] == [{key: value for key, value in event.items() if key != "event"}
                                 for event in transitions]
    assert recorded[-1]["trigger_count"] == replay["trigger_count"] == 1
    assert recorded[-1]["alert_states"] == replay["final_states"] == {"temperature_c": "normal"}
    assert len(transport.opened) == len(transport.closed) == 1


def test_watch_limit_timeout_is_unavailable_and_capture_failure_wins_over_alert_exit(monkeypatch, tmp_path):
    module = cli()
    scripted_shell(monkeypatch, module, [[reply(temperature=11).encode()], []])
    output = tmp_path / "limit-timeout.jsonl"
    assert module.main(["watch", "--duration", "1.5", "--interval", "0.5", "--timeout", "0.3",
                        "--limit", "temperature_c::10", "--fail-on-alert", "--output", str(output)]) == 1
    transitions = [e for e in events(output) if e["event"] == "alert"]
    assert [(e["transition"], e["state"], e["value"]) for e in transitions] == [
        ("triggered", "above", 11.0), ("unavailable", "unknown", None)]
    assert events(output)[-1]["reason"] == "response_timeout"


@pytest.mark.parametrize("arguments,expected", [
    (["--fail-on-alert"], 1),
    (["--limit", "temperature_c::10", "--limit", "temperature_c:0:"], 1),
    (["--limit", "typo:0:10"], 2),
    (["--limit", "temperature_c:10:0"], 2),
    (["--limit", "temperature_c::"], 2),
])
def test_watch_invalid_limits_are_rejected_before_usb_inventory_or_file_creation(
        monkeypatch, tmp_path, arguments, expected):
    module = cli()
    def forbidden():
        pytest.fail("invalid limits attempted USB inventory")
    monkeypatch.setattr(module, "discover_ports", forbidden)
    output = tmp_path / "invalid.jsonl"
    args = ["watch", "--output", str(output), *arguments]
    if expected == 2:
        with pytest.raises(SystemExit) as exc:
            module.main(args)
        assert exc.value.code == expected
    else:
        assert module.main(args) == expected
    assert not output.exists()


def test_alerts_offline_override_is_replayed_and_original_log_is_preserved(monkeypatch, tmp_path, capsys):
    module = cli()
    def forbidden():
        pytest.fail("offline alerts attempted USB inventory")
    monkeypatch.setattr(module, "discover_ports", forbidden)
    path = write_capture(tmp_path / "existing.jsonl", [session(), sample(1, reply(temperature=11)),
                                                      {"event": "end", "reason": "duration_complete"}])
    original = path.read_bytes()
    assert module.main(["alerts", str(path), "--limit", "temperature_c::10", "--json",
                        "--fail-on-alert"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["trigger_count"] == 1
    assert payload["events"][0]["value"] == 11.0
    assert path.read_bytes() == original
    assert module.main(["alerts", str(path)]) == 1


@pytest.mark.parametrize("records", [
    [{"event": "end", "reason": "duration_complete"}],
    [sample(1, "", status="invalid"), {"event": "end", "reason": "duration_complete"}],
    [sample(1, reply())],
    [sample(1, reply(temperature=21)), sample(2, "", status="invalid"),
     {"event": "end", "reason": "duration_complete"}],
])
def test_offline_alert_gate_rejects_empty_failed_or_unfinished_captures(monkeypatch, tmp_path, records):
    module = cli()
    path = write_capture(tmp_path / "indeterminate.jsonl", [session(), *records])
    assert module.main(["alerts", str(path), "--limit", "temperature_c::20", "--fail-on-alert"]) == 1


def test_sensor_fields_before_the_current_echo_cannot_contaminate_a_new_sample(monkeypatch, tmp_path):
    module = cli()
    stale = reply(temperature=99, pressure=90000, humidity=99).encode().split(b"\r", 1)[1]
    current = reply(temperature=12, pressure=100100, humidity=51).encode()
    transport = scripted_shell(monkeypatch, module, [[stale, current[:3], current[3:]]])
    output = tmp_path / "echo-boundary.jsonl"
    assert module.main(["watch", "--duration", "0.6", "--interval", "1", "--timeout", "0.4",
                        "--output", str(output)]) == 0
    data = telemetry.load_capture(output)
    assert data["summary"]["valid_count"] == 1
    assert data["samples"][0]["values"]["temperature_c"] == 12.0
    rx = [e for e in events(output) if e["event"] == "rx"]
    assert b"".join(bytes.fromhex(e["raw_hex"]) for e in rx) == b"boot\x00\xff\r\n" + stale + current
    assert len(transport.writes) == 1


def test_complete_sensor_fields_without_a_current_command_echo_are_not_success(monkeypatch, tmp_path):
    module = cli()
    stale = reply().encode().split(b"\r", 1)[1]
    scripted_shell(monkeypatch, module, [[stale]])
    output = tmp_path / "no-echo.jsonl"
    assert module.main(["watch", "--duration", "0.6", "--interval", "1", "--timeout", "0.4",
                        "--output", str(output)]) == 1
    data = telemetry.load_capture(output)
    assert data["summary"]["valid_count"] == 0


def test_last_echo_selects_the_new_reply_when_two_complete_replies_are_received(monkeypatch, tmp_path):
    module = cli()
    old = reply(temperature=99, humidity=99).encode()
    current = reply(temperature=12, humidity=51).encode()
    scripted_shell(monkeypatch, module, [[old + current]])
    output = tmp_path / "two-replies.jsonl"
    assert module.main(["watch", "--duration", "0.6", "--interval", "1", "--timeout", "0.4",
                        "--output", str(output)]) == 0
    loaded = telemetry.load_capture(output)
    assert loaded["summary"]["valid_count"] == 1
    assert loaded["samples"][0]["values"]["temperature_c"] == 12
    assert loaded["samples"][0]["values"]["humidity_percent"] == 51
    assert any(e["event"] == "rx" and bytes.fromhex(e["raw_hex"]) == old + current for e in events(output))


@pytest.mark.parametrize("error,expected", [
    (FlatSatError("Read failed on /dev/ttyACM8: disconnected"), 1),
    (KeyboardInterrupt(), 130),
])
def test_watch_read_failure_or_interruption_keeps_prior_samples_and_raw_evidence(
        monkeypatch, tmp_path, error, expected):
    module = cli()
    transport = scripted_shell(monkeypatch, module, [[reply().encode()]], fail_query=2, error=error)
    output = tmp_path / "interrupted-watch.jsonl"
    assert module.main(["watch", "--duration", "2", "--interval", "0.5", "--timeout", "0.4",
                        "--output", str(output)]) == expected
    recorded = events(output)
    assert len([e for e in recorded if e["event"] == "sensor_sample"]) == 1
    assert any(e["event"] == "rx" and bytes.fromhex(e["raw_hex"]) == reply().encode() for e in recorded)
    assert recorded[-1]["event"] == "error"
    assert transport.closed == ["/dev/ttyACM8"]
    loaded = telemetry.load_capture(output)
    assert loaded["end"] is None
    assert len(loaded["capture_errors"]) == 1


def test_info_permission_failure_still_records_the_selected_board_before_open(monkeypatch, tmp_path):
    module = cli()
    monkeypatch.setattr(module, "discover_ports", lambda: [shell_port()])

    class Denied:
        def __init__(self, port):
            self.port = port

        def __enter__(self):
            raise FlatSatError(f"Permission denied opening {self.port.path}")

        def __exit__(self, *_):
            pytest.fail("unopened port attempted to exit")

    monkeypatch.setattr(module, "SerialSession", Denied)
    output = tmp_path / "denied-info.jsonl"
    assert module.main(["info", "--output", str(output)]) == 1
    recorded = events(output)
    assert recorded[0]["event"] == "session"
    assert recorded[0]["ports"][0]["serial_number"] == "TEST-BOARD"
    assert recorded[-1]["event"] == "error"


def test_info_write_attempt_is_saved_before_a_partial_write_failure(monkeypatch, tmp_path):
    module = cli()
    clock = Clock()
    monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(module, "discover_ports", lambda: [shell_port()])
    output = tmp_path / "write-failure-info.jsonl"
    closed = []

    class WriteFailure:
        def __init__(self, port):
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, *_):
            closed.append(self.port.path)

        def read(self):
            clock.sleep(0.02)
            return b""

        def write(self, data):
            assert events(output)[-1]["event"] == "tx_attempt"
            assert bytes.fromhex(events(output)[-1]["raw_hex"]) == data
            raise FlatSatError("Incomplete write on /dev/ttyACM8")

    monkeypatch.setattr(module, "SerialSession", WriteFailure)
    assert module.main(["info", "--query", "sensors", "--output", str(output)]) == 1
    recorded = events(output)
    assert [e["event"] for e in recorded] == ["session", "tx_attempt", "error"]
    assert closed == ["/dev/ttyACM8"]
