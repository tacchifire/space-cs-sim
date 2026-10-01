"""Standalone, offline HTML reports for recorded FlatSat sensor samples."""
from __future__ import annotations

import html
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import quote

from .alerts import evaluate_capture
from .telemetry import METRICS, load_capture

CHART_WIDTH = 960
CHART_HEIGHT = 320
CHART_LEFT = 90
CHART_RIGHT = 30
CHART_TOP = 35
CHART_BOTTOM = 55
STATUS_LABELS = {"ok": "正常", "partial": "一部欠測", "invalid": "無効", "timeout": "応答なし"}


def _number(value):
    """Only finite measured numbers are eligible for a chart or numeric label."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _format(value) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:,.3f}".rstrip("0").rstrip(".")


def _embedded_json(value) -> str:
    # JSON script elements still terminate at a literal </script>. Escape HTML
    # delimiters as Unicode escapes rather than HTML entities, which break JSON.
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    for original, replacement in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                                  ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(original, replacement)
    return text


def _chart(samples: list[dict], metric: str) -> dict:
    """Split the trace at every absent or rejected observation before drawing."""
    elapsed = [_number(sample.get("elapsed_s")) for sample in samples]
    use_elapsed = bool(samples) and all(value is not None for value in elapsed)
    x_values = elapsed if use_elapsed else list(range(len(samples)))
    segments, current = [], []
    for sample, x in zip(samples, x_values):
        value = _number(sample.get("values", {}).get(metric))
        if value is None or sample.get("status") != "ok":
            if current:
                segments.append(current)
                current = []
            continue
        current.append({"x": x, "value": value, "index": sample.get("index"),
                        "time_utc": sample.get("time_utc"), "status": sample.get("status")})
    if current:
        segments.append(current)
    points = [point for segment in segments for point in segment]
    chart = {"segments": segments, "elements": [], "point_count": len(points),
             "x_label": "経過時間 (秒)" if use_elapsed else "記録順"}
    if not points:
        return chart
    x_min, x_max = min(x_values), max(x_values)
    if x_min == x_max:
        x_min, x_max = x_min - 0.5, x_max + 0.5
    # Normalize before subtraction: edited but finite values must not create
    # an overflowing axis range or non-finite SVG coordinates.
    scale = max(1.0, *(abs(point["value"]) for point in points))
    y_min, y_max = min(point["value"] / scale for point in points), max(point["value"] / scale for point in points)
    padding = (y_max - y_min) * 0.1 if y_max != y_min else max(abs(y_min) * 0.01, 1.0 / scale)
    y_min, y_max = y_min - padding, y_max + padding
    right, bottom = CHART_WIDTH - CHART_RIGHT, CHART_HEIGHT - CHART_BOTTOM

    def coordinate(x, y):
        x_fraction = (x - x_min) / (x_max - x_min) if x_max != x_min else 0.5
        return (CHART_LEFT + x_fraction * (right - CHART_LEFT),
                bottom - (y / scale - y_min) / (y_max - y_min) * (bottom - CHART_TOP))

    elements = chart["elements"]
    for tick in range(5):
        fraction = tick / 4
        value = y_min + fraction * (y_max - y_min)
        y = bottom - fraction * (bottom - CHART_TOP)
        elements.extend([
            {"tag": "line", "attrs": {"x1": CHART_LEFT, "x2": right, "y1": y, "y2": y,
                                      "class": "grid"}},
            {"tag": "text", "attrs": {"x": CHART_LEFT - 12, "y": y + 4, "text-anchor": "end",
                                      "class": "tick"}, "text": _format(value * scale)},
        ])
    for tick in range(5):
        value = x_min + tick / 4 * (x_max - x_min)
        x, _ = coordinate(value, points[0]["value"])
        elements.append({"tag": "text", "attrs": {"x": x, "y": bottom + 25,
                                                  "text-anchor": "middle", "class": "tick"},
                         "text": _format(value)})
    for segment in segments:
        if len(segment) > 1:
            coords = [coordinate(point["x"], point["value"]) for point in segment]
            path = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in coords)
            elements.append({"tag": "path", "attrs": {"d": path, "class": "trace"}})
        for point in segment:
            x, y = coordinate(point["x"], point["value"])
            elements.append({"tag": "circle", "attrs": {"cx": x, "cy": y, "r": 4,
                                                       "class": "point " + point["status"]},
                             "title": f'{point["time_utc"]}: {_format(point["value"])}'})
    return chart


def _svg_elements(elements: list[dict]) -> str:
    rendered = []
    for element in elements:
        tag = element["tag"]  # All tags and attribute names originate in _chart.
        attrs = " ".join(f'{key}="{html.escape(str(value), quote=True)}"'
                         for key, value in element["attrs"].items())
        text = html.escape(str(element.get("text", "")))
        if "title" in element:
            text += "<title>" + html.escape(element["title"]) + "</title>"
        rendered.append(f"<{tag} {attrs}>{text}</{tag}>")
    return "".join(rendered)


def _notes(sample: dict) -> str:
    notes = [str(error) for error in sample.get("errors", [])]
    missing = sample.get("missing_fields", [])
    if missing:
        notes.append("欠測: " + ", ".join(str(METRICS.get(key, {}).get("label", key)) for key in missing))
    return " / ".join(notes) or "—"


def _capture_notice(data: dict) -> str:
    messages = []
    end = data.get("end")
    if isinstance(end, dict) and end.get("reason"):
        reason = str(end["reason"])
        labels = {"duration_complete": "指定時間の記録が完了しました。",
                  "response_timeout": "応答がタイムアウトしたため記録を終了しました。",
                  "interrupted": "記録を中断しました。", "user_interrupt": "記録を中断しました。",
                  "device_error": "デバイスのエラーで記録を終了しました。"}
        messages.append(labels.get(reason, "終了理由: " + str(reason)))
    elif end is None and (data.get("samples") or data.get("session")):
        messages.append("終了イベントが記録されていません。")
    for error in data.get("capture_errors", []):
        if isinstance(error, dict):
            messages.append(str(error.get("message", "記録中のエラー")))
    if not messages:
        return ""
    paragraphs = "".join("<p>" + html.escape(message) + "</p>" for message in messages)
    return '<section class="card notice"><h2>記録の終了状態</h2>' + paragraphs + "</section>"


def _session_context(session: dict) -> str:
    label, notes = session.get("label"), session.get("notes", [])
    if not isinstance(notes, list):
        notes = [notes]
    if not label and not notes:
        return ""
    fields = ([str(label)] if label else []) + [str(note) for note in notes]
    return ('<section class="card notice"><h2>実験条件</h2>'
            + "".join("<p>" + html.escape(field) + "</p>" for field in fields) + "</section>")


def _alert_context(analysis: dict | None) -> str:
    if analysis is None:
        return ""
    states = {"normal": "範囲内", "above": "上限超過", "below": "下限未満",
              "unknown": "判定不能", None: "未観測"}
    transitions = {"triggered": "逸脱", "recovered": "範囲内へ回復",
                   "unavailable": "判定不能", "resumed": "判定再開（範囲内）"}

    def row(fields):
        return "<tr>" + "".join("<td>" + html.escape(str(field)) + "</td>" for field in fields) + "</tr>"

    limits = "".join(row([METRICS[item["metric"]]["label"],
                         "制限なし" if item["minimum"] is None else repr(item["minimum"]),
                         "制限なし" if item["maximum"] is None else repr(item["maximum"]),
                         METRICS[item["metric"]]["unit"],
                         states[analysis["final_states"][item["metric"]]]])
                     for item in analysis["limits"])
    events = "".join(row([event["index"], event.get("time_utc") or "未記録",
                         _format(event.get("elapsed_s")), METRICS[event["metric"]]["label"],
                         transitions[event["transition"]],
                         "—" if event["value"] is None else repr(event["value"]),
                         states[event["state"]]]) for event in analysis["events"])
    return ('<section class="card table-card" id="limit-events"><h2>しきい値とイベント</h2>'
            f'<p>逸脱への遷移: {analysis["trigger_count"]}回 / '
            f'回復: {analysis["recovery_count"]}回 / 判定不能への遷移: {analysis["unavailable_count"]}回</p>'
            '<div class="table-wrap"><table><thead><tr><th>項目</th><th>下限</th><th>上限</th>'
            '<th>単位</th><th>最後のサンプルの判定</th></tr></thead><tbody>' + limits + '</tbody></table></div>'
            '<p class="hint">境界値を含む範囲で判定し、状態が変わった時にイベントを記録します。'
            '欠測後に範囲内の値を観測した場合は「判定再開」と表示します。設定した範囲は実験の判定条件です。</p>'
            '<div class="table-wrap"><table><thead><tr><th>番号</th><th>照会開始 (UTC)</th><th>経過 (秒)</th>'
            '<th>項目</th><th>イベント</th><th>値</th><th>状態</th></tr></thead><tbody>' + events +
            '</tbody></table></div>' + ('<p>状態の変化はありません。</p>' if not events else '') + '</section>')


def _render(data: dict, capture_path: Path, output_path: Path) -> str:
    samples, summary, session = data["samples"], data["summary"], data["session"]
    metrics = [{"key": key, **definition} for key, definition in METRICS.items()]
    first_metric = metrics[0]
    charts = {metric["key"]: _chart(samples, metric["key"]) for metric in metrics}
    first_chart = charts[first_metric["key"]]
    metric_summary = summary.get("metrics", {}).get(first_metric["key"], {})
    options = "".join(f'<option value="{html.escape(metric["key"])}">'
                      f'{html.escape(str(metric["label"]))} ({html.escape(str(metric["unit"]))})</option>'
                      for metric in metrics)
    rows = []
    for sample in samples:
        status = sample.get("status", "invalid")
        status_class = status if status in STATUS_LABELS else "invalid"
        fields = [str(sample.get("index", "")), str(sample.get("time_utc", "")),
                  _format(sample.get("elapsed_s")), _format(sample.get("response_ms")),
                  _format(sample.get("values", {}).get(first_metric["key"])),
                  STATUS_LABELS.get(status, str(status)), _notes(sample)]
        cells = "".join(f'<td{attribute}>{html.escape(field)}</td>' for field, attribute in zip(fields,
                        ["", "", "", "", ' class="measurement"', f' class="quality {status_class}"', ""]))
        rows.append("<tr>" + cells + "</tr>")
    raw_link = "./" + quote(os.path.relpath(capture_path.resolve(), output_path.parent.resolve()), safe="/")
    ports = session.get("ports", [])
    if not isinstance(ports, list):
        ports = []
    boards = ", ".join(dict.fromkeys(str(port.get("serial_number") or port.get("board_id") or "未記録")
                                    for port in ports if isinstance(port, dict))) or "未記録"
    source = html.escape(str(data.get("source", capture_path)))
    metadata = html.escape(json.dumps(session, ensure_ascii=False, indent=2, allow_nan=False))
    payload = {"data": data, "metrics": metrics, "charts": charts}
    empty = "この記録にはセンサ測定がありません。" if not samples else "選択した項目の有効な測定値がありません。"
    empty_hidden = " hidden" if first_chart["point_count"] else ""
    replacements = {"OPTIONS": options, "ROWS": "".join(rows), "TOTAL": str(summary["sample_count"]),
                    "VALID": str(summary["valid_count"]), "FAILED": str(summary["failed_count"]),
                    "SOURCE": source, "BOARD": html.escape(boards), "RAW_LINK": html.escape(raw_link, quote=True),
                    "METADATA": metadata, "JSON": _embedded_json(payload),
                    "SVG": _svg_elements(first_chart["elements"]), "EMPTY": empty,
                    "EMPTY_HIDDEN": empty_hidden, "XLABEL": first_chart["x_label"],
                    "MIN": _format(metric_summary.get("min")), "MAX": _format(metric_summary.get("max")),
                    "MEAN": _format(metric_summary.get("mean")), "COUNT": str(first_chart["point_count"]),
                    "STDEV": _format(metric_summary.get("stdev")),
                    "RATE": _format(summary.get("timing", {}).get("effective_hz")),
                    "CONTEXT": _session_context(session),
                    "ALERTS": _alert_context(data.get("alerts")),
                    "UNIT": html.escape(str(first_metric["unit"])), "NOTICE": _capture_notice(data),
                    "CHART_HIDDEN": "" if first_chart["point_count"] else " hidden"}
    # Substitute once: a log string that happens to contain a placeholder must
    # never be interpreted as part of the report template.
    return re.sub(r"@@([A-Z_]+)@@", lambda match: replacements[match.group(1)], _HTML)


def write_report(capture_path: Path, output_path: Path, *, limits=None) -> None:
    """Read a saved capture and create a self-contained report without USB IO."""
    capture_path, output_path = Path(capture_path), Path(output_path)
    data = load_capture(capture_path)
    if limits is not None or data["session"].get("limits"):
        data["alerts"] = evaluate_capture(data, limits)
    document = _render(data, capture_path, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as output:
        output.write(document)


_HTML = '''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlatSat センサ記録</title>
<style>
:root{color-scheme:light;--ink:#182d34;--muted:#52666e;--line:#dbe4e7;--green:#14786a;--amber:#a96514}
*{box-sizing:border-box}body{margin:0;background:#f4f7f8;color:var(--ink);font:15px/1.7 system-ui,sans-serif}
main{max-width:1160px;margin:auto;padding:40px 28px 60px}header{display:flex;justify-content:space-between;gap:24px;align-items:center}
.eyebrow{color:var(--green);font-size:12px;letter-spacing:.15em;font-weight:700}h1{font-size:30px;line-height:1.3;margin:8px 0}
h2{font-size:18px;margin:0}p{margin:8px 0}.muted,.hint{color:var(--muted)}.badge{padding:7px 12px;border-radius:20px;background:#e1efeb;color:#216b60;white-space:nowrap;font-size:12px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}.card{background:white;border:1px solid var(--line);border-radius:14px;padding:20px 24px}
.stat-label{color:var(--muted);font-size:13px}.stat-value{font-size:30px;font-weight:650;line-height:1.4}.card-title{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:12px}
.notice{margin-bottom:20px;background:#eef4f5;font-size:13px}[hidden]{display:none!important}
label{font-size:13px;color:var(--muted)}select{font:inherit;background:#fff;color:var(--ink);border:1px solid #c7d5da;border-radius:8px;padding:7px 12px;margin-left:8px;max-width:100%}
.metric-stats{display:flex;flex-wrap:wrap;gap:26px;font-size:13px;margin:12px 0 4px}.metric-stats b{font-weight:650;color:var(--ink);margin-left:5px}
svg{display:block;width:100%;height:auto;min-height:190px}.grid{stroke:#e4eaed;stroke-width:1}.tick{fill:#52666e;font:12px system-ui,sans-serif}.trace{fill:none;stroke:var(--green);stroke-width:2.4}
.point{fill:var(--green);stroke:white;stroke-width:1.5}.axis-label{text-align:center;font-size:12px;color:var(--muted);margin:0}
.empty{margin:20px 0;padding:25px;background:#f4f7f8;border-radius:8px;text-align:center;color:var(--muted)}.hint{font-size:12px;margin-top:10px}
.table-card{margin-top:20px}.table-wrap{overflow:auto;max-height:460px}table{border-collapse:collapse;width:100%;font-size:12px}th{text-align:left;color:var(--muted);font-weight:600;position:sticky;top:0;background:white}
td,th{padding:11px 12px;border-bottom:1px solid #edf0f2;white-space:nowrap}td:last-child{white-space:normal;min-width:160px}.quality.ok{color:var(--green)}.quality.partial{color:var(--amber)}.quality.invalid,.quality.timeout{color:#a33e32}
.metadata{margin-top:20px;display:grid;grid-template-columns:1fr 1fr;gap:20px;font-size:12px}.metadata p{overflow-wrap:anywhere}.metadata a{color:var(--green)}details{margin-top:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;background:#f4f7f8;padding:14px;border-radius:8px}
@media(max-width:640px){main{padding:24px 16px}header{display:block}.badge{display:inline-block;margin-top:10px}.stats{gap:8px}.card{padding:16px}.stat-value{font-size:24px}.card-title{display:block}.card-title label{display:block;margin-top:12px}select{margin-left:0}.metadata{grid-template-columns:1fr}h1{font-size:26px}}
</style></head><body><main>
<header><div><div class="eyebrow">FLATSAT / CAPTURE REPORT</div><h1>センサ記録</h1><p class="muted">保存した測定値の推移と、応答の状態を確認できます。</p></div><span class="badge">保存済みログから生成</span></header>
@@CONTEXT@@
<section class="stats" aria-label="記録の品質"><div class="card"><div class="stat-label">記録したサンプル</div><div class="stat-value">@@TOTAL@@</div></div><div class="card"><div class="stat-label">全項目が正常</div><div class="stat-value">@@VALID@@</div></div><div class="card"><div class="stat-label">欠測または失敗</div><div class="stat-value">@@FAILED@@</div></div></section>
@@NOTICE@@
<section class="card"><div class="card-title"><h2>測定値の推移</h2><label for="metric">測定項目 <select id="metric">@@OPTIONS@@</select></label></div>
<div class="metric-stats"><span>最小 <b id="minimum">@@MIN@@</b></span><span>最大 <b id="maximum">@@MAX@@</b></span><span>平均 <b id="mean">@@MEAN@@</b></span><span>ばらつき（標準偏差） <b id="stdev">@@STDEV@@</b></span><span>単位 <b id="unit">@@UNIT@@</b></span><span>有効点 <b id="point-count">@@COUNT@@</b></span></div>
<p class="hint">ホストで記録した周期: @@RATE@@ Hz。センサ内部の計測周期を示す値ではありません。</p>
<p id="empty" class="empty"@@EMPTY_HIDDEN@@>@@EMPTY@@</p><svg id="chart" viewBox="0 0 960 320" role="img" aria-label="選択した測定項目の時系列グラフ"@@CHART_HIDDEN@@>@@SVG@@</svg><p class="axis-label" id="x-label">@@XLABEL@@</p>
<p class="hint">グラフと統計には全項目が正常な応答を使い、欠測や失敗の前後では線をつなぎません。時刻はホストでの照会開始時刻です。</p></section>
@@ALERTS@@
<section class="card table-card"><div class="card-title"><h2>サンプル一覧</h2><span class="hint">照会処理時間はホスト側で測り、応答形式によって待機時間を含みます</span></div><div class="table-wrap"><table><thead><tr><th>番号</th><th>照会開始 (UTC)</th><th>経過 (秒)</th><th>照会処理 (ms)</th><th id="value-heading">測定値</th><th>状態</th><th>補足</th></tr></thead><tbody>@@ROWS@@</tbody></table></div></section>
<section class="metadata"><div class="card"><h2>記録元</h2><p>@@SOURCE@@</p><p><a href="@@RAW_LINK@@">元の JSONL 記録を開く</a></p></div><div class="card"><h2>基板と接続</h2><p>基板 ID: @@BOARD@@</p><details><summary>記録された接続情報</summary><pre>@@METADATA@@</pre></details></div></section>
</main><script id="capture-data" type="application/json">@@JSON@@</script><script>
"use strict";
const report = JSON.parse(document.getElementById("capture-data").textContent);
const svgNamespace = "http://www.w3.org/2000/svg";
const selector = document.getElementById("metric");
const format = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ja-JP", {maximumFractionDigits:3}) : "—";
function drawMetric() {
  const key = selector.value;
  const metric = report.metrics.find(item => item.key === key);
  const chart = report.charts[key];
  const stats = report.data.summary.metrics[key] || {};
  document.getElementById("minimum").textContent = format(stats.min);
  document.getElementById("maximum").textContent = format(stats.max);
  document.getElementById("mean").textContent = format(stats.mean);
  document.getElementById("stdev").textContent = format(stats.stdev);
  document.getElementById("unit").textContent = metric.unit;
  document.getElementById("point-count").textContent = chart.point_count;
  document.getElementById("x-label").textContent = chart.x_label;
  document.getElementById("value-heading").textContent = metric.label + " (" + metric.unit + ")";
  const empty = document.getElementById("empty");
  empty.hidden = chart.point_count > 0;
  empty.textContent = report.data.samples.length ? "選択した項目の有効な測定値がありません。" : "この記録にはセンサ測定がありません。";
  const svg = document.getElementById("chart");
  if (chart.point_count) svg.removeAttribute("hidden"); else svg.setAttribute("hidden", "");
  svg.setAttribute("aria-label", metric.label + "の時系列グラフ");
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
  document.querySelectorAll(".measurement").forEach((cell, index) => {
    cell.textContent = format(report.data.samples[index].values[key]);
  });
}
selector.addEventListener("change", drawMetric);
drawMetric();
</script></body></html>'''
