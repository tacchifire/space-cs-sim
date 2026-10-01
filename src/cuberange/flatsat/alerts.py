"""Finite sensor limits and transition-only alerts for saved or live samples."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .telemetry import METRICS


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


@dataclass(frozen=True)
class Limit:
    metric: str
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self):
        if not isinstance(self.metric, str) or self.metric not in METRICS:
            raise ValueError(f"Unknown sensor metric: {self.metric!r}")
        if self.minimum is None and self.maximum is None:
            raise ValueError(f"{self.metric}: at least one limit bound is required")
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if value is None:
                continue
            number = _finite(value)
            if number is None:
                raise ValueError(f"{self.metric}: {name} must be a finite number, not a boolean")
            object.__setattr__(self, name, number)
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError(f"{self.metric}: minimum must not exceed maximum")

    def as_dict(self) -> dict:
        return {"metric": self.metric, "minimum": self.minimum, "maximum": self.maximum}


def parse_limit(text: str) -> Limit:
    """Parse METRIC:MIN:MAX; a blank minimum or maximum leaves it unbounded."""
    if not isinstance(text, str) or len(parts := text.split(":")) != 3:
        raise ValueError("Limit must use METRIC:MIN:MAX, with a blank bound for one-sided limits")
    metric, minimum, maximum = (part.strip() for part in parts)
    try:
        return Limit(metric, float(minimum) if minimum else None, float(maximum) if maximum else None)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid limit {text!r}: {exc}") from exc


def validate_limits(limits) -> list[Limit]:
    """Return an independent ordered list, rejecting duplicate metric settings."""
    try:
        result = list(limits)
    except TypeError as exc:
        raise ValueError("Limits must be a collection of Limit objects") from exc
    seen = set()
    for limit in result:
        if not isinstance(limit, Limit):
            raise ValueError("Limits must contain Limit objects")
        if limit.metric in seen:
            raise ValueError(f"Duplicate limit for metric: {limit.metric}")
        seen.add(limit.metric)
    return result


class AlertTracker:
    """Track each metric independently; a data gap never counts as recovery."""
    def __init__(self, limits):
        self.limits = tuple(validate_limits(limits))
        self._states = {limit.metric: "unknown" for limit in self.limits}
        self._seen = set()

    @property
    def states(self) -> dict:
        return dict(self._states)

    def update(self, sample: dict) -> list[dict]:
        if not isinstance(sample, dict):
            raise ValueError("Alert sample must be an object")
        values = sample.get("values", {})
        usable = sample.get("status") == "ok" and sample.get("completion") != "timeout"
        events = []
        for limit in self.limits:
            value = _finite(values.get(limit.metric)) if usable and isinstance(values, dict) else None
            if value is None:
                state = "unknown"
            elif limit.minimum is not None and value < limit.minimum:
                state = "below"
            elif limit.maximum is not None and value > limit.maximum:
                state = "above"
            else:
                state = "normal"
            previous = self._states[limit.metric] if limit.metric in self._seen else None
            self._states[limit.metric] = state
            self._seen.add(limit.metric)
            if state == previous or state == "normal" and previous is None:
                continue
            if state == "unknown":
                transition = "unavailable"
            elif state in ("below", "above"):
                transition = "triggered"
            elif previous == "unknown":
                transition = "resumed"
            else:
                transition = "recovered"
            events.append({**limit.as_dict(), "value": value, "state": state,
                           "previous_state": previous, "transition": transition})
        return events


def _stored_limits(data: dict) -> list[Limit]:
    session = data.get("session", {})
    if not isinstance(session, dict):
        raise ValueError("Capture session must be an object")
    records = session.get("limits", [])
    if not isinstance(records, list):
        raise ValueError("Stored limits must be a list")
    limits = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"metric", "minimum", "maximum"}:
            raise ValueError("Each stored limit must contain exactly metric, minimum, and maximum")
        limits.append(Limit(**record))
    return validate_limits(limits)


def evaluate_capture(data: dict, limits=None) -> dict:
    """Replay validated capture samples against explicit or stored limits offline."""
    if not isinstance(data, dict):
        raise ValueError("Capture data must be an object")
    configured = _stored_limits(data) if limits is None else validate_limits(limits)
    if not configured:
        raise ValueError("No alert limits configured; supply limits or record them in session.limits")
    samples = data.get("samples", [])
    if not isinstance(samples, list):
        raise ValueError("Capture samples must be a list")
    tracker = AlertTracker(configured)
    events = []
    evaluated_count = 0
    for position, sample in enumerate(samples, 1):
        if (isinstance(sample, dict) and sample.get("status") == "ok"
                and sample.get("completion") != "timeout" and isinstance(sample.get("values"), dict)
                and all(_finite(sample["values"].get(limit.metric)) is not None for limit in configured)):
            evaluated_count += 1
        for event in tracker.update(sample):
            index = sample.get("index", position)
            if isinstance(index, bool) or not isinstance(index, int):
                index = None
            time_utc = sample.get("time_utc")
            event.update(index=index, time_utc=time_utc if isinstance(time_utc, str) else None,
                         elapsed_s=_finite(sample.get("elapsed_s")))
            events.append(event)
    summary = data.get("summary")
    quality = summary.get("quality") if isinstance(summary, dict) else None
    capture_quality = dict(quality) if isinstance(quality, dict) else {"completion": "unknown", "notes": []}
    return {"limits": [limit.as_dict() for limit in configured], "events": events,
            "final_states": tracker.states,
            "trigger_count": sum(event["transition"] == "triggered" for event in events),
            "recovery_count": sum(event["transition"] == "recovered" for event in events),
            "unavailable_count": sum(event["transition"] == "unavailable" for event in events),
            "resumed_count": sum(event["transition"] == "resumed" for event in events),
            "sample_count": len(samples), "evaluated_count": evaluated_count,
            "failed_count": len(samples) - evaluated_count, "capture_quality": capture_quality}
