"""Offline comparisons preserve independent timing, gaps, and untrusted log text."""
from __future__ import annotations

from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import socket
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.flatsat import comparison_report, usb  # noqa: E402


def sample(index, elapsed, temperature, *, status="ok", errors=None):
    return {"event": "sensor_sample", "index": index,
            "time_utc": f"2026-10-01T12:00:{index:02d}+00:00", "elapsed_s": elapsed,
            "response_ms": 20.0, "completion": "complete", "status": status,
            "values": {"temperature_c": temperature, "pressure_pa": 101000.0,
                       "humidity_percent": 45.0, "accel_x_mg": 1.0,
                       "accel_y_mg": 2.0, "accel_z_mg": 3.0},
            "missing_fields": [], "errors": errors or []}


def capture(tmp_path, name, samples=(), *, board="BOARD-A", label="", notes=(), extra=()):
    path = tmp_path / name
    session = {"event": "session", "mode": "watch", "label": label, "notes": list(notes),
               "ports": [{"serial_number": board, "role": "shell", "path": "/dev/teaching-board"}]}
    events = [session, *samples, *extra, {"event": "end", "reason": "duration_complete"}]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


def embedded(document):
    found = re.search(r'<script id="comparison-data" type="application/json">(.*?)</script>', document, re.S)
    assert found, "comparison has no embedded data"
    return json.loads(found.group(1))


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_comparison_is_offline_and_preserves_unequal_spacing_lengths_and_gaps(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("offline comparison attempted USB or network IO")
    monkeypatch.setattr(usb, "discover_ports", forbidden)
    monkeypatch.setattr(usb, "SerialSession", forbidden)
    monkeypatch.setattr(usb, "_serial_module", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    baseline = capture(tmp_path, "baseline.jsonl", [sample(1, 2.0, 20.0), sample(2, 2.5, 21.0),
                       sample(3, 4.0, None, status="partial"), sample(4, 6.0, 22.0)], label="通常の記録")
    candidate = capture(tmp_path, "candidate.jsonl", [sample(1, 7.0, 30.0), sample(2, 8.7, 31.0),
                        sample(3, 12.0, 32.0)], label="別の条件", notes=["窓辺で記録"])
    output = tmp_path / "comparison.html"
    comparison_report.write_comparison_report(baseline, candidate, output)
    document = output.read_text()
    payload = embedded(document)
    chart = payload["charts"]["temperature_c"]
    first, second = chart["series"]["baseline"], chart["series"]["candidate"]
    assert [[point["x"] for point in segment] for segment in first["segments"]] == [[0.0, 0.5], [4.0]]
    assert [point["x"] for point in second["segments"][0]] == pytest.approx([0.0, 1.7, 5.0])
    assert first["point_count"] == 3 and second["point_count"] == 3
    assert payload["comparison"]["metrics"]["temperature_c"]["mean_delta"] == 10.0
    assert payload["comparison"]["baseline"]["summary"]["failed_count"] == 1
    assert payload["comparison"]["board_match"] == "same"
    starts = [element["attrs"]["cx"] for element in chart["elements"]
              if element["tag"] == "circle" and element["title"].startswith("2026-10-01T12:00:01")]
    assert starts == [90.0, 90.0], "both series must use the same axes and an independent zero origin"
    assert "通常の記録" in document and "別の条件" in document and "窓辺で記録" in document
    assert "記録 4 / 正常 3 / 欠測または失敗 1" in document
    assert '<span id="baseline-stdev">1</span>' in document
    assert '<span id="candidate-stdev">1</span>' in document
    assert "<strong>0.75 Hz</strong>" in document
    assert "<strong>20 ms</strong>" in document
    assert "各記録の最初のサンプルから" in document
    parser = Tags()
    parser.feed(document)
    assert not any(tag in ("iframe", "img", "link") for tag, _ in parser.tags)
    assert not any(tag == "script" and "src" in attrs for tag, attrs in parser.tags)


def test_no_valid_candidate_leaves_mean_difference_unknown(tmp_path):
    baseline = capture(tmp_path, "baseline.jsonl", [sample(1, 1.0, 0.0)])
    candidate = capture(tmp_path, "candidate.jsonl", [sample(1, 1.0, None, status="timeout")], board=None)
    output = tmp_path / "comparison.html"
    comparison_report.write_comparison_report(baseline, candidate, output)
    document = output.read_text()
    payload = embedded(document)
    stats = payload["comparison"]["metrics"]["temperature_c"]
    assert stats["baseline_mean"] == 0.0
    assert stats["candidate_mean"] is None and stats["mean_delta"] is None
    assert stats["candidate_count"] == 0
    assert payload["charts"]["temperature_c"]["series"]["candidate"]["segments"] == []
    assert '<span id="mean-delta">—</span>' in document
    assert '<span id="baseline-stdev">—</span>' in document
    assert '<span id="candidate-stdev">—</span>' in document
    assert "正常な測定値がありません。" in document
    assert "基板 ID を照合できません" in document


def test_empty_failed_logs_have_no_fabricated_points_and_display_errors(tmp_path):
    baseline = capture(tmp_path, "baseline.jsonl", extra=[{"event": "error", "message": "USB disconnected"}])
    candidate = capture(tmp_path, "candidate.jsonl")
    output = tmp_path / "comparison.html"
    comparison_report.write_comparison_report(baseline, candidate, output)
    document = output.read_text()
    payload = embedded(document)
    assert all(chart["point_count"] == 0 and chart["elements"] == [] for chart in payload["charts"].values())
    assert "両方の記録に正常な測定値がありません。" in document
    assert "USB disconnected" in document
    assert payload["comparison"]["metrics"]["temperature_c"]["mean_delta"] is None


def test_missing_repeated_and_backward_timestamps_split_traces_without_inventing_spacing():
    rows = [sample(1, 2.0, 20.0), sample(2, 4.0, 21.0), sample(3, None, 22.0),
            sample(4, 5.0, 23.0), sample(5, 5.0, 24.0), sample(6, 3.0, 25.0),
            sample(7, 8.0, 26.0)]
    chart = comparison_report._overlay(rows, [], "temperature_c")
    trace = chart["series"]["baseline"]
    assert [[point["x"] for point in segment] for segment in trace["segments"]] == [[0.0, 2.0], [3.0], [6.0]]
    assert trace["untimed_count"] == 3
    assert trace["times"] == [0.0, 2.0, 3.0, 6.0]
    assert comparison_report._elapsed_trace([sample(1, None, 20.0), sample(2, 2.0, 21.0)],
                                          "temperature_c")["segments"] == []


def test_large_finite_values_produce_finite_coordinates_and_json(tmp_path):
    baseline = capture(tmp_path, "baseline.jsonl", [sample(1, 0.0, -1.7e308), sample(2, 1.7e308, 1.7e308)])
    candidate = capture(tmp_path, "candidate.jsonl", [sample(1, 1.0, 1.7e308)])
    output = tmp_path / "comparison.html"
    comparison_report.write_comparison_report(baseline, candidate, output)
    document = output.read_text()
    payload = embedded(document)
    json.dumps(payload, allow_nan=False)
    for chart in payload["charts"].values():
        for element in chart["elements"]:
            for value in element["attrs"].values():
                if isinstance(value, (int, float)):
                    assert math.isfinite(value)
            if element["tag"] == "path":
                assert "nan" not in element["attrs"]["d"].lower()
                assert "inf" not in element["attrs"]["d"].lower()
    assert payload["charts"]["temperature_c"]["point_count"] == 3


def test_labels_notes_errors_and_placeholder_text_cannot_inject_html(tmp_path):
    malicious = '</script><img src=x onerror="alert(1)">@@JSON@@\u2028&'
    baseline = capture(tmp_path, "baseline.jsonl", [sample(1, 0.0, 20.0)], label=malicious,
                       notes=[malicious], extra=[{"event": "error", "message": malicious}])
    candidate = capture(tmp_path, "candidate.jsonl", [sample(1, 0.0, 21.0)], label=malicious)
    output = tmp_path / "comparison.html"
    comparison_report.write_comparison_report(baseline, candidate, output)
    document = output.read_text()
    payload = embedded(document)
    assert payload["labels"]["baseline"] == malicious
    assert payload["comparison"]["baseline"]["session"]["notes"] == [malicious]
    assert "\\u003c/script\\u003e" in document and "\\u2028" in document
    assert "&lt;/script&gt;&lt;img" in document and "@@JSON@@" in document
    assert document.count("</script>") == 2 and ".innerHTML" not in document
    parser = Tags()
    parser.feed(document)
    assert not any(tag == "img" for tag, _ in parser.tags)


def test_different_board_ids_and_raw_links_are_explicit(tmp_path):
    baseline = capture(tmp_path, 'baseline "&#.jsonl', [sample(1, 0.0, 20.0)], board="BOARD-A")
    candidate = capture(tmp_path, "candidate.jsonl", [sample(1, 0.0, 21.0)], board="BOARD-B")
    output = tmp_path / "reports" / "comparison.html"
    comparison_report.write_comparison_report(baseline, candidate, output)
    document = output.read_text()
    assert "基板 ID は異なります" in document
    parser = Tags()
    parser.feed(document)
    links = [attrs["href"] for tag, attrs in parser.tags if tag == "a"]
    assert links == ["./../baseline%20%22%26%23.jsonl", "./../candidate.jsonl"]


def test_comparison_never_overwrites_a_report_or_either_input_capture(tmp_path):
    baseline = capture(tmp_path, "baseline.jsonl", [sample(1, 0.0, 20.0)])
    candidate = capture(tmp_path, "candidate.jsonl", [sample(1, 0.0, 21.0)])
    output = tmp_path / "comparison.html"
    output.write_bytes(b"previous report")
    before = {path: path.read_bytes() for path in (baseline, candidate, output)}
    for target in (output, baseline, candidate):
        with pytest.raises(FileExistsError):
            comparison_report.write_comparison_report(baseline, candidate, target)
    assert {path: path.read_bytes() for path in before} == before


def test_supplied_snapshot_is_not_reloaded_after_capture_files_change(monkeypatch, tmp_path):
    baseline = capture(tmp_path, "baseline.jsonl", [sample(1, 0.0, 20.0)])
    candidate = capture(tmp_path, "candidate.jsonl", [sample(1, 0.0, 21.0)])
    snapshot = comparison_report.compare_captures(baseline, candidate)
    capture(tmp_path, "baseline.jsonl", [sample(1, 0.0, 100.0)])
    capture(tmp_path, "candidate.jsonl", [sample(1, 0.0, 200.0)])

    def forbidden(*args, **kwargs):
        pytest.fail("report reloaded a comparison that was supplied as a snapshot")
    monkeypatch.setattr(comparison_report, "compare_captures", forbidden)
    output = tmp_path / "snapshot.html"
    comparison_report.write_comparison_report(baseline, candidate, output, comparison=snapshot)
    payload = embedded(output.read_text())
    assert payload["comparison"]["metrics"]["temperature_c"]["mean_delta"] == 1.0
    assert payload["comparison"]["baseline"]["samples"][0]["values"]["temperature_c"] == 20.0
    assert payload["comparison"]["candidate"]["samples"][0]["values"]["temperature_c"] == 21.0
