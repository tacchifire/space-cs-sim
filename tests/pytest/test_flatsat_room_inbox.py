"""Room inbox items are derived from validated evidence and reviewed separately."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import stat
import sys
import threading
import time

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.flatsat import room_watch as room  # noqa: E402
from cuberange.flatsat import web  # noqa: E402
from test_flatsat_room_watch import EPOCH, sample, update  # noqa: E402
from test_flatsat_web import FakeAdapter, request, running_server  # noqa: E402


ITEM_FIELDS = {"id", "capture_id", "sequence", "kind", "metric", "time_utc",
               "recovered_at", "duration_s", "active", "value", "threshold",
               "label", "report_url", "reviewed"}


def _summary_item(capture_id, sequence, detected_at, *, kind="change", metric="movement_mg",
                  duration_s=None, active=False, reviewed=False):
    return {"id": f"{capture_id}:{sequence}", "capture_id": capture_id,
            "sequence": sequence, "kind": kind, "metric": metric,
            "time_utc": detected_at.isoformat(),
            "recovered_at": ((detected_at + timedelta(seconds=duration_s)).isoformat()
                             if duration_s is not None else None),
            "duration_s": duration_s, "active": active, "value": None,
            "threshold": None, "label": "summary", "report_url": f"/reports/{capture_id}",
            "reviewed": reviewed}


def _summary_record(capture_id, started_at, items, *, state="normal", interrupted=False,
                    label="summary"):
    states = dict.fromkeys(room.DEFAULT_THRESHOLDS, state)
    observed_at = max([started_at, *(datetime.fromisoformat(item["time_utc"]) for item in items)])
    observed_at += timedelta(seconds=1)
    snapshot = {"phase": "stopped", "reason": "interrupted" if interrupted else "stopped",
                "counts_complete": not interrupted, "states": states,
                "last_sample": None if interrupted else {
                    "time_utc": observed_at.isoformat(), "status": "ok", "completion": "complete"}}
    return {"capture_id": capture_id,
            "session": {"time_utc": started_at.isoformat(), "label": label,
                        "duration_s": 120, "timeout_s": .3},
            "snapshot": snapshot, "items": items, "report_url": f"/reports/{capture_id}"}


def _session(detector, label="room inbox", *, duration=120):
    return {"event": "session", "time_utc": EPOCH.isoformat(), "mode": "room_watch",
            "label": label, "duration_s": duration, "interval_s": 1, "timeout_s": .3,
            "baseline_samples": detector.baseline_target,
            "confirm_samples": detector.confirm_samples, "thresholds": detector.thresholds,
            "baseline_method": "component_median"}


def _capture_with_change_and_health(label="room inbox"):
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=2)
    records = [_session(detector, label)]
    for index in range(1, 6):
        for event in update(detector, index, x=100 if index >= 4 else 0):
            records.append({"event": "room_event", **event})
    missing, _ = sample(6, x=100)
    missing.update(status="timeout", completion="timeout")
    for event in detector.update(missing):
        records.append({"event": "room_event", **event})
    for event in update(detector, 7, x=100):
        records.append({"event": "room_event", **event})
    for index in (8, 9):
        for event in update(detector, index):
            records.append({"event": "room_event", **event})
    live_snapshot = detector.snapshot()
    detector.stop("stopped")
    records.append({"event": "end", "time_utc": (EPOCH + timedelta(seconds=10)).isoformat(),
                    "reason": "stopped", "room_watch": detector.snapshot(include_events=False)})
    return records, live_snapshot


def _quiet_capture(label="quiet room"):
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=2)
    records = [_session(detector, label)]
    for index in range(1, 4):
        for event in update(detector, index):
            records.append({"event": "room_event", **event})
    detector.stop("stopped")
    records.append({"event": "end", "time_utc": (EPOCH + timedelta(seconds=4)).isoformat(),
                    "reason": "stopped", "room_watch": detector.snapshot(include_events=False)})
    return records


def _many_changes(count):
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=1)
    records = [_session(detector, "busy room", duration=1000)]
    for index in range(1, 4):
        for event in update(detector, index):
            records.append({"event": "room_event", **event})
    index = 4
    for _ in range(count):
        for event in update(detector, index, x=100):
            records.append({"event": "room_event", **event})
        index += 1
        for event in update(detector, index):
            records.append({"event": "room_event", **event})
        index += 1
    detector.stop("stopped")
    records.append({"event": "end", "time_utc": (EPOCH + timedelta(seconds=index)).isoformat(),
                    "reason": "stopped", "room_watch": detector.snapshot(include_events=False)})
    return records


def _write_capture(directory, capture_id, records):
    path = directory / f"{capture_id}.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def test_quiet_capture_has_no_inbox_items(tmp_path):
    capture_id = "1" * 32
    _write_capture(tmp_path, capture_id, _quiet_capture())
    with running_server(tmp_path) as (server, adapter):
        state = request(server)[1]
        assert state["room_inbox"] == {"items": [], "counts": {
            "total_count": 0, "shown_count": 0, "change_count": 0, "health_count": 0,
            "active_count": 0, "unreviewed_count": 0}}
        assert state["captures"][0]["change_count"] == 0
        assert state["captures"][0]["health_count"] == 0
        assert state["captures"][0]["unreviewed_count"] == 0
        assert state["room_summary"]["capture_count"] == 1
        assert state["room_summary"]["all_time_counts"]["incident_count"] == 0
        assert state["room_summary"]["latest_saved_observation"]["capture_id"] == capture_id
        assert state["room_summary"]["latest_saved_observation"]["state"] == "normal"
        assert adapter.calls == []


def test_change_and_health_are_paired_counted_and_strip_raw_proof(tmp_path):
    capture_id = "2" * 32
    _write_capture(tmp_path, capture_id, _capture_with_change_and_health()[0])
    with running_server(tmp_path) as (server, _):
        state = request(server)[1]
        inbox = state["room_inbox"]
        assert inbox["counts"] == {"total_count": 2, "shown_count": 2, "change_count": 1,
                                    "health_count": 1, "active_count": 0, "unreviewed_count": 2}
        health, change = inbox["items"]
        assert set(health) == set(change) == ITEM_FIELDS
        assert health == {"id": f"{capture_id}:3", "capture_id": capture_id, "sequence": 3,
                          "kind": "health", "metric": None,
                          "time_utc": (EPOCH + timedelta(seconds=6)).isoformat(),
                          "recovered_at": (EPOCH + timedelta(seconds=7)).isoformat(),
                          "duration_s": 1.0, "active": False, "value": None, "threshold": None,
                          "label": "room inbox", "report_url": f"/reports/{capture_id}",
                          "reviewed": False}
        assert change["id"] == f"{capture_id}:2"
        assert change["kind"] == "change" and change["metric"] == "movement_mg"
        assert change["duration_s"] == 4.0 and not change["active"]
        assert change["recovered_at"] == (EPOCH + timedelta(seconds=9)).isoformat()
        assert change["value"] == 100 and change["threshold"] == 80
        capture = state["captures"][0]
        assert (capture["change_count"], capture["health_count"], capture["unreviewed_count"]) == (1, 1, 2)
        public = json.dumps(inbox)
        assert "raw_hex" not in public and "confirmation_samples" not in public and "baseline_samples" not in public


def test_review_is_csrf_protected_persistent_reversible_and_does_not_edit_capture(tmp_path):
    capture_id = "3" * 32
    raw_path = _write_capture(tmp_path, capture_id, _capture_with_change_and_health()[0])
    original = raw_path.read_bytes()
    review_path = f"/api/room-inbox/{capture_id}/2/review"
    with running_server(tmp_path) as (server, _):
        assert request(server, "POST", review_path, {"reviewed": True},
                       {"X-CubeRange-Token": "wrong"})[0] == 403
        assert request(server, "POST", review_path, {"reviewed": True},
                       {"Origin": "https://evil.example"})[0] == 403
        assert request(server, "POST", review_path, {"reviewed": 1})[0] == 400
        assert request(server, "POST", review_path, {"reviewed": True, "extra": False})[0] == 400
        assert request(server, "POST", f"/api/room-inbox/{capture_id}/1/review",
                       {"reviewed": True})[0] == 404
        assert request(server, "POST", f"/api/room-inbox/{'f' * 32}/2/review",
                       {"reviewed": True})[0] == 404
        status, result, _ = request(server, "POST", review_path, {"reviewed": True})
        assert status == 200 and result["item"]["reviewed"] is True
        assert raw_path.read_bytes() == original
        assert not list(tmp_path.glob(".*.tmp"))
        state = request(server)[1]
        change = next(item for item in state["room_inbox"]["items"] if item["sequence"] == 2)
        assert change["reviewed"] is True
        assert state["room_inbox"]["counts"]["unreviewed_count"] == 1
        assert state["captures"][0]["unreviewed_count"] == 1
    registry = json.loads((tmp_path / ".room-reviews.json").read_text())
    assert registry == {"version": 1, "reviewed": [f"{capture_id}:2"]}
    assert stat.S_IMODE((tmp_path / ".room-reviews.json").stat().st_mode) == 0o600
    with running_server(tmp_path) as (server, _):
        state = request(server)[1]
        assert next(item for item in state["room_inbox"]["items"]
                    if item["sequence"] == 2)["reviewed"] is True
        status, result, _ = request(server, "POST", review_path, {"reviewed": False})
        assert status == 200 and result["item"]["reviewed"] is False
        assert request(server)[1]["room_inbox"]["counts"]["unreviewed_count"] == 2
    with running_server(tmp_path) as (server, _):
        assert all(not item["reviewed"] for item in request(server)[1]["room_inbox"]["items"])
    assert raw_path.read_bytes() == original


def test_active_and_saved_event_use_the_same_stable_id_and_can_be_reviewed(tmp_path):
    capture_id = "4" * 32
    records, live_snapshot = _capture_with_change_and_health("active room")
    _write_capture(tmp_path, capture_id, records)
    with running_server(tmp_path) as (server, _):
        job = {"id": capture_id, "task": "room_watch", "status": "running",
               "started_at": EPOCH.isoformat(), "ended_at": None, "duration_s": 120,
               "capture_id": capture_id, "error": None, "exit_code": None,
               "label": "active room", "stop_requested": False,
               "room_watch": live_snapshot, "_started": time.monotonic(),
               "_stop_event": threading.Event()}
        with server.lock:
            server.jobs.append(job)
        active_state = request(server)[1]
        active_ids = {item["id"] for item in active_state["room_inbox"]["items"]}
        assert active_ids == {f"{capture_id}:2", f"{capture_id}:3"}
        assert active_state["captures"] == []
        assert active_state["room_summary"]["capture_count"] == 0
        assert request(server, "POST", f"/api/room-inbox/{capture_id}/2/review",
                       {"reviewed": True})[0] == 200
        with server.lock:
            server.jobs.clear()
        saved_state = request(server)[1]
        assert {item["id"] for item in saved_state["room_inbox"]["items"]} == active_ids
        assert saved_state["room_summary"]["all_time_counts"]["incident_count"] == 2
        assert next(item for item in saved_state["room_inbox"]["items"]
                    if item["sequence"] == 2)["reviewed"] is True


def test_active_change_survives_more_than_fifty_later_events_and_remains_reviewable(tmp_path):
    capture_id = "7" * 32
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=1)
    with running_server(tmp_path) as (server, _):
        job = {"id": capture_id, "task": "room_watch", "status": "running",
               "started_at": EPOCH.isoformat(), "ended_at": None, "duration_s": 120,
               "capture_id": capture_id, "error": None, "exit_code": None,
               "label": "long active room", "stop_requested": False,
               "room_watch": detector.snapshot(), "_started": time.monotonic(),
               "_stop_event": threading.Event()}
        with server.lock:
            server.jobs.append(job)

        def progress(index, *, x, temperature):
            update(detector, index, x=x, temp=temperature)
            with server.lock:
                job["room_watch"] = detector.snapshot()
                server._update_active_room_inbox(job, job["room_watch"])

        for index in range(1, 4):
            progress(index, x=0, temperature=20)
        progress(4, x=100, temperature=20)
        for cycle in range(26):
            progress(5 + cycle * 2, x=100, temperature=23)
            progress(6 + cycle * 2, x=100, temperature=20)

        assert detector.event_count > 50
        assert all(event.get("metric") != "movement_mg"
                   for event in detector.snapshot()["events"])
        state = request(server)[1]
        movement = next(item for item in state["room_inbox"]["items"]
                        if item["metric"] == "movement_mg")
        assert movement["id"] == f"{capture_id}:2"
        assert movement["active"] is True
        assert state["room_inbox"]["counts"]["active_count"] == 1

        status, result, _ = request(
            server, "POST", f"/api/room-inbox/{capture_id}/2/review", {"reviewed": True})
        assert status == 200 and result["item"]["reviewed"] is True
        progress(57, x=0, temperature=20)
        movement = next(item for item in request(server)[1]["room_inbox"]["items"]
                        if item["metric"] == "movement_mg")
        assert movement["active"] is False and movement["reviewed"] is True
        assert movement["duration_s"] == 53.0


def test_inbox_response_is_bounded_but_counts_cover_all_valid_events(tmp_path):
    capture_id = "5" * 32
    _write_capture(tmp_path, capture_id, _many_changes(201))
    with running_server(tmp_path) as (server, _):
        state = request(server)[1]
        assert len(state["room_inbox"]["items"]) == web.MAX_ROOM_INBOX_ITEMS == 200
        assert state["room_inbox"]["counts"] == {
            "total_count": 201, "shown_count": 200, "change_count": 201,
            "health_count": 0, "active_count": 0, "unreviewed_count": 201}
        assert state["captures"][0]["change_count"] == 201
        assert len({item["id"] for item in state["room_inbox"]["items"]}) == 200
        assert state["room_summary"]["all_time_counts"]["incident_count"] == 201
        assert state["room_summary"]["all_time_counts"]["change_count"] == 201


def test_room_summary_uses_full_saved_history_and_exact_time_windows():
    now = EPOCH + timedelta(days=8)
    current = "a" * 32
    recent = "b" * 32
    weekly = "c" * 32
    old = "d" * 32
    future = "e" * 32
    records = [
        _summary_record(current, now - timedelta(hours=2), [
            _summary_item(current, 1, now - timedelta(hours=1), duration_s=30),
            _summary_item(current, 2, now - timedelta(hours=24), metric="temperature_c",
                          duration_s=60, reviewed=True),
        ], state="changed", label="latest changed"),
        _summary_record(recent, now - timedelta(days=2), [
            _summary_item(recent, 1, now - timedelta(hours=24, microseconds=1),
                          metric="temperature_c", duration_s=100),
            _summary_item(recent, 2, now - timedelta(days=2), metric="humidity_percent",
                          active=True),
        ]),
        _summary_record(weekly, now - timedelta(days=6), [
            _summary_item(weekly, 1, now - timedelta(days=6), kind="health", metric=None,
                          duration_s=400),
        ], interrupted=True),
        _summary_record(old, now - timedelta(days=8), [
            _summary_item(old, 1, now - timedelta(days=7, microseconds=1),
                          duration_s=5000),
        ]),
        _summary_record(future, now + timedelta(hours=1), [
            _summary_item(future, 1, now + timedelta(seconds=1), duration_s=9000),
        ]),
    ]

    summary = web._room_summary(records, now=now)

    assert summary["scope"] == "validated_saved_room_captures"
    assert summary["generated_at"] == now.isoformat()
    assert summary["capture_count"] == 5
    assert summary["history_complete"] is False
    assert summary["clock_warning_count"] == 1
    assert summary["all_time_counts"] == {
        "incident_count": 7, "change_count": 6, "health_count": 1,
        "unreviewed_count": 6, "unclosed_count": 1,
        "metric_counts": {"movement_mg": 3, "temperature_c": 2,
                          "humidity_percent": 1, "pressure_pa": 0}}
    latest = summary["latest_saved_observation"]
    assert latest == {"capture_id": future, "label": "summary",
                      "report_url": f"/reports/{future}", "state": "normal",
                      "observed_at": (now + timedelta(seconds=9001)).isoformat(),
                      "started_at": (now + timedelta(hours=1)).isoformat(),
                      "changed_metrics": [], "termination_reason": "stopped",
                      "clock_warning": True}
    day = summary["windows"]["24h"]
    assert day["starts_at"] == (now - timedelta(hours=24)).isoformat()
    assert day["recording_count"] == 2 and day["incident_count"] == 2
    assert day["history_complete"] is True
    assert (day["change_count"], day["health_count"], day["unreviewed_count"],
            day["unclosed_count"]) == (2, 0, 1, 0)
    assert day["metric_counts"] == {"movement_mg": 1, "temperature_c": 1,
                                     "humidity_percent": 0, "pressure_pa": 0}
    assert day["top_metrics"] == [{"metric": "movement_mg", "count": 1},
                                   {"metric": "temperature_c", "count": 1}]
    assert day["longest_closed"]["id"] == f"{current}:2"
    week = summary["windows"]["7d"]
    assert week["recording_count"] == 4
    assert week["history_complete"] is False
    assert (week["incident_count"], week["change_count"], week["health_count"],
            week["unreviewed_count"], week["unclosed_count"]) == (5, 4, 1, 4, 1)
    assert week["metric_counts"] == {"movement_mg": 1, "temperature_c": 2,
                                      "humidity_percent": 1, "pressure_pa": 0}
    assert week["top_metrics"] == [{"metric": "temperature_c", "count": 2}]
    assert week["longest_closed"]["id"] == f"{weekly}:1"
    assert week["longest_closed"]["duration_s"] == 400
    public = json.dumps(summary)
    assert "raw_hex" not in public and "confirmation_samples" not in public


def test_room_summary_distinguishes_no_capture_quiet_and_interrupted_latest():
    now = EPOCH + timedelta(days=1)
    empty = web._room_summary([], now=now)
    assert empty["capture_count"] == 0
    assert empty["latest_saved_observation"] is None
    assert empty["history_complete"] is True
    assert empty["windows"]["24h"]["incident_count"] == 0

    quiet_id = "8" * 32
    interrupted_id = "9" * 32
    records = [
        _summary_record(quiet_id, now - timedelta(hours=2), [], label="quiet"),
        _summary_record(interrupted_id, now - timedelta(hours=1), [], interrupted=True,
                        label="newer interrupted"),
    ]
    summary = web._room_summary(records, now=now)
    assert summary["capture_count"] == 2
    assert summary["windows"]["24h"]["incident_count"] == 0
    assert summary["latest_saved_observation"]["capture_id"] == interrupted_id
    assert summary["latest_saved_observation"]["state"] == "unknown"
    assert summary["latest_saved_observation"]["observed_at"] is None
    assert summary["latest_saved_observation"]["started_at"] == (
        now - timedelta(hours=1)).isoformat()
    assert summary["latest_saved_observation"]["clock_warning"] is False
    assert summary["history_complete"] is False
    assert summary["windows"]["24h"]["history_complete"] is False


def test_room_summary_keeps_future_quiet_capture_visible_with_clock_warning():
    now = EPOCH + timedelta(days=1)
    current_id = "6" * 32
    future_id = "7" * 32
    records = [
        _summary_record(current_id, now - timedelta(hours=1), [], label="current quiet"),
        _summary_record(future_id, now + timedelta(minutes=1), [], label="future quiet"),
    ]

    summary = web._room_summary(records, now=now)

    assert summary["capture_count"] == 2
    assert summary["clock_warning_count"] == 1
    latest = summary["latest_saved_observation"]
    assert latest["capture_id"] == future_id
    assert latest["state"] == "normal"
    assert latest["observed_at"] == (now + timedelta(minutes=1, seconds=1)).isoformat()
    assert latest["started_at"] == (now + timedelta(minutes=1)).isoformat()
    assert latest["clock_warning"] is True
    assert summary["windows"]["24h"]["recording_count"] == 1


def test_room_summary_excludes_future_recovery_from_longest_closed():
    now = EPOCH + timedelta(days=1)
    capture_id = "5" * 32
    future_recovery = _summary_item(
        capture_id, 1, now - timedelta(minutes=1), duration_s=120)
    record = _summary_record(capture_id, now - timedelta(hours=1), [future_recovery])

    summary = web._room_summary([record], now=now)

    assert summary["clock_warning_count"] == 1
    assert summary["all_time_counts"]["unclosed_count"] == 1
    assert summary["windows"]["24h"]["incident_count"] == 1
    assert summary["windows"]["24h"]["unclosed_count"] == 1
    assert summary["windows"]["24h"]["longest_closed"] is None
    assert summary["latest_saved_observation"]["clock_warning"] is True


def test_room_summary_window_completeness_ignores_old_interrupted_capture():
    now = EPOCH + timedelta(days=20)
    old_id = "3" * 32
    recent_id = "4" * 32
    records = [
        _summary_record(old_id, now - timedelta(days=8), [], interrupted=True,
                        label="old interrupted"),
        _summary_record(recent_id, now - timedelta(hours=1), [], label="recent quiet"),
    ]

    summary = web._room_summary(records, now=now)

    assert summary["history_complete"] is False
    assert summary["windows"]["24h"]["history_complete"] is True
    assert summary["windows"]["7d"]["history_complete"] is True


def test_room_summary_marks_window_when_interrupted_session_could_overlap_it():
    now = EPOCH + timedelta(days=20)
    capture_id = "2" * 32
    record = _summary_record(capture_id, now - timedelta(hours=25), [], interrupted=True)
    record["session"]["duration_s"] = 2 * 60 * 60

    summary = web._room_summary([record], now=now)

    assert summary["windows"]["24h"]["recording_count"] == 0
    assert summary["windows"]["24h"]["incident_count"] == 0
    assert summary["windows"]["24h"]["history_complete"] is False


@pytest.mark.parametrize("document", [
    {"version": 1, "reviewed": [f"{'6' * 32}:{index}" for index in range(1, 4098)]},
    {"version": 1, "reviewed": [f"{'6' * 32}:1", f"{'6' * 32}:1"]},
    {"version": 1, "reviewed": [[]]},
    {"version": True, "reviewed": []},
    {"version": 2, "reviewed": []},
])
def test_review_registry_rejects_unbounded_duplicate_or_unknown_state(tmp_path, document):
    (tmp_path / ".room-reviews.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="room review state"):
        web.make_server(port=0, data_dir=tmp_path, adapter=FakeAdapter())


def test_review_registry_has_a_hard_byte_limit_before_json_loading(tmp_path):
    path = tmp_path / ".room-reviews.json"
    with path.open("wb") as stream:
        stream.truncate(web.MAX_ROOM_REVIEW_BYTES + 1)
    with pytest.raises(ValueError, match="size limit"):
        web.make_server(port=0, data_dir=tmp_path, adapter=FakeAdapter())


def test_review_registry_rejects_a_symlink(tmp_path):
    target = tmp_path / "outside.json"
    target.write_text('{"version":1,"reviewed":[]}', encoding="utf-8")
    (tmp_path / ".room-reviews.json").symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        web.make_server(port=0, data_dir=tmp_path, adapter=FakeAdapter())
