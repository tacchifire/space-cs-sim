"""Recorded cadence, data quality, and dispersion use measured capture data only."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.flatsat.telemetry import load_capture  # noqa: E402
from cuberange.flatsat.usb import FlatSatError, FlatSatPort  # noqa: E402


def sensor_text(temperature=10):
    return (f"sensors\rAccel: x=3 mg  y=-4 mg  z=12 mg\r\nTemp: {temperature} C\r\n"
            "Press: 100000 Pa\r\nHumid: 50%\r\n\n")


def measurement(index, temperature=10, *, elapsed=None, status="ok", response_ms=20,
                completion="complete", **extra):
    return {
        "event": "sensor_sample", "index": index,
        "time_utc": f"2026-10-01T00:00:{index:02d}+00:00",
        "elapsed_s": float(index) if elapsed is None else elapsed,
        "response_ms": response_ms, "status": status, "completion": completion,
        "values": {"temperature_c": temperature, "pressure_pa": 100000,
                   "humidity_percent": 50, "accel_x_mg": 3, "accel_y_mg": -4,
                   "accel_z_mg": 12, "acceleration_norm_mg": 13},
        "errors": [], **extra,
    }


def capture(tmp_path, records, *, mode="watch", interval=1.0, end=True, errors=()):
    output = tmp_path / "analysis.jsonl"
    events = [{"event": "session", "mode": mode, "interval_s": interval}, *records, *errors]
    if end:
        events.append({"event": "end", "reason": "duration_complete"})
    output.write_text("".join(json.dumps(event) + "\n" for event in events))
    return load_capture(output)


def test_dispersion_and_endpoints_only_use_complete_valid_measurements_in_capture_order(tmp_path):
    loaded = capture(tmp_path, [measurement(1, 10), measurement(2, 1000, status="partial"),
                                measurement(3, 12), measurement(4, 14)])
    stats = loaded["summary"]["metrics"]["temperature_c"]
    assert {key: stats[key] for key in ("count", "min", "max", "mean")} == {
        "count": 3, "min": 10, "max": 14, "mean": 12,
    }
    assert stats["stdev"] == 2.0
    assert stats["first"] == 10
    assert stats["last"] == 14
    assert stats["delta"] == 4
    assert stats["span"] == 4
    assert stats["unavailable_reasons"] == {}


def test_metric_endpoints_preserve_record_order_instead_of_sorting_by_value(tmp_path):
    stats = capture(tmp_path, [measurement(1, 14), measurement(2, 10), measurement(3, 12)])["summary"]["metrics"]["temperature_c"]
    assert (stats["first"], stats["last"], stats["delta"], stats["span"]) == (14, 12, -2, 4)
    assert stats["stdev"] == 2


def test_single_observation_has_zero_observed_change_but_no_sample_standard_deviation(tmp_path):
    stats = capture(tmp_path, [measurement(1, 0)])["summary"]["metrics"]["temperature_c"]
    assert stats["count"] == 1
    assert stats["mean"] == stats["first"] == stats["last"] == 0
    assert stats["delta"] == stats["span"] == 0
    assert stats["stdev"] is None
    assert stats["unavailable_reasons"]["stdev"] == "fewer_than_two_values"


def test_no_valid_observations_do_not_invent_numeric_statistics(tmp_path):
    summary = capture(tmp_path, [measurement(1, status="invalid")])["summary"]
    for stats in summary["metrics"].values():
        assert stats["count"] == 0
        for field in ("min", "max", "mean", "stdev", "first", "last", "delta", "span"):
            assert stats[field] is None
            assert stats["unavailable_reasons"][field] == "no_values"
    assert "no_valid_samples" in summary["quality"]["notes"]


def test_unrepresentable_derived_statistics_are_none_with_a_reason_and_valid_json(tmp_path):
    summary = capture(tmp_path, [measurement(1, -1.7e308), measurement(2, 1.7e308)])["summary"]
    stats = summary["metrics"]["temperature_c"]
    assert stats["count"] == 2 and stats["mean"] == 0
    for field in ("delta", "span", "stdev"):
        assert stats[field] is None
        assert stats["unavailable_reasons"][field] == "overflow"
    json.dumps(summary, allow_nan=False)


def test_cadence_uses_all_recorded_queries_and_n_minus_one_over_span(tmp_path):
    loaded = capture(tmp_path, [measurement(1, elapsed=0.5, response_ms=10),
                                measurement(2, elapsed=1.5, status="partial", response_ms=20),
                                measurement(3, elapsed=3.5, response_ms=30)])
    timing = loaded["summary"]["timing"]
    assert timing["timestamp_basis"] == "host_query_start"
    assert timing["elapsed_span_s"] == 3
    assert timing["intervals_s"] == {"count": 2, "min": 1, "max": 2, "mean": 1.5}
    assert timing["effective_hz"] == pytest.approx(2 / 3)
    assert timing["expected_interval_s"] == 1
    assert timing["response_ms"] == {"count": 3, "min": 10, "max": 30, "mean": 20}
    assert "irregular_time" not in loaded["summary"]["quality"]["notes"]


@pytest.mark.parametrize("elapsed", [[0, 0, 1], [2, 1, 3], [0, 2, 1]])
def test_duplicate_or_backward_times_are_flagged_without_sorting_or_cadence_estimates(tmp_path, elapsed):
    loaded = capture(tmp_path, [measurement(i + 1, elapsed=value) for i, value in enumerate(elapsed)])
    assert [sample["elapsed_s"] for sample in loaded["samples"]] == elapsed
    timing = loaded["summary"]["timing"]
    assert timing["elapsed_span_s"] is None
    assert timing["effective_hz"] is None
    assert timing["intervals_s"] == {"count": 0, "min": None, "max": None, "mean": None}
    assert "irregular_time" in loaded["summary"]["quality"]["notes"]


def test_watch_missing_monotonic_time_is_not_filled_from_a_different_clock(tmp_path):
    missing = measurement(2)
    missing["elapsed_s"] = None
    loaded = capture(tmp_path, [measurement(1, elapsed=0.2), missing, measurement(3, elapsed=2.2)])
    assert loaded["samples"][1]["elapsed_s"] is None
    assert loaded["summary"]["timing"]["effective_hz"] is None
    assert "irregular_time" in loaded["summary"]["quality"]["notes"]


def test_one_recorded_time_has_zero_span_but_no_frequency(tmp_path):
    timing = capture(tmp_path, [measurement(1, elapsed=0.2)])["summary"]["timing"]
    assert timing["elapsed_span_s"] == 0
    assert timing["effective_hz"] is None
    assert timing["intervals_s"]["count"] == 0


def test_timeout_latency_is_excluded_but_completed_invalid_or_partial_replies_are_measured(tmp_path):
    records = [measurement(1, response_ms=10), measurement(2, status="partial", response_ms=30),
               measurement(3, status="invalid", response_ms=50),
               measurement(4, completion="timeout", response_ms=9999)]
    summary = capture(tmp_path, records)["summary"]
    assert summary["timing"]["response_ms"] == {"count": 3, "min": 10, "max": 50, "mean": 30}
    assert summary["quality"]["status_counts"] == {"ok": 1, "partial": 1, "invalid": 1, "timeout": 1}
    assert sum(summary["quality"]["status_counts"].values()) == summary["sample_count"]


@pytest.mark.parametrize("interval", [None, 0, -1, True, "1", float("nan"), float("inf"), 10**500])
def test_only_finite_positive_recorded_intervals_are_expected_cadence(tmp_path, interval):
    assert capture(tmp_path, [measurement(1)], interval=interval)["summary"]["timing"]["expected_interval_s"] is None


def test_recording_errors_outrank_an_end_marker_and_preserve_sample_quality_counts(tmp_path):
    error = {"event": "error", "message": "read failed"}
    summary = capture(tmp_path, [measurement(1)], errors=[error])["summary"]
    assert summary["valid_count"] == 1
    assert summary["quality"]["completion"] == "interrupted"
    assert {"unfinished", "capture_errors"}.issubset(summary["quality"]["notes"])


def test_missing_end_is_unknown_not_a_completed_recording(tmp_path):
    quality = capture(tmp_path, [measurement(1)], end=False)["summary"]["quality"]
    assert quality["completion"] == "unknown"
    assert "unfinished" in quality["notes"]


def test_a_timeout_end_is_an_interrupted_recording_even_with_a_clean_end_event(tmp_path):
    loaded = capture(tmp_path, [measurement(1, completion="timeout"),
                                {"event": "end", "reason": "response_timeout"}], end=False)
    assert loaded["summary"]["quality"]["completion"] == "interrupted"
    assert "unfinished" in loaded["summary"]["quality"]["notes"]


@pytest.mark.parametrize("reason", ["user_interrupt", "interrupted", "device_error"])
def test_explicit_interrupted_end_reason_does_not_need_an_extra_error_event(tmp_path, reason):
    loaded = capture(tmp_path, [measurement(1), {"event": "end", "reason": reason}], end=False)
    assert loaded["summary"]["valid_count"] == 1
    assert loaded["summary"]["quality"]["completion"] == "interrupted"
    assert "unfinished" in loaded["summary"]["quality"]["notes"]


@pytest.mark.parametrize("reason", ["future_firmware_event", ["not", "a", "reason"]])
def test_unknown_end_reasons_do_not_assert_completed_recording(tmp_path, reason):
    loaded = capture(tmp_path, [measurement(1), {"event": "end", "reason": reason}], end=False)
    assert loaded["summary"]["quality"]["completion"] == "unknown"
    assert {"unfinished", "unknown_end_reason"}.issubset(loaded["summary"]["quality"]["notes"])


def test_legacy_end_without_a_reason_still_marks_the_recording_complete(tmp_path):
    loaded = capture(tmp_path, [measurement(1), {"event": "end"}], end=False)
    assert loaded["summary"]["quality"]["completion"] == "complete"


def test_normal_recording_end_is_complete_even_with_a_rejected_sample(tmp_path):
    quality = capture(tmp_path, [measurement(1, status="invalid")])["summary"]["quality"]
    assert quality["completion"] == "complete"
    assert quality["notes"] == ["no_valid_samples"]


def test_zero_samples_are_explicit_and_have_no_frequency_or_latency(tmp_path):
    summary = capture(tmp_path, [])["summary"]
    assert summary["quality"]["notes"] == ["no_samples"]
    assert summary["timing"]["elapsed_span_s"] is None
    assert summary["timing"]["effective_hz"] is None
    assert summary["timing"]["response_ms"]["count"] == 0


def legacy(text, *, answered=None, include_answered=True, time_utc="2026-10-01T00:00:01Z", **extra):
    event = {"event": "query_result", "command": "sensors", "text": text, "time_utc": time_utc, **extra}
    if include_answered:
        event["answered"] = answered
    return event


def test_recorded_unanswered_query_cannot_be_promoted_to_valid_even_with_complete_numbers(tmp_path):
    loaded = capture(tmp_path, [legacy(sensor_text(), answered=False)], mode="info")
    assert loaded["summary"]["valid_count"] == 0
    assert loaded["samples"][0]["status"] == "invalid"
    assert any("not answered" in error for error in loaded["samples"][0]["errors"])


def test_legacy_answered_metadata_requires_a_complete_echo_for_synchronization(tmp_path):
    without_echo = sensor_text().split("\r", 1)[1]
    loaded = capture(tmp_path, [legacy(without_echo, answered=True)], mode="info")
    assert loaded["summary"]["valid_count"] == 0
    assert any("echo" in error for error in loaded["samples"][0]["errors"])


def test_legacy_parser_uses_only_values_after_the_last_sensors_echo(tmp_path):
    loaded = capture(tmp_path, [legacy(sensor_text(99) + sensor_text(12), answered=True)], mode="info")
    assert loaded["summary"]["valid_count"] == 1
    assert loaded["samples"][0]["values"]["temperature_c"] == 12


def test_older_echo_free_capture_without_answered_metadata_remains_supported(tmp_path):
    without_echo = sensor_text().split("\r", 1)[1]
    loaded = capture(tmp_path, [legacy(without_echo, include_answered=False)], mode="info")
    assert loaded["summary"]["valid_count"] == 1
    assert loaded["summary"]["timing"]["timestamp_basis"] == "legacy_host_record"
    assert loaded["samples"][0]["elapsed_s"] == 0


def test_backward_legacy_record_time_is_not_clamped_to_an_invented_zero(tmp_path):
    loaded = capture(tmp_path, [legacy(sensor_text(), include_answered=False, time_utc="2026-10-01T00:00:02Z"),
                                legacy(sensor_text(), include_answered=False, time_utc="2026-10-01T00:00:01Z")], mode="info")
    assert loaded["samples"][1]["elapsed_s"] is None
    assert "irregular_time" in loaded["summary"]["quality"]["notes"]
    assert loaded["summary"]["timing"]["effective_hz"] is None


def test_offset_free_legacy_timestamp_does_not_guess_the_execution_hosts_timezone(tmp_path):
    loaded = capture(tmp_path, [legacy(sensor_text(), include_answered=False, time_utc="2026-10-01T00:00:01")], mode="info")
    assert loaded["samples"][0]["elapsed_s"] is None
    assert loaded["summary"]["timing"]["elapsed_span_s"] is None


@pytest.mark.parametrize("answered", [0, 1, "false", None])
def test_answered_metadata_is_a_boolean_not_a_truthy_string_or_number(tmp_path, answered):
    with pytest.raises(FlatSatError, match="answered must be a boolean"):
        capture(tmp_path, [legacy(sensor_text(), answered=answered)], mode="info")


def test_mixed_legacy_wall_clock_and_recorded_elapsed_time_do_not_form_a_false_cadence(tmp_path):
    loaded = capture(tmp_path, [measurement(1, elapsed=0.2),
                                legacy(sensor_text(), include_answered=False, time_utc="2026-10-01T00:00:02Z")], mode="info")
    assert loaded["summary"]["timing"]["effective_hz"] is None
    assert loaded["summary"]["timing"]["intervals_s"]["count"] == 0
    assert "irregular_time" in loaded["summary"]["quality"]["notes"]


@pytest.mark.parametrize("field", ["elapsed_s", "response_ms"])
def test_huge_imported_time_integer_is_an_actionable_capture_error(tmp_path, field):
    event = measurement(1)
    event[field] = 10**500
    with pytest.raises(FlatSatError, match=field):
        capture(tmp_path, [event])


def shell_simulator(monkeypatch, chunks):
    module = importlib.import_module("cuberange.flatsat.__main__")
    clock = SimpleNamespace(now=0.0)
    clock.monotonic = lambda: clock.now

    def sleep(duration):
        clock.now += duration

    clock.sleep = sleep
    port = FlatSatPort("/dev/ttyACM8", "shell", "TEST-BOARD", "Cat-Shell", "2-1:1.4", True)
    writes, opened, closed = [], [], []
    monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(module, "discover_ports", lambda: [port])

    class Shell:
        def __init__(self, selected):
            self.port = selected
            self.pending = []
            self.waiting = False

        def __enter__(self):
            opened.append(self.port.path)
            return self

        def __exit__(self, *_):
            closed.append(self.port.path)

        def write(self, data):
            assert data == b"sensors\r\n"
            assert not self.waiting, "a sensor request overlapped an unfinished response"
            writes.append(clock.now)
            self.pending = list(chunks)
            self.waiting = True

        def read(self):
            clock.sleep(0.005)
            if self.pending:
                result = self.pending.pop(0)
                if not self.pending and result.endswith(b"\n"):
                    self.waiting = False
                return result
            return b""

    monkeypatch.setattr(module, "SerialSession", Shell)
    return module, SimpleNamespace(clock=clock, writes=writes, opened=opened, closed=closed)


def test_complete_sensor_reply_with_a_split_blank_line_finishes_before_the_old_quiet_delay(monkeypatch, tmp_path):
    raw = sensor_text().encode()
    module, transport = shell_simulator(monkeypatch, [raw[:-1], raw[-1:]])
    output = tmp_path / "split-terminator.jsonl"
    assert module.main(["watch", "--duration", "0.6", "--interval", "1", "--timeout", "0.05",
                        "--output", str(output)]) == 0
    loaded = load_capture(output)
    assert len(transport.writes) == 1
    assert loaded["samples"][0]["completion"] == "complete"
    assert loaded["samples"][0]["response_ms"] == pytest.approx(10)
    received = [json.loads(line) for line in output.read_text().splitlines()]
    assert b"".join(bytes.fromhex(event["raw_hex"]) for event in received if event["event"] == "rx") == raw


def test_partial_sensor_reply_cannot_finish_early_merely_because_it_has_a_blank_line(monkeypatch, tmp_path):
    module, transport = shell_simulator(monkeypatch, [b"sensors\rTemp: 10 C\r\n\n"])
    output = tmp_path / "partial-terminator.jsonl"
    assert module.main(["watch", "--duration", "0.6", "--interval", "1", "--timeout", "0.05",
                        "--output", str(output)]) == 1
    loaded = load_capture(output)
    assert len(transport.writes) == 1
    assert loaded["samples"][0]["completion"] == "timeout"
    assert loaded["samples"][0]["status"] == "timeout"
    assert loaded["summary"]["valid_count"] == 0


def test_observed_complete_frames_allow_fast_sequential_polling_on_one_session(monkeypatch, tmp_path):
    raw = sensor_text().encode()
    module, transport = shell_simulator(monkeypatch, [raw[:3], raw[3:-1], raw[-1:]])
    output = tmp_path / "fast-polling.jsonl"
    assert module.main(["watch", "--duration", "0.75", "--interval", "0.1", "--timeout", "0.05",
                        "--output", str(output)]) == 0
    loaded = load_capture(output)
    assert len(transport.writes) >= 5
    assert transport.opened == transport.closed == ["/dev/ttyACM8"]
    assert loaded["summary"]["valid_count"] == len(transport.writes)
    assert all(sample["completion"] == "complete" for sample in loaded["samples"])
    assert all(sample["response_ms"] < 25 for sample in loaded["samples"])
    assert loaded["summary"]["timing"]["effective_hz"] == pytest.approx(10)
    assert all(next_start - start >= 0.1 - 1e-9 for start, next_start in zip(transport.writes, transport.writes[1:]))
