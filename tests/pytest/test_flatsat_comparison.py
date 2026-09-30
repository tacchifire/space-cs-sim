"""Saved experiment comparisons remain descriptive, offline, and loss-aware."""
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.flatsat.comparison import compare_captures  # noqa: E402
from cuberange.flatsat.telemetry import parse_sensor_response  # noqa: E402
from cuberange.flatsat.usb import FlatSatError  # noqa: E402


def recording(path, temperatures, *, board="TEST-BOARD", failed=(), finish=True):
    events = [{"event": "session", "mode": "watch", "label": path.stem,
               "notes": ["conditions recorded by operator"],
               "ports": [{"serial_number": board, "role": "shell"}]}]
    for index, temperature in enumerate(temperatures, 1):
        text = (f"sensors\rAccel: x=0 mg y=0 mg z=1000 mg\r\nTemp: {temperature} C\r\n"
                "Press: 100000 Pa\r\nHumid: 50%\r\n\n")
        parsed = parse_sensor_response(text)
        if index in failed:
            parsed["status"] = "timeout"
        events.append({"event": "sensor_sample", "index": index, "elapsed_s": index - 1,
                       "completion": "timeout" if index in failed else "complete", **parsed})
    if finish:
        events.append({"event": "end", "reason": "duration_complete"})
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path


def test_mean_difference_compares_independent_runs_not_paired_sample_counts(tmp_path):
    baseline = recording(tmp_path / "first.jsonl", [10, 12])
    candidate = recording(tmp_path / "second.jsonl", [15, 18, 21])
    result = compare_captures(baseline, candidate)
    assert result["board_match"] == "same"
    assert result["notes"] == []
    assert result["metrics"]["temperature_c"] == {
        "baseline_mean": 11.0, "candidate_mean": 18.0, "mean_delta": 7.0,
        "baseline_count": 2, "candidate_count": 3,
    }
    assert result["metrics"]["accel_x_mg"]["mean_delta"] == 0.0


def test_failed_observation_does_not_change_mean_difference(tmp_path):
    baseline = recording(tmp_path / "first.jsonl", [10, 99], failed=(2,))
    candidate = recording(tmp_path / "second.jsonl", [13])
    result = compare_captures(baseline, candidate)
    assert result["metrics"]["temperature_c"]["mean_delta"] == 3.0
    assert result["baseline"]["summary"]["failed_count"] == 1


@pytest.mark.parametrize("board,expected,note", [
    ("OTHER-BOARD", "different", "different_boards"),
    (None, "unknown", "unknown_board_id"),
])
def test_board_identity_mismatch_or_missing_is_visible(tmp_path, board, expected, note):
    baseline = recording(tmp_path / "first.jsonl", [10])
    candidate = recording(tmp_path / "second.jsonl", [13], board=board)
    result = compare_captures(baseline, candidate)
    assert result["board_match"] == expected
    assert note in result["notes"]


def test_missing_baseline_never_becomes_zero_or_available_delta(tmp_path):
    baseline = recording(tmp_path / "first.jsonl", [])
    candidate = recording(tmp_path / "second.jsonl", [13], finish=False)
    result = compare_captures(baseline, candidate)
    assert result["metrics"]["temperature_c"]["baseline_mean"] is None
    assert result["metrics"]["temperature_c"]["mean_delta"] is None
    assert "baseline_no_valid_samples" in result["notes"]
    assert "candidate_unfinished" in result["notes"]


def test_nonfinite_difference_is_unavailable_and_reported(tmp_path):
    baseline = recording(tmp_path / "first.jsonl", [-1.7e308])
    candidate = recording(tmp_path / "second.jsonl", [1.7e308])
    result = compare_captures(baseline, candidate)
    assert result["metrics"]["temperature_c"]["mean_delta"] is None
    assert "nonfinite_difference:temperature_c" in result["notes"]
    json.dumps(result, allow_nan=False)


def test_comparison_rejects_truncated_source_with_line_context(tmp_path):
    baseline = recording(tmp_path / "first.jsonl", [10])
    candidate = tmp_path / "second.jsonl"
    candidate.write_text('{"event":"sensor_sample"')
    with pytest.raises(FlatSatError, match="line 1"):
        compare_captures(baseline, candidate)


def test_json_comparison_cli_does_not_touch_usb_or_mix_console_output(monkeypatch, tmp_path, capsys):
    module = importlib.import_module("cuberange.flatsat.__main__")
    def forbidden(*args, **kwargs):
        pytest.fail("offline comparison opened USB")
    monkeypatch.setattr(module, "discover_ports", forbidden)
    monkeypatch.setattr(module, "SerialSession", forbidden)
    baseline = recording(tmp_path / "first.jsonl", [10])
    candidate = recording(tmp_path / "second.jsonl", [13])
    assert module.main(["compare", str(baseline), str(candidate), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["metrics"]["temperature_c"]["mean_delta"] == 3.0
    assert result["baseline"]["session"]["label"] == "first"
    assert "samples" not in result["baseline"]
