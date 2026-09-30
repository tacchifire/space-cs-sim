"""The offline report preserves recorded values and treats capture text as data."""
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

from cuberange.flatsat import report, usb  # noqa: E402


def sample(index, temperature, *, status="ok", humidity=45.0, errors=None):
    return {"event": "sensor_sample", "index": index,
            "time_utc": f"2026-09-30T12:00:{index:02d}+00:00",
            "elapsed_s": float(index), "response_ms": 250.0, "completion": "quiet",
            "status": status, "values": {"temperature_c": temperature, "pressure_pa": 101000.0,
                "humidity_percent": humidity, "accel_x_mg": 1.0, "accel_y_mg": 2.0, "accel_z_mg": 3.0},
            "errors": errors or [], "missing_fields": []}


def capture(tmp_path, *events, name="capture.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


def embedded(document):
    found = re.search(r'<script id="capture-data" type="application/json">(.*?)</script>', document, re.S)
    assert found, "report has no embedded capture data"
    return json.loads(found.group(1))


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_report_replays_limits_and_gaps_instead_of_trusting_recorded_alerts(tmp_path):
    source = capture(tmp_path,
        {"event": "session", "limits": [{"metric": "temperature_c", "minimum": None, "maximum": 24.0001}]},
        sample(1, 24.0002), sample(2, None, status="partial"), sample(3, 23.0),
        {"event": "alert", "transition": "recovered", "metric": "temperature_c", "value": 0})
    output = tmp_path / "limits.html"
    report.write_report(source, output)
    document = output.read_text()
    analysis = embedded(document)["data"]["alerts"]
    assert [event["transition"] for event in analysis["events"]] == ["triggered", "unavailable", "resumed"]
    assert analysis["recovery_count"] == 0
    assert 'id="limit-events"' in document
    assert "<td>24.0001</td>" in document
    assert "<td>24.0002</td>" in document
    assert "判定再開（範囲内）" in document


def test_report_explicit_limits_override_recorded_ranges_without_changing_source(tmp_path):
    from cuberange.flatsat.alerts import parse_limit
    source = capture(tmp_path,
        {"event": "session", "limits": [{"metric": "temperature_c", "minimum": None, "maximum": 30}]},
        sample(1, 25.0))
    original = source.read_bytes()
    output = tmp_path / "overridden.html"
    report.write_report(source, output, limits=[parse_limit("temperature_c::24.0001")])
    analysis = embedded(output.read_text())["data"]["alerts"]
    assert analysis["trigger_count"] == 1
    assert analysis["limits"][0]["maximum"] == 24.0001
    assert source.read_bytes() == original


def test_report_is_offline_and_uses_the_actual_capture_values(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("offline report attempted USB or network IO")
    monkeypatch.setattr(usb, "discover_ports", forbidden)
    monkeypatch.setattr(usb, "SerialSession", forbidden)
    monkeypatch.setattr(usb, "_serial_module", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    source = capture(tmp_path, sample(1, 23.5), sample(2, 24.5))
    output = tmp_path / "report.html"
    report.write_report(source, output)
    document = output.read_text(encoding="utf-8")
    payload = embedded(document)
    assert payload["data"]["source"] == str(source.resolve())
    stats = payload["data"]["summary"]["metrics"]["temperature_c"]
    assert {key: stats[key] for key in ("count", "min", "max", "mean")} == {
        "count": 2, "min": 23.5, "max": 24.5, "mean": 24.0,
    }
    assert [point["value"] for point in payload["charts"]["temperature_c"]["segments"][0]] == [23.5, 24.5]
    assert payload["data"]["samples"][0]["response_ms"] == 250.0
    assert "照会処理 (ms)" in document
    assert "照会開始 (UTC)" in document
    parser = Tags()
    parser.feed(document)
    assert not any(tag in ("img", "iframe", "link") for tag, _ in parser.tags)
    assert not any(tag == "script" and "src" in attrs for tag, attrs in parser.tags)


def test_missing_partial_invalid_and_timeout_samples_create_graph_gaps(tmp_path):
    source = capture(tmp_path,
        sample(1, -2.0), sample(2, 0.0),
        sample(3, None, status="partial"),
        sample(4, 9.0),
        sample(5, 10.0, status="invalid", errors=["bad sensor line"]),
        sample(6, 11.0),
        sample(7, 12.0, status="timeout"),
        sample(8, 13.0, status="partial", humidity=None),
        sample(9, 14.0))
    output = tmp_path / "report.html"
    report.write_report(source, output)
    document = output.read_text()
    payload = embedded(document)
    chart = payload["charts"]["temperature_c"]
    assert [[point["value"] for point in segment] for segment in chart["segments"]] == [
        [-2.0, 0.0], [9.0], [11.0], [14.0],
    ]
    assert chart["point_count"] == 5
    assert len([item for item in chart["elements"] if item["tag"] == "path"]) == 1
    assert all(point["status"] == "ok" for segment in chart["segments"] for point in segment)
    assert payload["data"]["summary"]["failed_count"] == 4
    assert "一部欠測" in document and "応答なし" in document and "bad sensor line" in document
    # A real zero is retained; the absent sample is not substituted with another zero.
    assert payload["data"]["samples"][1]["values"]["temperature_c"] == 0.0
    assert payload["data"]["samples"][2]["values"]["temperature_c"] is None
    assert 'class="measurement">13<' in document, "partial values should remain available for diagnosis"


def test_empty_capture_explicitly_reports_no_measurements(tmp_path):
    source = capture(tmp_path)
    output = tmp_path / "empty.html"
    report.write_report(source, output)
    document = output.read_text()
    payload = embedded(document)
    assert payload["data"]["summary"]["sample_count"] == 0
    assert payload["data"]["summary"]["metrics"]["temperature_c"]["mean"] is None
    assert "この記録にはセンサ測定がありません。" in document
    assert all(chart["segments"] == [] and chart["elements"] == [] for chart in payload["charts"].values())
    assert '<svg id="chart"' in document and 'グラフ" hidden>' in document


def test_untrusted_capture_strings_cannot_escape_html_or_json_scripts(tmp_path):
    malicious = '</script><img src=x onerror="alert(1)">@@JSON@@\u2028&'
    session = {"event": "session", "mode": malicious,
               "ports": [{"serial_number": malicious, "path": malicious, "role": "shell"}]}
    source = capture(tmp_path, session, sample(1, None, status="invalid", errors=[malicious]),
                     {"event": "error", "message": malicious})
    output = tmp_path / "report.html"
    report.write_report(source, output)
    document = output.read_text()
    payload = embedded(document)
    assert payload["data"]["session"]["mode"] == malicious
    assert payload["data"]["samples"][0]["errors"] == [malicious]
    assert "\\u003c/script\\u003e" in document
    assert "\\u2028" in document
    assert "&lt;/script&gt;&lt;img" in document
    assert "@@JSON@@" in document, "log text must not become a template placeholder"
    assert document.count("</script>") == 2
    assert ".innerHTML" not in document
    parser = Tags()
    parser.feed(document)
    assert not any(tag == "img" for tag, _ in parser.tags)
    assert sum(tag == "script" for tag, _ in parser.tags) == 2


def test_interrupted_session_with_no_samples_displays_the_recorded_error(tmp_path):
    source = capture(tmp_path, {"event": "session", "mode": "watch", "ports": []},
                     {"event": "error", "message": "USB disconnected"},
                     {"event": "end", "reason": "device_error"})
    output = tmp_path / "report.html"
    report.write_report(source, output)
    document = output.read_text()
    assert "この記録にはセンサ測定がありません。" in document
    assert "記録の終了状態" in document
    assert "USB disconnected" in document
    assert "デバイスのエラーで記録を終了しました。" in document


def test_report_refuses_to_overwrite_either_a_previous_report_or_the_capture(tmp_path):
    source = capture(tmp_path, sample(1, 20.0))
    output = tmp_path / "report.html"
    output.write_bytes(b"previous report")
    before = source.read_bytes()
    with pytest.raises(FileExistsError):
        report.write_report(source, output)
    assert output.read_bytes() == b"previous report"
    with pytest.raises(FileExistsError):
        report.write_report(source, source)
    assert source.read_bytes() == before


def test_raw_capture_link_uses_a_quoted_relative_path(tmp_path):
    source = capture(tmp_path, name='capture "&#.jsonl')
    output = tmp_path / "reports" / "report.html"
    report.write_report(source, output)
    parser = Tags()
    parser.feed(output.read_text())
    links = [attrs["href"] for tag, attrs in parser.tags if tag == "a"]
    assert links == ["./../capture%20%22%26%23.jsonl"]
    assert all(not link.startswith(("http:", "https:", "javascript:")) for link in links)


def test_finite_extreme_measurements_never_create_nonfinite_chart_coordinates(tmp_path):
    source = capture(tmp_path, sample(1, -1.7e308), sample(2, 1.7e308))
    output = tmp_path / "extreme.html"
    report.write_report(source, output)
    chart = embedded(output.read_text())["charts"]["temperature_c"]
    assert chart["point_count"] == 2
    for element in chart["elements"]:
        for attribute in ("cx", "cy", "x", "y", "x1", "x2", "y1", "y2"):
            if attribute in element["attrs"]:
                assert math.isfinite(element["attrs"][attribute])
        if element["tag"] == "path":
            assert "nan" not in element["attrs"]["d"] and "inf" not in element["attrs"]["d"]


def test_experiment_conditions_are_visible_and_escaped(tmp_path):
    source = capture(tmp_path, {"event": "session", "mode": "watch", "label": "<baseline>",
                               "notes": ["<script>conditions</script>"]}, sample(1, 20.0))
    output = tmp_path / "report.html"
    report.write_report(source, output)
    document = output.read_text()
    assert "実験条件" in document
    assert "&lt;baseline&gt;" in document
    assert "&lt;script&gt;conditions&lt;/script&gt;" in document
    assert "ばらつき（標準偏差）" in document
