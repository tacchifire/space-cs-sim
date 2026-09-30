"""Descriptive comparisons of two recorded experiments, without device IO."""
from __future__ import annotations

import math
from pathlib import Path

from .telemetry import METRICS, load_capture


def _board_id(data: dict) -> str | None:
    ports = data["session"].get("ports", [])
    if not isinstance(ports, list):
        return None
    identities = {str(port["serial_number"]) for port in ports
                  if isinstance(port, dict) and port.get("serial_number")}
    return next(iter(identities)) if len(identities) == 1 else None


def compare_captures(baseline: Path, candidate: Path) -> dict:
    """Compare summaries; observations are independent, not paired measurements."""
    baseline_data = load_capture(baseline)
    candidate_data = load_capture(candidate)
    first_board, second_board = _board_id(baseline_data), _board_id(candidate_data)
    board_match = ("unknown" if first_board is None or second_board is None else
                   "same" if first_board == second_board else "different")
    notes = []
    if board_match != "same":
        notes.append("different_boards" if board_match == "different" else "unknown_board_id")
    metrics = {}
    for key in METRICS:
        first = baseline_data["summary"]["metrics"][key]
        second = candidate_data["summary"]["metrics"][key]
        delta = None
        if first["mean"] is not None and second["mean"] is not None:
            difference = second["mean"] - first["mean"]
            if math.isfinite(difference):
                delta = difference
            else:
                notes.append(f"nonfinite_difference:{key}")
        metrics[key] = {"baseline_mean": first["mean"], "candidate_mean": second["mean"],
                        "mean_delta": delta, "baseline_count": first["count"],
                        "candidate_count": second["count"]}
    for name, data in (("baseline", baseline_data), ("candidate", candidate_data)):
        if not data["summary"]["valid_count"]:
            notes.append(f"{name}_no_valid_samples")
        if data["capture_errors"] or data["end"] is None:
            notes.append(f"{name}_unfinished")
        elif data["end"].get("reason") == "response_timeout":
            notes.append(f"{name}_response_timeout")
    return {"baseline": baseline_data, "candidate": candidate_data,
            "board_match": board_match, "metrics": metrics, "notes": notes}
