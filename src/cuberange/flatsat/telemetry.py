"""Strict sensor parsing and offline aggregation of FlatSat shell captures."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

from .usb import FlatSatError

METRICS = {
    "temperature_c": {"label": "温度", "unit": "°C"},
    "pressure_pa": {"label": "気圧", "unit": "Pa"},
    "humidity_percent": {"label": "湿度", "unit": "%"},
    "accel_x_mg": {"label": "加速度 X", "unit": "mg"},
    "accel_y_mg": {"label": "加速度 Y", "unit": "mg"},
    "accel_z_mg": {"label": "加速度 Z", "unit": "mg"},
    "acceleration_norm_mg": {"label": "加速度の大きさ（重力を含む）", "unit": "mg"},
}
BASE_FIELDS = tuple(key for key in METRICS if key != "acceleration_norm_mg")
_NUMBER = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[+-]?(?:nan|inf(?:inity)?))"
_ROWS = {
    "Accel:": (re.compile(rf"Accel:\s*x={_NUMBER}\s+mg\s+y={_NUMBER}\s+mg\s+z={_NUMBER}\s+mg"),
               ("accel_x_mg", "accel_y_mg", "accel_z_mg")),
    "Temp:": (re.compile(rf"Temp:\s*{_NUMBER}\s+C"), ("temperature_c",)),
    "Press:": (re.compile(rf"Press:\s*{_NUMBER}\s+Pa"), ("pressure_pa",)),
    "Humid:": (re.compile(rf"Humid:\s*{_NUMBER}\s*%"), ("humidity_percent",)),
}


def _value_error(key: str, value: float) -> str | None:
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        return f"{key}: non-finite value"
    if key == "humidity_percent" and not 0 <= value <= 100:
        return f"{key}: outside 0..100 percent"
    if key == "pressure_pa" and value <= 0:
        return f"{key}: pressure must be positive"
    return None


def parse_sensor_response(text: str) -> dict:
    """Require the observed six values and units; missing data stays None."""
    values = dict.fromkeys(METRICS)
    errors = []
    seen = set()
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = line.strip()
        for label, (pattern, fields) in _ROWS.items():
            if not line.lower().startswith(label.lower()):
                continue
            if label in seen:
                errors.append(f"duplicate {label}")
                for key in fields:
                    values[key] = None
                break
            seen.add(label)
            match = pattern.fullmatch(line)
            if match is None:
                errors.append(f"{label} malformed value or unit")
                break
            for key, raw in zip(fields, match.groups()):
                value = float(raw)
                error = _value_error(key, value)
                if error:
                    errors.append(error)
                else:
                    values[key] = value
            break
    missing = [key for key in BASE_FIELDS if values[key] is None]
    if all(values[key] is not None for key in ("accel_x_mg", "accel_y_mg", "accel_z_mg")):
        values["acceleration_norm_mg"] = math.hypot(*(values[key] for key in (
            "accel_x_mg", "accel_y_mg", "accel_z_mg")))
        if not math.isfinite(values["acceleration_norm_mg"]):
            values["acceleration_norm_mg"] = None
            errors.append("acceleration_norm_mg: non-finite magnitude")
    return {"status": "invalid" if errors else "partial" if missing else "ok",
            "values": values, "missing_fields": missing, "errors": errors}


def _timestamp(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        text = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(text)
        # A capture without an offset has no declared timezone. Do not infer one
        # from whichever host happens to run the offline summary.
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _finite_number(value, *, nonnegative: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and (not nonnegative or value >= 0)
    except OverflowError:
        return False


def _validated_sample(record: dict) -> dict:
    """Validate imported values too, so edited captures cannot invent valid data."""
    sample = dict(record)
    source_values = sample.get("values")
    if not isinstance(source_values, dict):
        raise ValueError("sensor_sample.values must be an object")
    values = dict.fromkeys(METRICS)
    errors = sample.get("errors", [])
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise ValueError("sensor_sample.errors must be a list of strings")
    errors = list(errors)
    for key in BASE_FIELDS:
        value = source_values.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{key}: value must be numeric")
            continue
        error = _value_error(key, value)
        if error:
            errors.append(error)
        else:
            values[key] = float(value)
    missing = [key for key in BASE_FIELDS if values[key] is None]
    axes = [values[key] for key in ("accel_x_mg", "accel_y_mg", "accel_z_mg")]
    if all(value is not None for value in axes):
        values["acceleration_norm_mg"] = math.hypot(*axes)
        if not math.isfinite(values["acceleration_norm_mg"]):
            values["acceleration_norm_mg"] = None
            errors.append("acceleration_norm_mg: non-finite magnitude")
    status = sample.get("status", "partial")
    if errors:
        status = "invalid"
    elif missing and status == "ok":
        status = "partial"
    elif status not in ("ok", "partial", "invalid", "timeout"):
        raise ValueError("unknown sensor_sample status")
    if sample.get("completion") == "timeout":
        status = "timeout"
    sample.update(status=status, values=values, errors=errors, missing_fields=missing)
    for key in ("elapsed_s", "response_ms"):
        value = sample.get(key)
        if value is not None and not _finite_number(value, nonnegative=True):
            raise ValueError(f"{key} must be a nonnegative finite number")
    return sample


def _legacy_sample(record: dict, index: int) -> dict:
    text = record.get("text", "")
    if not isinstance(text, str):
        raise ValueError("query_result.text must be a string")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    echoes = [position for position, line in enumerate(lines) if line.strip() == "sensors"]
    useful = "\n".join(lines[echoes[-1] + 1:]) if echoes else text
    parsed = parse_sensor_response(useful)
    if "answered" in record:
        if not isinstance(record["answered"], bool):
            raise ValueError("query_result.answered must be a boolean")
        if not echoes:
            parsed["status"] = "invalid"
            parsed["errors"].append("missing complete sensors command echo; reply is not synchronized")
        if not record["answered"]:
            parsed["status"] = "invalid"
            parsed["errors"].append("recorded query was not answered")
    if "unknown command" in useful.lower():
        parsed["status"] = "invalid"
        parsed["errors"].append("shell rejected the sensors command")
    return _validated_sample({"index": index, "time_utc": record.get("time_utc"),
                              "elapsed_s": record.get("elapsed_s"),
                              "response_ms": record.get("response_ms"),
                              "completion": record.get("completion", "legacy"), **parsed})


def _basic_statistics(values: list[float]) -> dict:
    return {"count": len(values), "min": min(values) if values else None,
            "max": max(values) if values else None, "mean": mean(values) if values else None}


def _metric_statistics(values: list[float]) -> dict:
    """Use observation order for change and the n-1 convention for dispersion."""
    result = _basic_statistics(values)
    result.update(stdev=None, first=values[0] if values else None,
                  last=values[-1] if values else None, delta=None, span=None)
    unavailable = {}
    if not values:
        unavailable = {key: "no_values" for key in result if key != "count"}
    else:
        for key, value in (("delta", values[-1] - values[0]),
                           ("span", result["max"] - result["min"])):
            if _finite_number(value):
                result[key] = value
            else:
                unavailable[key] = "overflow"
        if len(values) < 2:
            unavailable["stdev"] = "fewer_than_two_values"
        else:
            try:
                deviation = stdev(values)
            except OverflowError:
                deviation = None
            if _finite_number(deviation):
                result["stdev"] = deviation
            else:
                unavailable["stdev"] = "overflow"
    result["unavailable_reasons"] = unavailable
    return result


def _timing_summary(samples: list[dict], session: dict, *, mixed_basis: bool = False) -> tuple[dict, bool]:
    """A cadence needs one complete, strictly ordered clock for every query."""
    elapsed = [sample.get("elapsed_s") for sample in samples]
    known_times = all(_finite_number(value, nonnegative=True) for value in elapsed)
    ordered = known_times and all(second > first for first, second in zip(elapsed, elapsed[1:]))
    irregular = bool(samples) and (not ordered or mixed_basis)
    span = elapsed[-1] - elapsed[0] if elapsed and ordered and not mixed_basis else None
    intervals = ([second - first for first, second in zip(elapsed, elapsed[1:])]
                 if ordered and not mixed_basis else [])
    frequency = (len(elapsed) - 1) / span if len(elapsed) >= 2 and span is not None and span > 0 else None
    if not _finite_number(frequency):
        frequency = None
    responses = [sample["response_ms"] for sample in samples
                 if sample.get("completion") in ("quiet", "complete", "legacy")
                 and sample["status"] != "timeout"
                 and _finite_number(sample.get("response_ms"), nonnegative=True)]
    expected = session.get("interval_s")
    return {"timestamp_basis": "host_query_start" if session.get("mode") == "watch" else "legacy_host_record",
            "elapsed_span_s": span, "intervals_s": _basic_statistics(intervals),
            "effective_hz": frequency, "response_ms": _basic_statistics(responses),
            "expected_interval_s": expected if _finite_number(expected) and expected > 0 else None}, irregular


def _summary(samples: list[dict], session: dict, end: dict | None,
             capture_errors: list[dict], *, mixed_basis: bool = False) -> dict:
    valid = [sample for sample in samples if sample["status"] == "ok"]
    timing, irregular = _timing_summary(samples, session, mixed_basis=mixed_basis)
    reason = end.get("reason") if end else None
    unknown_end_reason = bool(end) and reason is not None and reason not in (
        "duration_complete", "response_timeout", "user_interrupt", "interrupted", "device_error")
    if capture_errors or reason in ("response_timeout", "user_interrupt", "interrupted", "device_error"):
        completion = "interrupted"
    else:
        completion = "complete" if end and not unknown_end_reason else "unknown"
    notes = []
    if not samples:
        notes.append("no_samples")
    elif not valid:
        notes.append("no_valid_samples")
    if irregular:
        notes.append("irregular_time")
    if completion != "complete":
        notes.append("unfinished")
    if unknown_end_reason:
        notes.append("unknown_end_reason")
    if capture_errors:
        notes.append("capture_errors")
    return {"sample_count": len(samples), "valid_count": len(valid),
            "failed_count": len(samples) - len(valid),
            "metrics": {key: _metric_statistics([sample["values"][key] for sample in valid])
                        for key in METRICS},
            "timing": timing,
            "quality": {"completion": completion,
                        "status_counts": {status: sum(sample["status"] == status for sample in samples)
                                          for status in ("ok", "partial", "invalid", "timeout")},
                        "notes": notes}}


def load_capture(path: Path) -> dict:
    """Read JSONL without USB, retaining failed samples separately from statistics."""
    path = Path(path)
    session = {}
    samples = []
    end = None
    capture_errors = []
    first_time = None
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("event"), str):
                    raise ValueError("capture record requires an event string")
                if record["event"] == "session":
                    session = record
                elif record["event"] == "end":
                    end = record
                elif record["event"] == "error":
                    capture_errors.append(record)
                elif record["event"] == "sensor_sample":
                    samples.append(_validated_sample(record))
                elif record["event"] == "query_result" and record.get("command") == "sensors":
                    samples.append(_legacy_sample(record, len(samples) + 1))
            except (ValueError, TypeError, AttributeError) as exc:
                raise FlatSatError(f"Invalid capture {path}, line {line_number}: {exc}") from exc
    explicit_elapsed = any(sample.get("elapsed_s") is not None for sample in samples)
    inferred_elapsed = False
    for sample in samples:
        timestamp = _timestamp(sample.get("time_utc"))
        if timestamp is not None:
            if first_time is None:
                first_time = timestamp
            if sample.get("elapsed_s") is None and session.get("mode") != "watch":
                difference = timestamp - first_time
                # Backward wall-clock records and missing monotonic timings are
                # unavailable, rather than fabricated zero-second observations.
                if difference >= 0:
                    sample["elapsed_s"] = difference
                    inferred_elapsed = True
    return {"source": str(path.resolve()), "session": session, "samples": samples,
            "end": end, "capture_errors": capture_errors,
            "summary": _summary(samples, session, end, capture_errors,
                                mixed_basis=explicit_elapsed and inferred_elapsed)}
