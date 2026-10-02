"""Event-only monitoring reports must not imply continuous sensor measurements."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cuberange.flatsat.report import _render


def room_data(*, label="room", events=None):
    return {"session": {"mode": "room_watch", "label": label}, "samples": [],
            "summary": {"sample_count": 0}, "end": {"reason": "stopped"},
            "room_watch": {"snapshot": {"observed_count": 37, "valid_count": 36,
                "failed_count": 1, "phase": "stopped", "reason": "stopped",
                "baseline": {"temperature_c": 25, "accel_z_mg": 1000},
                "thresholds": {"movement_mg": 80, "temperature_c": 2}},
                "events": events or []}}


def changed(index):
    return {"transition": "changed", "metric": "movement_mg", "value": 120 + index,
            "threshold": 80, "time_utc": "2026-10-01T00:00:00+00:00", "elapsed_s": index}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_room_report_counts_queries_and_changes_without_a_fabricated_graph(tmp_path):
    event = changed(1)
    document = _render(room_data(events=[event]), tmp_path / "source.jsonl", tmp_path / "out.html")
    assert "照会総数<strong>37</strong>" in document
    assert "有効な応答<strong>36</strong>" in document
    assert "変化の検出<strong>1</strong>" in document
    assert "変化を検出" in document and "<td>121</td>" in document
    assert "監視を停止" in document
    tags = Tags()
    tags.feed(document)
    assert not any(tag == "svg" for tag, _ in tags.tags)
    assert not any(attrs.get("id") == "metric" for _, attrs in tags.tags)


def test_room_report_retains_escaped_event_text_as_data(tmp_path):
    unsafe = '</script><img src=x onerror="alert(1)">'
    event = {**changed(1), "transition": unsafe, "time_utc": unsafe}
    document = _render(room_data(label=unsafe, events=[event]),
                       tmp_path / "source.jsonl", tmp_path / "out.html")
    tags = Tags()
    tags.feed(document)
    assert not any(tag == "img" for tag, _ in tags.tags)
    assert "&lt;img" in document
    embedded = re.search(r'<script id="capture-data" type="application/json">(.*?)</script>',
                         document, re.S)
    assert json.loads(embedded.group(1))["room_watch"]["events"][0]["time_utc"] == unsafe


def test_room_report_bounds_visible_history_and_preserves_total_and_raw_link(tmp_path):
    document = _render(room_data(events=[changed(index) for index in range(250)]),
                       tmp_path / "source file.jsonl", tmp_path / "out.html")
    assert "保存イベント 250 件のうち直近 200 件" in document
    assert "変化の検出<strong>250</strong>" in document
    assert document.count("<td>変化を検出</td>") == 200
    assert 'href="./source%20file.jsonl"' in document


def test_room_report_with_no_events_does_not_claim_sensor_measurements(tmp_path):
    document = _render(room_data(), tmp_path / "source.jsonl", tmp_path / "out.html")
    assert "保存されたイベントはありません" in document
    assert "変化の検出<strong>0</strong>" in document
    assert "通常時の全サンプルを連続計測したグラフではありません" in document


def test_interrupted_room_report_marks_unknown_counts_and_translates_reason(tmp_path):
    data = room_data()
    snapshot = data["room_watch"]["snapshot"]
    snapshot.update({"observed_count": None, "valid_count": None, "reason": "interrupted"})
    document = _render(data, tmp_path / "source.jsonl", tmp_path / "out.html")
    assert "照会総数<strong>不明</strong>" in document
    assert "有効な応答<strong>不明</strong>" in document
    assert "終了状態: 監視を中断" in document
    assert "None" not in document
