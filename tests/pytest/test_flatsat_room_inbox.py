"""Room inbox items are derived from validated evidence and reviewed separately."""
from __future__ import annotations

from datetime import timedelta
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
        assert request(server, "POST", f"/api/room-inbox/{capture_id}/2/review",
                       {"reviewed": True})[0] == 200
        with server.lock:
            server.jobs.clear()
        saved_state = request(server)[1]
        assert {item["id"] for item in saved_state["room_inbox"]["items"]} == active_ids
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
