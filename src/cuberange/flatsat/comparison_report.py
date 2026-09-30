"""Offline, standalone comparisons of two recorded FlatSat sensor sessions."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote

from .comparison import compare_captures
from .report import (
    CHART_BOTTOM, CHART_HEIGHT, CHART_LEFT, CHART_RIGHT, CHART_TOP, CHART_WIDTH,
    _capture_notice, _embedded_json, _format, _number, _svg_elements,
)
from .telemetry import METRICS

RUNS = ("baseline", "candidate")
BOARD_LABELS = {"same": "基板 ID は一致", "different": "基板 ID は異なります",
                "unknown": "基板 ID を照合できません"}


def _elapsed_trace(samples: list[dict], metric: str) -> dict:
    """Align to the first sample without inventing timestamps for absent times."""
    if not samples:
        return {"segments": [], "times": [], "point_count": 0, "untimed_count": 0}
    origin = _number(samples[0].get("elapsed_s"))
    if origin is None or origin < 0:
        return {"segments": [], "times": [], "point_count": 0, "untimed_count": len(samples)}
    segments, current, times, untimed_count = [], [], [], 0
    previous = None
    for sample in samples:
        elapsed = _number(sample.get("elapsed_s"))
        if elapsed is None or elapsed < 0 or previous is not None and elapsed <= previous:
            untimed_count += 1
            if current:
                segments.append(current)
                current = []
            continue
        previous = elapsed
        x = elapsed - origin
        times.append(x)
        value = _number(sample.get("values", {}).get(metric))
        if sample.get("status") != "ok" or value is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append({"x": x, "value": value, "index": sample.get("index"),
                        "time_utc": sample.get("time_utc"), "status": "ok"})
    if current:
        segments.append(current)
    return {"segments": segments, "times": times,
            "point_count": sum(len(segment) for segment in segments), "untimed_count": untimed_count}


def _display(value) -> str:
    number = _number(value)
    if number is not None and (abs(number) >= 1_000_000 or 0 < abs(number) < 0.001):
        return f"{number:.3g}"
    return _format(value)


def _overlay(baseline: list[dict], candidate: list[dict], metric: str) -> dict:
    """Draw independent traces on shared axes, preserving their spacing and gaps."""
    series = {"baseline": _elapsed_trace(baseline, metric), "candidate": _elapsed_trace(candidate, metric)}
    points = [point for trace in series.values() for segment in trace["segments"] for point in segment]
    chart = {"series": series, "elements": [], "point_count": len(points)}
    if not points:
        return chart
    times = [value for trace in series.values() for value in trace["times"]]
    x_scale = max(1.0, *(abs(value) for value in times))
    x_low, x_high = min(value / x_scale for value in times), max(value / x_scale for value in times)
    if x_low == x_high:
        x_low, x_high = x_low - 0.5, x_high + 0.5
    values = [point["value"] for point in points]
    scale = max(1.0, *(abs(value) for value in values))
    low, high = min(value / scale for value in values), max(value / scale for value in values)
    padding = (high - low) * 0.1 if high != low else max(abs(low) * 0.01, 1.0 / scale)
    finite_limit = sys.float_info.max / scale
    low, high = max(low - padding, -finite_limit), min(high + padding, finite_limit)
    right, bottom = CHART_WIDTH - CHART_RIGHT, CHART_HEIGHT - CHART_BOTTOM

    def coordinate(x, value):
        return (CHART_LEFT + (x / x_scale - x_low) / (x_high - x_low) * (right - CHART_LEFT),
                bottom - (value / scale - low) / (high - low) * (bottom - CHART_TOP))

    elements = chart["elements"]
    for tick in range(5):
        fraction = tick / 4
        value = (low + fraction * (high - low)) * scale
        y = bottom - fraction * (bottom - CHART_TOP)
        elements.extend([
            {"tag": "line", "attrs": {"x1": CHART_LEFT, "x2": right, "y1": y, "y2": y, "class": "grid"}},
            {"tag": "text", "attrs": {"x": CHART_LEFT - 12, "y": y + 4, "text-anchor": "end",
                                      "class": "tick"}, "text": _display(value)},
        ])
    for tick in range(5):
        value = (x_low + tick / 4 * (x_high - x_low)) * x_scale
        x, _ = coordinate(value, values[0])
        elements.append({"tag": "text", "attrs": {"x": x, "y": bottom + 25,
                                                   "text-anchor": "middle", "class": "tick"},
                         "text": _display(value)})
    for run, trace in series.items():
        for segment in trace["segments"]:
            if len(segment) > 1:
                coords = [coordinate(point["x"], point["value"]) for point in segment]
                path = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in coords)
                elements.append({"tag": "path", "attrs": {"d": path, "class": "trace " + run}})
            for point in segment:
                x, y = coordinate(point["x"], point["value"])
                elements.append({"tag": "circle", "attrs": {"cx": x, "cy": y, "r": 4,
                                                           "class": "point " + run},
                                 "title": f'{point["time_utc"]}: {_display(point["value"])}'})
    return chart


def _label(data: dict, fallback: str) -> str:
    value = data.get("session", {}).get("label")
    return str(value) if value else fallback


def _comparison_note(note: str, labels: dict) -> str | None:
    if note in ("different_boards", "unknown_board_id"):
        return None  # The board-match banner already states these facts.
    for run in RUNS:
        messages = {f"{run}_no_valid_samples": "正常な測定値がありません。",
                    f"{run}_unfinished": "記録の完了を確認できません。",
                    f"{run}_response_timeout": "応答のタイムアウトで記録を終了しました。"}
        if note in messages:
            return labels[run] + ": " + messages[note]
    if note.startswith("nonfinite_difference:"):
        key = note.partition(":")[2]
        return str(METRICS.get(key, {}).get("label", key)) + ": 平均差を有限の数値で表せません。"
    return note


def _run_metadata(data: dict, path: Path, output: Path, label: str, run: str) -> str:
    session = data.get("session", {})
    notes = session.get("notes", [])
    if not isinstance(notes, list):
        notes = [notes]
    note_text = "".join("<p>" + html.escape(str(note)) + "</p>" for note in notes)
    summary = data["summary"]
    quality = (f'記録 {summary["sample_count"]} / 正常 {summary["valid_count"]} / '
               f'欠測または失敗 {summary["failed_count"]}')
    basis = summary.get("timing", {}).get("timestamp_basis")
    basis_label = {"host_query_start": "ホストでの照会開始", "legacy_host_record": "ホストでの記録時刻"}.get(
        basis, "未確認")
    timing = summary.get("timing", {})
    rate = _display(timing.get("effective_hz"))
    response_mean = _display(timing.get("response_ms", {}).get("mean"))
    raw_link = "./" + quote(os.path.relpath(path.resolve(), output.parent.resolve()), safe="/")
    details = {"session": session, "end": data.get("end"), "capture_errors": data.get("capture_errors", [])}
    metadata = html.escape(json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False))
    return (f'<section class="card metadata {run}"><h2>{html.escape(label)}</h2>'
            f'<p class="quality">{html.escape(quality)}</p><p>時刻の基準: {html.escape(basis_label)}</p>'
            f'<div class="timing"><p>観測した記録頻度<br><strong>{rate} Hz</strong></p>'
            f'<p>平均照会処理時間<br><strong>{response_mean} ms</strong></p></div>{note_text}'
            f'<p class="source">{html.escape(str(data.get("source", path)))}</p>'
            f'<p><a href="{html.escape(raw_link, quote=True)}">元の JSONL 記録を開く</a></p>'
            + _capture_notice(data)
            + '<details><summary>接続情報と記録の終了状態</summary><pre>' + metadata + '</pre></details></section>')


def _render(comparison: dict, baseline_path: Path, candidate_path: Path, output: Path) -> str:
    baseline, candidate = comparison["baseline"], comparison["candidate"]
    labels = {"baseline": _label(baseline, "基準記録"), "candidate": _label(candidate, "比較記録")}
    metrics = [{"key": key, **definition} for key, definition in METRICS.items()]
    charts = {metric["key"]: _overlay(baseline["samples"], candidate["samples"], metric["key"])
              for metric in metrics}
    first_metric = metrics[0]
    first_chart = charts[first_metric["key"]]
    stats = comparison["metrics"][first_metric["key"]]
    options = "".join(f'<option value="{html.escape(metric["key"])}">{html.escape(str(metric["label"]))}'
                      f' ({html.escape(str(metric["unit"]))})</option>' for metric in metrics)
    notes = [_comparison_note(str(note), labels) for note in comparison.get("notes", [])]
    notes = [note for note in notes if note is not None]
    for run in RUNS:
        missing_time = first_chart["series"][run]["untimed_count"]
        if missing_time:
            notes.append(f'{labels[run]}: 時刻の欠落または順序の不整合がある {missing_time} 件はグラフに表示しません。')
    note_html = "".join("<p>" + html.escape(note) + "</p>" for note in notes)
    metadata = _run_metadata(baseline, baseline_path, output, labels["baseline"], "baseline")
    metadata += _run_metadata(candidate, candidate_path, output, labels["candidate"], "candidate")
    payload = {"comparison": comparison, "labels": labels, "metrics": metrics, "charts": charts}
    replacements = {"OPTIONS": options, "BASELINE_LABEL": html.escape(labels["baseline"]),
                    "CANDIDATE_LABEL": html.escape(labels["candidate"]),
                    "BASELINE_MEAN": _display(stats["baseline_mean"]), "CANDIDATE_MEAN": _display(stats["candidate_mean"]),
                    "BASELINE_STDEV": _display(baseline["summary"]["metrics"][first_metric["key"]].get("stdev")),
                    "CANDIDATE_STDEV": _display(candidate["summary"]["metrics"][first_metric["key"]].get("stdev")),
                    "MEAN_DELTA": _display(stats["mean_delta"]),
                    "BASELINE_COUNT": str(stats["baseline_count"]), "CANDIDATE_COUNT": str(stats["candidate_count"]),
                    "UNIT": html.escape(str(first_metric["unit"])),
                    "BOARD_MATCH": html.escape(BOARD_LABELS.get(comparison["board_match"], BOARD_LABELS["unknown"])),
                    "NOTES": note_html, "METADATA": metadata, "JSON": _embedded_json(payload),
                    "SVG": _svg_elements(first_chart["elements"]),
                    "EMPTY_HIDDEN": " hidden" if first_chart["point_count"] else "",
                    "CHART_HIDDEN": "" if first_chart["point_count"] else " hidden"}
    return re.sub(r"@@([A-Z_]+)@@", lambda match: replacements[match.group(1)], _HTML)


def write_comparison_report(baseline: Path, candidate: Path, output: Path,
                            *, comparison: dict | None = None) -> None:
    """Create an offline report, optionally using an already validated snapshot."""
    baseline, candidate, output = Path(baseline), Path(candidate), Path(output)
    if comparison is None:
        comparison = compare_captures(baseline, candidate)
    document = _render(comparison, baseline, candidate, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(document)


_HTML = '''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlatSat センサ記録の比較</title><style>
:root{color-scheme:light;--ink:#182d34;--muted:#52666e;--line:#dbe4e7;--baseline:#14786a;--candidate:#7652ab}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:#f4f7f8;color:var(--ink);font:15px/1.7 system-ui,sans-serif}
main{max-width:1160px;margin:auto;padding:36px 28px 60px}.eyebrow{color:var(--baseline);font-size:12px;font-weight:700;letter-spacing:.15em}
h1{font-size:30px;line-height:1.3;margin:8px 0}h2{font-size:18px;margin:0}p{margin:8px 0}.muted,.hint{color:var(--muted)}
.card{background:white;border:1px solid var(--line);border-radius:14px;padding:20px 24px}.chart-card{margin-top:24px}.card-title{display:flex;align-items:center;justify-content:space-between;gap:20px}
label{font-size:13px;color:var(--muted)}select{font:inherit;padding:7px 12px;margin-left:8px;border:1px solid #c7d5da;border-radius:8px;background:white;color:var(--ink)}
.means{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}.mean{border-radius:10px;background:#f4f7f8;padding:14px 18px}.mean.baseline{border-top:3px solid var(--baseline)}.mean.candidate{border-top:3px solid var(--candidate)}
.mean-label{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.number{font-size:27px;font-weight:650;line-height:1.5}.unit{font-size:12px;margin-left:6px;font-weight:400;color:var(--muted)}.count{font-size:12px;color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:20px;font-size:13px}.legend span{display:flex;align-items:center;gap:8px}.swatch{display:inline-block;width:25px;border-top:3px solid var(--baseline)}.swatch.candidate{border-color:var(--candidate);border-top-style:dashed}
svg{display:block;width:100%;height:auto;min-height:190px}.grid{stroke:#e4eaed;stroke-width:1}.tick{fill:#52666e;font:12px system-ui,sans-serif}.trace{fill:none;stroke-width:2.4}.trace.baseline{stroke:var(--baseline)}.trace.candidate{stroke:var(--candidate);stroke-dasharray:7 4}
.point{stroke:white;stroke-width:1.5}.point.baseline{fill:var(--baseline)}.point.candidate{fill:var(--candidate)}.axis-label{text-align:center;color:var(--muted);font-size:12px;margin:0}.hint{font-size:12px;margin-top:10px}
.empty{padding:28px;background:#f4f7f8;text-align:center;border-radius:8px;color:var(--muted)}.board{margin-top:20px;font-size:13px}.board strong{font-weight:650}.board p{font-size:12px;color:var(--muted)}
.runs{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px}.metadata{font-size:12px;min-width:0}.metadata h2{overflow-wrap:anywhere}.metadata.baseline{border-top:3px solid var(--baseline)}.metadata.candidate{border-top:3px solid var(--candidate)}
.source{overflow-wrap:anywhere;color:var(--muted)}.quality{font-weight:600}.metadata a{color:var(--baseline)}details{margin-top:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f8;font-size:11px;padding:12px;border-radius:8px}.notice{background:#eef4f5;padding:12px;margin-top:16px}.notice h2{font-size:14px}
.timing{display:grid;grid-template-columns:1fr 1fr;gap:16px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:12px 0;padding:5px 0}.timing strong{font-size:17px;font-weight:600}.spread{font-size:12px;color:var(--muted);margin-top:6px}
@media(max-width:640px){main{padding:24px 16px}h1{font-size:25px}.card{padding:16px}.card-title{display:block}.card-title label{display:block;margin-top:12px}select{margin-left:0;max-width:100%}.means{gap:8px}.mean{padding:10px}.number{font-size:22px}.unit{display:block;margin-left:0}.runs{grid-template-columns:1fr}}
</style></head><body><main><header><div class="eyebrow">FLATSAT / CAPTURE COMPARISON</div><h1>センサ記録の比較</h1><p class="muted">2つの記録の推移と、正常な応答の平均を比較できます。</p></header>
<section class="card chart-card"><div class="card-title"><h2>測定値の推移と平均差</h2><label for="metric">測定項目 <select id="metric">@@OPTIONS@@</select></label></div>
<div class="means"><div class="mean baseline"><div class="mean-label">基準: @@BASELINE_LABEL@@</div><div class="number"><span id="baseline-mean">@@BASELINE_MEAN@@</span><span class="unit">@@UNIT@@</span></div><div class="count">正常な測定 <span id="baseline-count">@@BASELINE_COUNT@@</span> 件</div><div class="spread">標本標準偏差 <span id="baseline-stdev">@@BASELINE_STDEV@@</span> <span class="unit">@@UNIT@@</span></div></div>
<div class="mean candidate"><div class="mean-label">比較: @@CANDIDATE_LABEL@@</div><div class="number"><span id="candidate-mean">@@CANDIDATE_MEAN@@</span><span class="unit">@@UNIT@@</span></div><div class="count">正常な測定 <span id="candidate-count">@@CANDIDATE_COUNT@@</span> 件</div><div class="spread">標本標準偏差 <span id="candidate-stdev">@@CANDIDATE_STDEV@@</span> <span class="unit">@@UNIT@@</span></div></div>
<div class="mean"><div class="mean-label">平均差 (比較 − 基準)</div><div class="number"><span id="mean-delta">@@MEAN_DELTA@@</span><span class="unit">@@UNIT@@</span></div><div class="count">両方に正常な測定がある場合に計算</div></div></div>
<div class="legend"><span><i class="swatch"></i>基準: @@BASELINE_LABEL@@</span><span><i class="swatch candidate"></i>比較: @@CANDIDATE_LABEL@@</span></div>
<p id="empty" class="empty"@@EMPTY_HIDDEN@@>両方の記録に正常な測定値がありません。</p><svg id="chart" viewBox="0 0 960 320" role="img" aria-label="基準と比較の測定値の推移"@@CHART_HIDDEN@@>@@SVG@@</svg>
<p class="axis-label">各記録の最初のサンプルからの経過時間 (秒)</p><p class="hint">時刻はホスト側で記録した値を基準にします。各記録の間隔を保ち、欠測や失敗の前後では線をつなぎません。</p></section>
<section class="card board"><strong>@@BOARD_MATCH@@</strong>@@NOTES@@</section><div class="runs">@@METADATA@@</div>
</main><script id="comparison-data" type="application/json">@@JSON@@</script><script>
"use strict";
const report = JSON.parse(document.getElementById("comparison-data").textContent);
const selector = document.getElementById("metric");
const svgNamespace = "http://www.w3.org/2000/svg";
const format = value => typeof value !== "number" || !Number.isFinite(value) ? "—" :
  Math.abs(value) >= 1000000 || Math.abs(value) > 0 && Math.abs(value) < 0.001 ? value.toExponential(3) :
  value.toLocaleString("ja-JP", {maximumFractionDigits:3});
function drawMetric() {
  const key = selector.value;
  const metric = report.metrics.find(item => item.key === key);
  const chart = report.charts[key];
  const stats = report.comparison.metrics[key];
  document.getElementById("baseline-mean").textContent = format(stats.baseline_mean);
  document.getElementById("candidate-mean").textContent = format(stats.candidate_mean);
  document.getElementById("mean-delta").textContent = format(stats.mean_delta);
  document.getElementById("baseline-count").textContent = stats.baseline_count;
  document.getElementById("candidate-count").textContent = stats.candidate_count;
  document.getElementById("baseline-stdev").textContent = format(report.comparison.baseline.summary.metrics[key].stdev);
  document.getElementById("candidate-stdev").textContent = format(report.comparison.candidate.summary.metrics[key].stdev);
  document.querySelectorAll(".unit").forEach(element => {element.textContent = metric.unit;});
  document.getElementById("empty").hidden = chart.point_count > 0;
  const svg = document.getElementById("chart");
  if (chart.point_count) svg.removeAttribute("hidden"); else svg.setAttribute("hidden", "");
  svg.setAttribute("aria-label", metric.label + "の基準と比較の推移");
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  for (const item of chart.elements) {
    const element = document.createElementNS(svgNamespace, item.tag);
    for (const [name, value] of Object.entries(item.attrs)) element.setAttribute(name, value);
    if (item.text !== undefined) element.textContent = item.text;
    if (item.title !== undefined) {
      const title = document.createElementNS(svgNamespace, "title");
      title.textContent = item.title;
      element.appendChild(title);
    }
    svg.appendChild(element);
  }
}
selector.addEventListener("change", drawMetric);
drawMetric();
</script></body></html>'''
