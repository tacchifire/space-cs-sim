"""Fixed-baseline room change detection with sparse, attributable USB evidence."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import threading
import time

from . import __main__ as cli
from .telemetry import BASE_FIELDS, METRICS, _validated_sample
from .usb import FlatSatError, SerialSession

DEFAULT_THRESHOLDS = {"movement_mg": 80.0, "temperature_c": 2.0,
                      "humidity_percent": 10.0, "pressure_pa": 500.0}
MAX_LOG_BYTES = 16 * 1024 * 1024
_END_RESERVE = 16384
_AXES = ("accel_x_mg", "accel_y_mg", "accel_z_mg")
_STATES = ("normal", "changed", "unavailable", "calibrating")
_PHASES = ("calibrating", "monitoring", "unavailable", "stopped")
_REASONS = ("stopped", "duration_complete", "response_timeout", "device_error", "size_limit", "interrupted")


def compact_events(events):
    """USB proof stays in JSONL; UI polling needs only the event description."""
    return [{key: value for key, value in event.items()
             if key not in ("raw_hex", "baseline_samples", "confirmation_samples")}
            for event in events]


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be a {'positive ' if positive else ''}finite number")
    return number


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def validate_config(*, baseline_samples=20, confirm_samples=3, thresholds=None):
    baseline_samples = _integer(baseline_samples, "baseline_samples", 3, 120)
    confirm_samples = _integer(confirm_samples, "confirm_samples", 1, 10)
    if thresholds is None:
        thresholds = {}
    if not isinstance(thresholds, dict) or thresholds.keys() - DEFAULT_THRESHOLDS.keys():
        raise ValueError("thresholds must contain only movement_mg/temperature_c/humidity_percent/pressure_pa")
    configured = dict(DEFAULT_THRESHOLDS)
    configured.update({key: _number(value, key, positive=True) for key, value in thresholds.items()})
    return {"baseline_samples": baseline_samples, "confirm_samples": confirm_samples,
            "thresholds": configured}


def _median(values):
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    return ordered[midpoint] if len(ordered) % 2 else ordered[midpoint - 1] / 2 + ordered[midpoint] / 2


def _deviations(values, baseline):
    result = {"movement_mg": math.hypot(*(values[key] - baseline[key] for key in _AXES)),
              **{key: abs(values[key] - baseline[key])
                 for key in DEFAULT_THRESHOLDS if key != "movement_mg"}}
    if any(not math.isfinite(value) for value in result.values()):
        raise ValueError("sensor deviation exceeds the finite range")
    return result


class RoomDetector:
    """Confirmation and hysteresis are independent for each fixed-baseline metric."""
    def __init__(self, *, baseline_samples=20, confirm_samples=3, thresholds=None, max_gap_s=60):
        config = validate_config(baseline_samples=baseline_samples, confirm_samples=confirm_samples,
                                 thresholds=thresholds)
        self.baseline_target = config["baseline_samples"]
        self.confirm_samples = config["confirm_samples"]
        self.thresholds = config["thresholds"]
        self.baseline = None
        self.seed = []
        self.phase = "calibrating"
        self.reason = None
        self.observed_count = self.valid_count = self.failed_count = 0
        self.event_count = 0
        self.last_sample = None
        self.states = dict.fromkeys(DEFAULT_THRESHOLDS, "calibrating")
        self._previous_states = None
        self._consecutive = dict.fromkeys(DEFAULT_THRESHOLDS, 0)
        self._pending = {metric: [] for metric in DEFAULT_THRESHOLDS}
        self.events = []
        self._last_elapsed = None
        self.max_gap_s = _number(max_gap_s, "max_gap_s", positive=True)

    def snapshot(self, *, include_events=True):
        result = {"phase": self.phase, "reason": self.reason,
                  "baseline_count": len(self.seed), "baseline_target": self.baseline_target,
                  "observed_count": self.observed_count, "valid_count": self.valid_count,
                  "failed_count": self.failed_count, "event_count": self.event_count,
                  "last_sample": self.last_sample, "baseline": self.baseline,
                  "thresholds": dict(self.thresholds), "states": dict(self.states)}
        if include_events:
            result["events"] = compact_events(self.events[-50:])
        return result

    def stop(self, reason):
        if reason not in _REASONS:
            raise ValueError("unknown room watch termination reason")
        self.phase, self.reason = "stopped", reason

    def _event(self, transition, sample, raw_hex, *, metric=None, state=None, confirmation=None):
        deviations = sample.get("deviations")
        event = {"transition": transition, "metric": metric, "time_utc": sample["time_utc"],
                 "index": sample["index"], "elapsed_s": sample.get("elapsed_s"),
                 "baseline": self.baseline, "values": sample["values"], "deviations": deviations,
                 "value": deviations.get(metric) if metric and deviations else None,
                 "threshold": self.thresholds.get(metric), "state": state,
                 "status": sample["status"], "completion": sample.get("completion"),
                 "raw_hex": raw_hex}
        if transition == "baseline_ready":
            event["baseline_method"] = "component_median"
            event["baseline_samples"] = list(self.seed)
        if confirmation is not None:
            event["confirmation_samples"] = list(confirmation)
        self.event_count += 1
        self.events.append(event)
        self.events = self.events[-50:]
        return event

    def update(self, sample, *, raw_hex=""):
        if self.phase == "stopped":
            raise ValueError("room detector is stopped")
        if not isinstance(raw_hex, str) or len(raw_hex) % 2 or re.fullmatch(r"[0-9a-fA-F]*", raw_hex) is None:
            raise ValueError("raw_hex must be an even-length hexadecimal string")
        parsed = _validated_sample(sample)
        parsed = {key: parsed.get(key) for key in ("time_utc", "elapsed_s", "response_ms", "status",
                                                  "values", "errors", "missing_fields", "completion")}
        self.observed_count += 1
        parsed.update(index=self.observed_count, time_utc=parsed["time_utc"] or _utc(), deviations=None)
        elapsed = parsed.get("elapsed_s")
        if elapsed is None:
            parsed["status"] = "invalid"
            parsed["errors"] = [*parsed["errors"], "room query timing is missing"]
        else:
            if self._last_elapsed is not None and elapsed <= self._last_elapsed:
                parsed["status"] = "invalid"
                parsed["errors"] = [*parsed["errors"], "room query timing is not strictly increasing"]
            else:
                if self._last_elapsed is not None and elapsed - self._last_elapsed > self.max_gap_s:
                    parsed["status"] = "invalid"
                    parsed["errors"] = [*parsed["errors"], "room query gap interrupted confirmation"]
                self._last_elapsed = elapsed
        parsed["errors"] = parsed["errors"][:32]
        if parsed["status"] == "ok" and parsed.get("completion") not in ("complete", "quiet"):
            parsed["status"] = "invalid"
            parsed["errors"] = [*parsed["errors"], "room query did not complete"][:32]
        usable = parsed["status"] == "ok"
        if usable and self.baseline:
            try:
                parsed["deviations"] = _deviations(parsed["values"], self.baseline)
            except ValueError as exc:
                parsed["status"] = "invalid"
                parsed["errors"] = [*parsed["errors"], str(exc)]
                usable = False
        self.last_sample = parsed
        events = []
        if not usable:
            self.failed_count += 1
            self._consecutive = dict.fromkeys(DEFAULT_THRESHOLDS, 0)
            self._pending = {metric: [] for metric in DEFAULT_THRESHOLDS}
            if self.phase != "unavailable":
                self._previous_states = dict(self.states)
                self.phase = "unavailable"
                self.states = dict.fromkeys(DEFAULT_THRESHOLDS, "unavailable")
                events.append(self._event("unavailable", parsed, raw_hex, state="unavailable"))
            return events
        self.valid_count += 1
        if self.phase == "unavailable":
            self.phase = "monitoring" if self.baseline else "calibrating"
            self.states = self._previous_states or dict.fromkeys(DEFAULT_THRESHOLDS, "calibrating")
            self._previous_states = None
            events.append(self._event("resumed", parsed, raw_hex))
        if self.baseline is None:
            self.seed.append({"index": parsed["index"], "time_utc": parsed["time_utc"],
                              "values": {key: parsed["values"][key] for key in BASE_FIELDS},
                              "raw_hex": raw_hex, "completion": parsed.get("completion")})
            if len(self.seed) == self.baseline_target:
                baseline = {key: _median([seed["values"][key] for seed in self.seed]) for key in BASE_FIELDS}
                try:
                    parsed["deviations"] = _deviations(parsed["values"], baseline)
                except ValueError as exc:
                    self.seed.pop()
                    parsed["status"], parsed["errors"] = "invalid", [str(exc)]
                    self.valid_count -= 1
                    self.failed_count += 1
                    self._previous_states = dict(self.states)
                    self.phase, self.states = "unavailable", dict.fromkeys(DEFAULT_THRESHOLDS, "unavailable")
                    events.append(self._event("unavailable", parsed, raw_hex, state="unavailable"))
                    return events
                self.baseline = baseline
                self.phase = "monitoring"
                self.states = dict.fromkeys(DEFAULT_THRESHOLDS, "normal")
                events.append(self._event("baseline_ready", parsed, raw_hex))
            return events
        for metric, deviation in parsed["deviations"].items():
            changed = self.states[metric] == "changed"
            condition = deviation <= self.thresholds[metric] * 0.6 if changed else deviation >= self.thresholds[metric]
            if condition:
                self._pending[metric].append({"index": parsed["index"], "time_utc": parsed["time_utc"],
                                              "elapsed_s": parsed["elapsed_s"], "values": dict(parsed["values"]),
                                              "raw_hex": raw_hex, "completion": parsed.get("completion")})
            else:
                self._pending[metric] = []
            self._consecutive[metric] = self._consecutive[metric] + 1 if condition else 0
            if self._consecutive[metric] >= self.confirm_samples:
                self.states[metric] = "normal" if changed else "changed"
                self._consecutive[metric] = 0
                events.append(self._event("recovered" if changed else "changed", parsed, raw_hex,
                                          metric=metric, state=self.states[metric],
                                          confirmation=self._pending[metric]))
                self._pending[metric] = []
        return events


class LogSizeLimit(FlatSatError):
    pass


class RoomCapture:
    """Keep sparse records below a hard byte bound, reserving space for an end."""
    def __init__(self, path, *, max_bytes=MAX_LOG_BYTES):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.written = 0
        self._stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("xb")
        return self

    def __exit__(self, *_):
        self._stream.close()

    def event(self, name, *, terminal=False, **fields):
        record = {"time_utc": _utc(), "event": name, **fields}
        self._write([record], terminal=terminal)

    def room_events(self, events):
        self._write([{"time_utc": _utc(), "event": "room_event", **event} for event in events])

    def _write(self, records, *, terminal=False):
        encoded = "".join(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n"
                          for record in records).encode("utf-8")
        limit = self.max_bytes if terminal else self.max_bytes - _END_RESERVE
        if self.written + len(encoded) > limit:
            raise LogSizeLimit("Room recording reached its 16 MiB size limit")
        self._stream.write(encoded)
        self._stream.flush()
        self.written += len(encoded)


class _QueryEvidence:
    """The shared bounded query can retain bytes without persisting every poll."""
    def __init__(self, *, discard=False):
        self.raw = bytearray()
        self.discard = discard

    def event(self, name, **fields):
        if name == "rx" and not self.discard:
            if len(self.raw) + len(fields["raw_hex"]) // 2 > cli.MAX_QUERY_REPLY + 4096:
                raise FlatSatError("room query evidence exceeded its byte limit")
            self.raw.extend(bytes.fromhex(fields["raw_hex"]))


def run_room_watch(port, duration, interval, timeout, output, *, baseline_samples=20,
                   confirm_samples=3, thresholds=None, label="", stop_event=None,
                   progress=None, max_bytes=MAX_LOG_BYTES):
    if port.role != "shell":
        raise FlatSatError("room_watch requires the Cat-Shell interface")
    for name, value, minimum, maximum in (("duration", duration, 0.2, 86400),
                                           ("interval", interval, 0.2, 60), ("timeout", timeout, 0.05, 5)):
        number = _number(value, name)
        if not minimum <= number <= maximum:
            raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    detector = RoomDetector(baseline_samples=baseline_samples, confirm_samples=confirm_samples,
                            thresholds=thresholds, max_gap_s=interval * 3 + timeout)
    stop_event = stop_event if stop_event is not None else threading.Event()
    progress = progress or (lambda _snapshot: None)
    reason, error = "duration_complete", None
    with RoomCapture(output, max_bytes=max_bytes) as capture:
        capture.event("session", mode="room_watch", ports=[asdict(port)], duration_s=duration,
                      interval_s=interval, timeout_s=timeout, label=label,
                      baseline_samples=detector.baseline_target, confirm_samples=detector.confirm_samples,
                      thresholds=detector.thresholds, baseline_method="component_median",
                      timestamp_basis="host query start; not device acquisition time")
        started = time.monotonic()
        deadline, next_query, heartbeat = started + duration, started, started + 60
        try:
            with SerialSession(port) as link:
                cli._drain(link, _QueryEvidence(discard=True), min(deadline, started + 0.2))
                next_query = time.monotonic()
                while time.monotonic() < deadline:
                    if stop_event.is_set():
                        reason = "stopped"
                        break
                    now = time.monotonic()
                    if now < next_query:
                        stop_event.wait(min(next_query - now, deadline - now, 0.2))
                        continue
                    requested_at = _utc()
                    evidence = _QueryEvidence()
                    result = cli._query(link, evidence, "sensors", min(timeout, deadline - now))
                    sample = {"time_utc": requested_at, "elapsed_s": now - started,
                              "response_ms": result["response_ms"], "completion": result["completion"],
                              **result["parsed"]}
                    previous = {"phase": detector.phase, "baseline": detector.baseline,
                                "seed": list(detector.seed), "states": dict(detector.states),
                                "events": list(detector.events), "event_count": detector.event_count,
                                "_consecutive": dict(detector._consecutive),
                                "_pending": {metric: list(items) for metric, items in detector._pending.items()},
                                "_previous_states": dict(detector._previous_states) if detector._previous_states else None}
                    events = detector.update(sample, raw_hex=evidence.raw.hex())
                    try:
                        capture.room_events(events)
                    except LogSizeLimit:
                        # No transition in the batch was written. Preserve poll
                        # totals while restoring only unrecorded detector state.
                        detector.__dict__.update(previous)
                        detector.last_sample["deviations"] = (_deviations(detector.last_sample["values"], detector.baseline)
                                                              if detector.baseline and detector.last_sample["status"] == "ok" else None)
                        raise
                    finished = time.monotonic()
                    if finished >= heartbeat:
                        capture.event("room_heartbeat", room_watch=detector.snapshot(include_events=False),
                                      raw_hex=evidence.raw.hex())
                        heartbeat = finished + 60
                    progress(detector.snapshot())
                    if result["completion"] == "timeout":
                        reason, error = "response_timeout", "Sensor response timed out; start a new room watch to resynchronize"
                        break
                    next_query = max(now + interval, finished + 0.001)
                if stop_event.is_set() and error is None:
                    reason = "stopped"
        except LogSizeLimit as exc:
            reason, error = "size_limit", str(exc)
        except (FlatSatError, OSError, ValueError) as exc:
            reason, error = "device_error", str(exc)[:1000]
            if detector.phase != "unavailable":
                previous = {"phase": detector.phase, "states": dict(detector.states),
                            "events": list(detector.events), "event_count": detector.event_count,
                            "_consecutive": dict(detector._consecutive),
                            "_pending": {metric: list(items) for metric, items in detector._pending.items()},
                            "_previous_states": dict(detector._previous_states) if detector._previous_states else None}
                try:
                    missing = {"time_utc": _utc(), "elapsed_s": time.monotonic() - started,
                               "status": "invalid", "values": dict.fromkeys(METRICS),
                               "missing_fields": list(BASE_FIELDS), "errors": [error], "completion": "error"}
                    events = detector.update(missing)
                    capture.room_events(events)
                except LogSizeLimit:
                    detector.__dict__.update(previous)
                    reason = "size_limit"
        detector.stop(reason)
        if error:
            capture.event("error", terminal=True, message=error)
        capture.event("end", terminal=True, reason=reason,
                      room_watch=detector.snapshot(include_events=False))
        progress(detector.snapshot())
    return 1 if error else 0


def _strict_load(line):
    def bad_constant(value):
        raise ValueError(f"non-finite JSON value: {value}")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate room capture key")
            result[key] = value
        return result
    try:
        return json.loads(line, parse_constant=bad_constant, object_pairs_hook=pairs)
    except RecursionError as exc:
        raise ValueError("room capture JSON nesting is too deep") from exc


def _hex(value):
    if not isinstance(value, str) or len(value) % 2 or re.fullmatch(r"[0-9a-fA-F]*", value) is None:
        raise ValueError("room raw_hex must be an even-length hexadecimal string")
    return value


def _baseline(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.keys() != set(BASE_FIELDS):
        raise ValueError("room baseline must contain the six sensor values")
    result = {key: _number(value[key], f"baseline.{key}") for key in BASE_FIELDS}
    if result["pressure_pa"] <= 0 or not 0 <= result["humidity_percent"] <= 100:
        raise ValueError("room baseline has invalid sensor values")
    return result


def _date(value):
    if not isinstance(value, str):
        raise ValueError("room event timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("room timestamp requires its timezone")
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid room timestamp") from exc
    return parsed


def _proof_sample(item):
    if not isinstance(item, dict) or not isinstance(item.get("values"), dict):
        raise ValueError("invalid room confirmation sample")
    values = item["values"]
    if values.keys() != METRICS.keys():
        raise ValueError("room confirmation sample requires all sensor metrics")
    for key, value in values.items():
        _number(value, f"confirmation.{key}")
    actual = cli._sensor_reply(bytes.fromhex(_hex(item.get("raw_hex"))).decode("utf-8", errors="replace"))
    if actual["status"] != "ok" or actual["values"] != values:
        raise ValueError("room confirmation values differ from USB evidence")
    if item.get("completion") not in ("complete", "quiet"):
        raise ValueError("room confirmation requires a completed reply")
    return values


def _remember_proof(proofs, index, values, raw_hex, stamp, elapsed):
    proof = {"values": values, "raw_hex": raw_hex.lower(), "time_utc": stamp, "elapsed_s": elapsed}
    existing = proofs.get(index)
    if existing and any(existing[key] != proof[key] for key in proof
                        if existing[key] is not None and proof[key] is not None):
        raise ValueError("room evidence for one observation is inconsistent")
    proofs[index] = {key: proof[key] if proof[key] is not None else (existing or {}).get(key)
                     for key in proof}


def _confirmation(record, config, baseline, reset_index, proofs):
    items = record.get("confirmation_samples")
    if not isinstance(items, list) or len(items) != config["confirm_samples"]:
        raise ValueError("room transition requires its configured confirmation samples")
    first_index = record["index"] - len(items) + 1
    if first_index <= reset_index:
        raise ValueError("room confirmation crosses a missing-data or calibration boundary")
    last_elapsed, last_stamp = None, None
    for offset, item in enumerate(items):
        values = _proof_sample(item)
        index = _integer(item.get("index"), "confirmation index", 1, record["index"])
        elapsed = _number(item.get("elapsed_s"), "confirmation elapsed_s")
        stamp = _date(item.get("time_utc"))
        if index != first_index + offset or elapsed < 0:
            raise ValueError("room confirmation samples are not consecutive")
        if last_elapsed is not None and (elapsed <= last_elapsed or stamp <= last_stamp or
                                         elapsed - last_elapsed > config["max_gap_s"]):
            raise ValueError("room confirmation sample timing is not consecutive")
        deviation = _deviations(values, baseline)[record["metric"]]
        threshold = config["thresholds"][record["metric"]]
        if ((record["transition"] == "changed" and deviation < threshold) or
                (record["transition"] == "recovered" and deviation > threshold * 0.6)):
            raise ValueError("room confirmation does not satisfy its transition threshold")
        _remember_proof(proofs, index, values, _hex(item["raw_hex"]), stamp, elapsed)
        last_elapsed, last_stamp = elapsed, stamp
    last = items[-1]
    if (last["index"] != record["index"] or _date(last["time_utc"]) != _date(record["time_utc"]) or
            last["elapsed_s"] != record["elapsed_s"] or last["values"] != record["values"] or
            _hex(last["raw_hex"]).lower() != _hex(record["raw_hex"]).lower() or
            last["completion"] != record.get("completion")):
        raise ValueError("room confirmation tail differs from its event")


def _validate_snapshot(snapshot, config):
    if not isinstance(snapshot, dict) or snapshot.get("phase") not in _PHASES:
        raise ValueError("invalid room snapshot phase")
    for key in ("baseline_count", "baseline_target", "observed_count", "valid_count", "failed_count", "event_count"):
        _integer(snapshot.get(key), key, 0, 1000000)
    if (snapshot["baseline_target"] != config["baseline_samples"] or
            snapshot["baseline_count"] > snapshot["baseline_target"] or
            snapshot["valid_count"] + snapshot["failed_count"] != snapshot["observed_count"] or
            snapshot["baseline_count"] > snapshot["valid_count"]):
        raise ValueError("inconsistent room snapshot counters")
    supplied = snapshot.get("thresholds")
    if not isinstance(supplied, dict) or supplied.keys() != DEFAULT_THRESHOLDS.keys():
        raise ValueError("room snapshot thresholds must contain the four configured metrics")
    configured = validate_config(baseline_samples=config["baseline_samples"],
                                 confirm_samples=config["confirm_samples"], thresholds=supplied)
    if configured["thresholds"] != config["thresholds"]:
        raise ValueError("room snapshot thresholds differ from its session")
    baseline = _baseline(snapshot.get("baseline"))
    if (baseline is None) != (snapshot["baseline_count"] < snapshot["baseline_target"]):
        raise ValueError("room baseline readiness differs from its counters")
    states = snapshot.get("states")
    if (not isinstance(states, dict) or states.keys() != DEFAULT_THRESHOLDS.keys() or
            any(state not in _STATES for state in states.values())):
        raise ValueError("invalid room metric states")
    last = snapshot.get("last_sample")
    if last is not None:
        _integer(last.get("index") if isinstance(last, dict) else None, "last sample index", 1, 1000000)
        if not isinstance(last, dict) or last.get("index") != snapshot["observed_count"]:
            raise ValueError("invalid last room sample")
        validated = _validated_sample(last)
        _date(last.get("time_utc"))
        if _number(last.get("elapsed_s"), "last_sample.elapsed_s") < 0:
            raise ValueError("invalid last room sample timing")
        if validated["status"] != last.get("status") or validated["values"] != last.get("values"):
            raise ValueError("room last sample is not a valid normalized sample")
        if validated["status"] == "ok" and last.get("completion") not in ("complete", "quiet"):
            raise ValueError("room last sample has no completed reply")
        expected = _deviations(validated["values"], baseline) if baseline and validated["status"] == "ok" else None
        if last.get("deviations") != expected:
            raise ValueError("room sample deviations differ from its baseline")
    elif snapshot["observed_count"]:
        raise ValueError("missing last room sample")
    if snapshot["phase"] == "stopped" and snapshot.get("reason") not in _REASONS:
        raise ValueError("invalid room termination reason")
    return dict(snapshot)


def load_room_capture(path):
    """Validate sparse monitoring metadata and the raw evidence at each event."""
    path = Path(path)
    if path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError("room capture exceeds 16 MiB")
    records = [_strict_load(line) for line in path.read_text(encoding="utf-8").splitlines()]
    json.dumps(records, allow_nan=False)
    if not records or any(not isinstance(record, dict) or not isinstance(record.get("event"), str) for record in records):
        raise ValueError("invalid room capture records")
    sessions = [record for record in records if record["event"] == "session"]
    if len(sessions) != 1 or sessions[0].get("mode") != "room_watch":
        raise ValueError("room capture requires one room_watch session")
    session = sessions[0]
    if records[0] is not session or sum(record["event"] == "end" for record in records) > 1:
        raise ValueError("room session/end ordering is invalid")
    if any(record["event"] == "end" for record in records[:-1]):
        raise ValueError("room records follow their end")
    for key, low, high in (("duration_s", 0.2, 86400), ("interval_s", 0.2, 60), ("timeout_s", 0.05, 5)):
        value = _number(session.get(key), key)
        if not low <= value <= high:
            raise ValueError(f"invalid room session {key}")
    if not isinstance(session.get("label", ""), str) or len(session.get("label", "")) > 200:
        raise ValueError("invalid room session label")
    _date(session.get("time_utc"))
    config = validate_config(baseline_samples=session.get("baseline_samples"),
                             confirm_samples=session.get("confirm_samples"), thresholds=session.get("thresholds"))
    config["max_gap_s"] = session["interval_s"] * 3 + session["timeout_s"]
    if session.get("baseline_method") != "component_median":
        raise ValueError("unknown room baseline method")
    events, snapshot = [], None
    states = dict.fromkeys(DEFAULT_THRESHOLDS, "calibrating")
    previous_states = None
    previous_index, previous_elapsed, previous_time = 0, None, None
    previous_totals = (0, 0, 0)
    heartbeat_frontier = 0
    reset_index = 0
    proofs = {}
    for record in records:
        kind = record["event"]
        if kind == "room_event":
            transition, metric = record.get("transition"), record.get("metric")
            if transition not in ("baseline_ready", "changed", "recovered", "unavailable", "resumed"):
                raise ValueError("unknown room event transition")
            _integer(record.get("index"), "event.index", 1, 1000000)
            stamp = _date(record.get("time_utc"))
            elapsed = _number(record.get("elapsed_s"), "event.elapsed_s")
            if (elapsed < 0 or elapsed > session["duration_s"] + session["timeout_s"] + 1 or
                    record["index"] <= heartbeat_frontier or
                    record["index"] < previous_index or
                    (previous_elapsed is not None and elapsed < previous_elapsed) or
                    (previous_time is not None and stamp < previous_time)):
                raise ValueError("room event ordering is invalid")
            if record["index"] == previous_index and (elapsed != previous_elapsed or stamp != previous_time):
                raise ValueError("room events from one poll disagree on timing")
            previous_index, previous_elapsed, previous_time = record["index"], elapsed, stamp
            baseline = _baseline(record.get("baseline"))
            raw = bytes.fromhex(_hex(record.get("raw_hex"))).decode("utf-8", errors="replace")
            values = record.get("values")
            sample = _validated_sample({"values": values, "status": record.get("status"),
                                        "completion": record.get("completion"), "errors": [],
                                        "elapsed_s": record.get("elapsed_s")})
            if sample["status"] == "ok" and record.get("completion") not in ("complete", "quiet"):
                raise ValueError("room event has no completed reply")
            usable = sample["status"] == "ok"
            if transition in ("baseline_ready", "resumed") and not usable:
                raise ValueError("room readiness/resumption requires a valid sample")
            if transition == "unavailable" and (usable or record.get("state") != "unavailable"):
                raise ValueError("room unavailable event requires a failed sample")
            if usable:
                if cli._sensor_reply(raw)["values"] != sample["values"] or cli._sensor_reply(raw)["status"] != "ok":
                    raise ValueError("room event values differ from USB raw response")
                _remember_proof(proofs, record["index"], sample["values"], _hex(record["raw_hex"]), stamp, elapsed)
            if transition in ("changed", "recovered"):
                if metric not in DEFAULT_THRESHOLDS or baseline is None or not usable:
                    raise ValueError("invalid room change event")
                deviation = _deviations(sample["values"], baseline)
                _number(record.get("value"), "event.value")
                _number(record.get("threshold"), "event.threshold", positive=True)
                if (record.get("deviations") != deviation or record.get("value") != deviation[metric] or
                        record.get("threshold") != config["thresholds"][metric] or
                        record.get("state") != ("changed" if transition == "changed" else "normal")):
                    raise ValueError("inconsistent room change event")
                if ((transition == "changed" and deviation[metric] < config["thresholds"][metric]) or
                        (transition == "recovered" and deviation[metric] > config["thresholds"][metric] * 0.6)):
                    raise ValueError("room event does not cross its threshold")
                _confirmation(record, config, baseline, reset_index, proofs)
                expected_previous = "normal" if transition == "changed" else "changed"
                if states[metric] != expected_previous:
                    raise ValueError("room metric transition order is invalid")
                states[metric] = record["state"]
            elif metric is not None:
                raise ValueError("non-metric room event cannot name a metric")
            if transition == "baseline_ready":
                seed = record.get("baseline_samples")
                if not isinstance(seed, list) or len(seed) != config["baseline_samples"] or baseline is None:
                    raise ValueError("invalid room baseline evidence")
                for item in seed:
                    if not isinstance(item, dict) or not isinstance(item.get("values"), dict):
                        raise ValueError("invalid baseline sample")
                    if item.get("completion") not in ("complete", "quiet"):
                        raise ValueError("room baseline sample has no completed reply")
                    _baseline(item["values"])
                    actual = cli._sensor_reply(bytes.fromhex(_hex(item.get("raw_hex"))).decode("utf-8", errors="replace"))
                    if actual["status"] != "ok" or any(actual["values"][key] != item["values"].get(key) for key in BASE_FIELDS):
                        raise ValueError("room baseline values differ from USB evidence")
                    _integer(item.get("index"), "baseline sample index", 1, record["index"])
                    _date(item.get("time_utc"))
                    _remember_proof(proofs, item["index"], actual["values"], _hex(item["raw_hex"]),
                                    _date(item["time_utc"]), None)
                if any(left["index"] >= right["index"] for left, right in zip(seed, seed[1:])):
                    raise ValueError("room baseline samples are not ordered")
                expected = {key: _median([item["values"][key] for item in seed]) for key in BASE_FIELDS}
                if baseline != expected:
                    raise ValueError("room baseline differs from its initial samples")
                if any(state != "calibrating" for state in states.values()):
                    raise ValueError("room baseline became ready twice or during an unavailable state")
                states = dict.fromkeys(DEFAULT_THRESHOLDS, "normal")
                reset_index = record["index"]
            elif transition == "unavailable":
                if previous_states is not None:
                    raise ValueError("duplicate room unavailable transition")
                previous_states, states = dict(states), dict.fromkeys(DEFAULT_THRESHOLDS, "unavailable")
                reset_index = record["index"]
            elif transition == "resumed":
                if previous_states is None:
                    raise ValueError("room resumed without an unavailable transition")
                states, previous_states = previous_states, None
            events.append(record)
        elif kind in ("room_heartbeat", "end"):
            snapshot = _validate_snapshot(record.get("room_watch"), config)
            totals = tuple(snapshot[key] for key in ("observed_count", "valid_count", "failed_count"))
            last = snapshot.get("last_sample")
            if (any(current < previous for current, previous in zip(totals, previous_totals)) or
                    snapshot["observed_count"] < previous_index or
                    (last and previous_elapsed is not None and last.get("elapsed_s") is not None and
                     last["elapsed_s"] < previous_elapsed) or
                    (last and previous_time is not None and _date(last["time_utc"]) < previous_time)):
                raise ValueError("room snapshot precedes its recorded observations")
            previous_totals = totals
            if snapshot["event_count"] != len(events):
                raise ValueError("room snapshot event count differs from its records")
            if snapshot["states"] != states:
                raise ValueError("room snapshot states differ from its events")
            if kind == "room_heartbeat":
                raw = bytes.fromhex(_hex(record.get("raw_hex"))).decode("utf-8", errors="replace")
                last = snapshot.get("last_sample")
                if last and last.get("status") == "ok" and cli._sensor_reply(raw)["values"] != last["values"]:
                    raise ValueError("room heartbeat differs from USB evidence")
                if last and last.get("status") == "ok":
                    if cli._sensor_reply(raw)["status"] != "ok":
                        raise ValueError("room heartbeat has no synchronized USB reply")
                    _remember_proof(proofs, last["index"], last["values"], _hex(record["raw_hex"]),
                                    _date(last["time_utc"]), last["elapsed_s"])
                heartbeat_frontier = snapshot["observed_count"]
            elif snapshot["phase"] != "stopped" or record.get("reason") != snapshot.get("reason"):
                raise ValueError("room end must contain its final stopped snapshot")
            if last:
                previous_index = last["index"]
                previous_elapsed = last["elapsed_s"]
                previous_time = _date(last["time_utc"])
        elif kind not in ("session", "error"):
            raise ValueError("unexpected event in sparse room capture")
    ended = records[-1]["event"] == "end"
    if not ended:
        # Sparse logs cannot reconstruct unrecorded polls after the last flush.
        # Preserve the events, with explicitly unknown final observation totals.
        ready = next((event for event in events if event["transition"] == "baseline_ready"), None)
        snapshot = {"phase": "stopped", "reason": "interrupted", "counts_complete": False,
                    "baseline_count": config["baseline_samples"] if ready else (snapshot or {}).get("baseline_count", 0),
                    "baseline_target": config["baseline_samples"], "observed_count": None,
                    "valid_count": None, "failed_count": None, "event_count": len(events),
                    "last_sample": None, "baseline": ready["baseline"] if ready else None,
                    "thresholds": config["thresholds"], "states": states}
    elif snapshot is None:
        raise ValueError("room capture has no end snapshot")
    if snapshot["event_count"] != len(events):
        raise ValueError("room event count differs from its records")
    baseline_events = [event for event in events if event["transition"] == "baseline_ready"]
    if snapshot["baseline"] is not None:
        if len(baseline_events) != 1 or baseline_events[0]["baseline"] != snapshot["baseline"]:
            raise ValueError("room baseline changed after calibration")
        if any(event["baseline"] != snapshot["baseline"] for event in events if event["baseline"] is not None):
            raise ValueError("room events changed their fixed baseline")
    elif baseline_events:
        raise ValueError("room capture baseline state is inconsistent")
    snapshot["events"] = compact_events(events[-50:])
    return {"snapshot": snapshot, "events": events}
