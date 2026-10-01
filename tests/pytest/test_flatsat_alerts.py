"""Limits and alert transitions reject malformed settings and preserve data gaps."""
from __future__ import annotations

import argparse
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import socket
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.flatsat import alerts, telemetry, usb  # noqa: E402

METRIC = "temperature_c"


def observation(value=None, *, status="ok", completion="complete", metric=METRIC, index=1):
    return {"index": index, "time_utc": f"2026-10-01T12:00:{index:02d}+00:00",
            "elapsed_s": float(index), "status": status, "completion": completion,
            "values": {metric: value}}


def test_limits_are_frozen_normalized_finite_and_serializable():
    limit = alerts.Limit(METRIC, 20, 30)
    assert limit.as_dict() == {"metric": METRIC, "minimum": 20.0, "maximum": 30.0}
    assert isinstance(limit.minimum, float) and isinstance(limit.maximum, float)
    json.dumps(limit.as_dict(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        limit.minimum = 21.0
    assert alerts.Limit(METRIC, 0, 0).as_dict()["maximum"] == 0.0


@pytest.mark.parametrize("minimum,maximum", [
    (None, None), (True, None), (None, False), (float("nan"), None),
    (None, float("inf")), (float("-inf"), None), (10**400, None),
    ("20", None), (None, "30"), (31, 30),
])
def test_malformed_or_nonfinite_limit_bounds_are_rejected(minimum, maximum):
    with pytest.raises(ValueError):
        alerts.Limit(METRIC, minimum, maximum)


@pytest.mark.parametrize("metric", ["temperature", "unknown", None, True, []])
def test_metric_names_must_be_known_strings(metric):
    with pytest.raises(ValueError, match="Unknown sensor metric"):
        alerts.Limit(metric, 20, 30)


@pytest.mark.parametrize("text,minimum,maximum", [
    ("temperature_c:20:30", 20.0, 30.0), ("temperature_c::30", None, 30.0),
    ("temperature_c:20:", 20.0, None), (" temperature_c : -2e1 : 3e1 ", -20.0, 30.0),
    ("acceleration_norm_mg::1000", None, 1000.0),
])
def test_cli_limit_syntax_supports_blank_bounds_and_numeric_notation(text, minimum, maximum):
    limit = alerts.parse_limit(text)
    assert limit.minimum == minimum and limit.maximum == maximum


@pytest.mark.parametrize("text", [
    "temperature_c", "temperature_c:20", "temperature_c:20:30:40", "temperature_c::",
    "temperature_c:nan:30", "temperature_c:20:inf", "temperature_c:true:30",
    "temperature_c:20:false", "temperature_c:31:30", "missing:20:30", None,
])
def test_bad_cli_limits_raise_value_error_for_argparse(text):
    with pytest.raises(ValueError):
        alerts.parse_limit(text)


def test_argparse_rejects_bad_threshold_before_any_device_operation(capsys):
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=alerts.parse_limit, action="append")
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["--limit", "temperature_c:20:NaN"])
    assert error.value.code == 2
    assert "--limit" in capsys.readouterr().err


def test_duplicate_metrics_are_rejected_and_validated_list_is_independent():
    limit = alerts.Limit(METRIC, 20, 30)
    with pytest.raises(ValueError, match="Duplicate limit"):
        alerts.validate_limits([limit, alerts.Limit(METRIC, None, 31)])
    with pytest.raises(ValueError, match="Duplicate limit"):
        alerts.AlertTracker([limit, limit])
    original = [limit]
    normalized = alerts.validate_limits(original)
    assert isinstance(normalized, list)
    original.clear()
    assert normalized == [limit]
    assert alerts.validate_limits([]) == []
    with pytest.raises(ValueError):
        alerts.validate_limits([limit.as_dict()])


def test_inclusive_bounds_initial_normal_and_repeated_bad_states_are_quiet():
    tracker = alerts.AlertTracker([alerts.Limit(METRIC, 20, 30)])
    assert tracker.states == {METRIC: "unknown"}
    assert tracker.update(observation(20)) == []
    assert tracker.update(observation(30)) == []
    trigger = tracker.update(observation(31))
    assert trigger == [{"metric": METRIC, "minimum": 20.0, "maximum": 30.0,
                        "value": 31.0, "state": "above", "previous_state": "normal",
                        "transition": "triggered"}]
    assert tracker.update(observation(32)) == []
    recovered = tracker.update(observation(30))
    assert recovered[0]["transition"] == "recovered"
    assert recovered[0]["previous_state"] == "above"
    assert tracker.states == {METRIC: "normal"}
    copy = tracker.states
    copy[METRIC] = "above"
    assert tracker.update(observation(25)) == [], "external state views must not mutate the tracker"


def test_initial_bad_and_crossing_to_the_other_bad_side_trigger_independently():
    tracker = alerts.AlertTracker([alerts.Limit(METRIC, 20, 30)])
    below = tracker.update(observation(19))[0]
    assert below["state"] == "below" and below["transition"] == "triggered"
    assert below["previous_state"] is None
    above = tracker.update(observation(31))[0]
    assert above["state"] == "above" and above["previous_state"] == "below"
    assert above["transition"] == "triggered"


@pytest.mark.parametrize("gap", [
    observation(31, status="partial"), observation(31, status="invalid"),
    observation(31, status="timeout"), observation(31, completion="timeout"),
    observation(None), observation(True), observation(float("nan")),
    observation(float("inf")), observation(10**400),
    {"status": "ok", "values": {}}, {"status": "ok", "values": None},
])
def test_bad_to_unknown_to_normal_is_resumed_instead_of_false_recovery(gap):
    tracker = alerts.AlertTracker([alerts.Limit(METRIC, 20, 30)])
    tracker.update(observation(31))
    unavailable = tracker.update(gap)
    assert unavailable[0]["state"] == "unknown"
    assert unavailable[0]["transition"] == "unavailable"
    assert unavailable[0]["value"] is None
    assert tracker.update(gap) == []
    resumed = tracker.update(observation(25))
    assert resumed[0]["transition"] == "resumed"
    assert resumed[0]["previous_state"] == "unknown"
    json.dumps(unavailable + resumed, allow_nan=False)


def test_initial_unknown_is_announced_once_and_bad_after_gap_triggers_again():
    tracker = alerts.AlertTracker([alerts.Limit(METRIC, None, 30)])
    initial = tracker.update(observation(status="timeout"))
    assert initial[0]["transition"] == "unavailable"
    assert initial[0]["previous_state"] is None
    assert tracker.update(observation(status="timeout")) == []
    assert tracker.update(observation(31))[0]["transition"] == "triggered"


def test_one_sided_and_exact_limits_evaluate_without_artificial_other_bounds():
    minimum = alerts.AlertTracker([alerts.Limit(METRIC, 0, None)])
    assert minimum.update(observation(0)) == []
    assert minimum.update(observation(-1))[0]["state"] == "below"
    assert minimum.update(observation(1e100))[0]["transition"] == "recovered"
    maximum = alerts.AlertTracker([alerts.Limit(METRIC, None, 0)])
    assert maximum.update(observation(-1e100)) == []
    assert maximum.update(observation(1))[0]["state"] == "above"
    exact = alerts.AlertTracker([alerts.Limit(METRIC, 0, 0)])
    assert exact.update(observation(0)) == []
    assert exact.update(observation(-1))[0]["state"] == "below"


def test_metrics_have_independent_states_and_deadline_response_cannot_evaluate():
    tracker = alerts.AlertTracker([alerts.Limit(METRIC, 20, 30), alerts.Limit("humidity_percent", None, 50)])
    sample = observation(25)
    sample["values"]["humidity_percent"] = 60
    first = tracker.update(sample)
    assert [event["metric"] for event in first] == ["humidity_percent"]
    sample["completion"] = "timeout"
    assert [event["state"] for event in tracker.update(sample)] == ["unknown", "unknown"]
    assert tracker.states == {METRIC: "unknown", "humidity_percent": "unknown"}


def test_empty_tracker_is_valid_when_watch_has_no_configured_limits():
    tracker = alerts.AlertTracker([])
    assert tracker.update(observation(25)) == []
    assert tracker.update(observation(status="timeout")) == []
    assert tracker.states == {}


def test_offline_replay_uses_stored_limits_and_preserves_event_sample_annotations():
    limit = alerts.Limit(METRIC, 20, 30)
    samples = [observation(25, index=1), observation(31, index=2), observation(32, index=3),
               observation(29, index=4), observation(None, status="timeout", index=5),
               observation(25, index=6), observation(19, index=7)]
    result = alerts.evaluate_capture({"session": {"limits": [limit.as_dict()]}, "samples": samples})
    assert result["limits"] == [limit.as_dict()]
    assert [event["transition"] for event in result["events"]] == [
        "triggered", "recovered", "unavailable", "resumed", "triggered",
    ]
    assert [event["index"] for event in result["events"]] == [2, 4, 5, 6, 7]
    assert result["events"][0]["time_utc"] == samples[1]["time_utc"]
    assert result["events"][0]["elapsed_s"] == 2.0
    assert result["final_states"] == {METRIC: "below"}
    assert result["trigger_count"] == 2 and result["recovery_count"] == 1
    assert result["unavailable_count"] == 1 and result["resumed_count"] == 1
    assert result["sample_count"] == 7
    assert result["evaluated_count"] == 6 and result["failed_count"] == 1
    assert result["capture_quality"] == {"completion": "unknown", "notes": []}
    json.dumps(result, allow_nan=False)


def test_explicit_limits_override_stored_settings_and_empty_replay_stays_unobserved():
    data = {"session": {"limits": "malformed unused settings"}, "samples": [observation(31)]}
    result = alerts.evaluate_capture(data, [alerts.Limit(METRIC, None, 35)])
    assert result["events"] == [] and result["final_states"] == {METRIC: "normal"}
    empty = alerts.evaluate_capture({"session": {}, "samples": []}, [alerts.Limit(METRIC, None, 30)])
    assert empty["events"] == [] and empty["final_states"] == {METRIC: "unknown"}
    assert empty["sample_count"] == 0 and empty["unavailable_count"] == 0
    assert empty["evaluated_count"] == 0 and empty["failed_count"] == 0


@pytest.mark.parametrize("records", [
    None, "temperature_c:20:30", {}, ["temperature_c:20:30"], [{}],
    [{"metric": METRIC, "maximum": 30}],
    [{"metric": METRIC, "minimum": None, "maximum": 30, "extra": 1}],
    [{"metric": "not_a_sensor", "minimum": 20, "maximum": 30}],
    [{"metric": METRIC, "minimum": True, "maximum": 30}],
    [{"metric": METRIC, "minimum": 20, "maximum": float("nan")}],
    [{"metric": METRIC, "minimum": 20, "maximum": "30"}],
    [{"metric": METRIC, "minimum": 20, "maximum": 30},
     {"metric": METRIC, "minimum": None, "maximum": 40}],
])
def test_malformed_imported_limit_settings_are_rejected_strictly(records):
    with pytest.raises(ValueError):
        alerts.evaluate_capture({"session": {"limits": records}, "samples": []})


@pytest.mark.parametrize("data,limits", [
    ({"session": {}, "samples": []}, None),
    ({"session": {"limits": []}, "samples": []}, None),
    ({"session": {"limits": [alerts.Limit(METRIC, 20, 30).as_dict()]}, "samples": []}, []),
])
def test_replay_without_active_limits_raises_a_clear_error(data, limits):
    with pytest.raises(ValueError, match="No alert limits"):
        alerts.evaluate_capture(data, limits)


def test_saved_capture_derived_acceleration_metric_is_evaluated_without_usb(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("offline alert replay attempted USB or network IO")
    monkeypatch.setattr(usb, "discover_ports", forbidden)
    monkeypatch.setattr(usb, "SerialSession", forbidden)
    monkeypatch.setattr(usb, "_serial_module", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    limit = alerts.Limit("acceleration_norm_mg", None, 2.5)
    sample = observation(25)
    sample.update(event="sensor_sample", response_ms=5.0, errors=[])
    sample["values"].update(pressure_pa=101000.0, humidity_percent=45.0,
                            accel_x_mg=1.0, accel_y_mg=2.0, accel_z_mg=2.0,
                            acceleration_norm_mg=0.0)
    events = [{"event": "session", "mode": "watch", "limits": [limit.as_dict()]}, sample,
              {"event": "end", "reason": "duration_complete"}]
    capture = tmp_path / "norm.jsonl"
    capture.write_text("".join(json.dumps(event) + "\n" for event in events))
    result = alerts.evaluate_capture(telemetry.load_capture(capture))
    assert result["events"][0]["metric"] == "acceleration_norm_mg"
    assert result["events"][0]["value"] == 3.0, "use the loader's derived value instead of an edited magnitude"
    assert result["events"][0]["transition"] == "triggered"


def test_nonfinite_or_unsupported_event_metadata_does_not_escape_into_json():
    sample = observation(31)
    sample.update(index=float("nan"), time_utc={"untrusted": float("inf")}, elapsed_s=float("inf"))
    result = alerts.evaluate_capture({"samples": [sample]}, [alerts.Limit(METRIC, None, 30)])
    assert result["events"][0]["index"] is None
    assert result["events"][0]["time_utc"] is None
    assert result["events"][0]["elapsed_s"] is None
    json.dumps(result, allow_nan=False)


def test_replay_quality_counts_only_samples_usable_for_every_configured_metric():
    samples = [observation(25, index=1), observation(25, status="partial", index=2),
               observation(25, completion="timeout", index=3), observation(25, index=4)]
    for sample in samples[:3]:
        sample["values"]["humidity_percent"] = 45
    samples[3]["values"]["humidity_percent"] = None
    quality = {"completion": "interrupted", "notes": ["unfinished"],
               "status_counts": {"ok": 3, "partial": 1, "invalid": 0, "timeout": 0}}
    result = alerts.evaluate_capture({"samples": samples, "summary": {"quality": quality}},
                                    [alerts.Limit(METRIC, 20, 30), alerts.Limit("humidity_percent", None, 50)])
    assert result["sample_count"] == 4
    assert result["evaluated_count"] == 1 and result["failed_count"] == 3
    assert result["capture_quality"] == quality
    assert result["capture_quality"] is not quality
