"""Room decisions and sparse USB evidence, without a physical USB device."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
from cuberange.flatsat import room_watch as room
from cuberange.flatsat import __main__ as cli
from cuberange.flatsat.usb import FlatSatError, FlatSatPort

EPOCH = datetime(2026, 10, 1, tzinfo=timezone.utc)
PORT = FlatSatPort('/dev/fake-room', 'shell', 'TEST-ROOM', 'Cat-Shell', '1-1', True)


def response(*, x=0, y=0, z=1000, temp=20, humidity=50, pressure=100000):
    return f'sensors\r\nAccel: x={x} mg y={y} mg z={z} mg\r\nTemp: {temp} C\r\nPress: {pressure} Pa\r\nHumid: {humidity}%\r\n\r\n'


def sample(index, **values):
    text = response(**values)
    return {'time_utc': (EPOCH + timedelta(seconds=index)).isoformat(), 'elapsed_s': float(index),
            'response_ms': 1.0, 'completion': 'complete', **cli._sensor_reply(text)}, text.encode().hex()


def update(detector, index, **values):
    reading, raw = sample(index, **values)
    return detector.update(reading, raw_hex=raw)


def calibrated(confirm_samples=3, thresholds=None):
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=confirm_samples, thresholds=thresholds)
    assert update(detector, 1) == [] and update(detector, 2) == []
    assert update(detector, 3)[0]['transition'] == 'baseline_ready'
    return detector


def test_fixed_vector_baseline_detects_tilt_even_when_total_acceleration_is_unchanged():
    detector = calibrated()
    for index in (4, 5):
        assert update(detector, index, x=600, z=800) == []
    events = update(detector, 6, x=600, z=800)
    assert [(event['transition'], event['metric']) for event in events] == [('changed', 'movement_mg')]
    assert detector.last_sample['values']['acceleration_norm_mg'] == 1000
    assert detector.last_sample['deviations']['movement_mg'] == pytest.approx(632.455532)
    assert detector.baseline['accel_x_mg'] == 0  # No drift-following recalibration.
    assert update(detector, 7, x=600, z=800) == []


def test_environment_changes_are_independent_and_recovery_uses_hysteresis_confirmation():
    detector = calibrated(thresholds={'temperature_c': 2, 'humidity_percent': 10, 'pressure_pa': 500})
    for index in (4, 5):
        assert update(detector, index, temp=23, humidity=61, pressure=100501) == []
    events = update(detector, 6, temp=23, humidity=61, pressure=100501)
    assert {event['metric'] for event in events} == {'temperature_c', 'humidity_percent', 'pressure_pa'}
    assert all(event['transition'] == 'changed' for event in events)
    for index in (7, 8, 9):
        assert update(detector, index, temp=21.5, humidity=57, pressure=100350) == []
    assert all(detector.states[key] == 'changed' for key in ('temperature_c', 'humidity_percent', 'pressure_pa'))
    assert update(detector, 10) == [] and update(detector, 11) == []
    events = update(detector, 12)
    assert len(events) == 3 and all(event['transition'] == 'recovered' for event in events)


def test_single_threshold_crossings_do_not_accumulate():
    detector = calibrated()
    assert update(detector, 4, x=80) == []
    assert update(detector, 5, x=81) == []
    assert update(detector, 6, x=0) == []
    assert update(detector, 7, x=81) == []
    assert update(detector, 8, x=81) == []
    assert update(detector, 9, x=0) == []
    assert detector.states['movement_mg'] == 'normal'


def test_threshold_equality_confirms_with_the_complete_evidence_window():
    detector = calibrated()
    assert update(detector, 4, x=80) == []
    assert update(detector, 5, x=80) == []
    event = update(detector, 6, x=80)[0]
    assert event['transition'] == 'changed' and event['value'] == 80
    assert [item['index'] for item in event['confirmation_samples']] == [4, 5, 6]
    assert all(item['raw_hex'] for item in event['confirmation_samples'])
    assert 'confirmation_samples' not in detector.snapshot()['events'][-1]


def test_missing_data_clears_counters_preserves_changed_state_and_resumes_without_false_recovery():
    detector = calibrated()
    for index in (4, 5, 6):
        update(detector, index, x=100)
    assert detector.states['movement_mg'] == 'changed'
    assert update(detector, 7) == []
    missing, _ = sample(8)
    missing.update(status='timeout', completion='timeout')
    assert detector.update(missing)[0]['transition'] == 'unavailable'
    assert detector.update({**missing, 'elapsed_s': 9.0}) == []
    assert detector.phase == 'unavailable'
    assert update(detector, 10)[0]['transition'] == 'resumed'
    assert detector.states['movement_mg'] == 'changed'
    assert update(detector, 11) == []
    assert update(detector, 12)[0]['transition'] == 'recovered'
    assert detector.failed_count == 2 and detector.observed_count == 12
    assert [item['index'] for item in detector.events[-1]['confirmation_samples']] == [10, 11, 12]


def test_calibration_counts_only_valid_samples_and_never_announces_changes_before_ready():
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=1)
    assert update(detector, 1, x=500) == []
    missing, _ = sample(2)
    missing.update(status='timeout', completion='timeout')
    assert detector.update(missing)[0]['transition'] == 'unavailable'
    assert update(detector, 3, x=500)[0]['transition'] == 'resumed'
    events = update(detector, 4, x=500)
    assert [event['transition'] for event in events] == ['baseline_ready']
    assert detector.baseline['accel_x_mg'] == 500


def test_equal_and_backward_query_timing_interrupts_confirmation():
    detector = calibrated()
    assert update(detector, 4, x=100) == []
    reading, raw = sample(4, x=100)
    assert detector.update(reading, raw_hex=raw)[0]['transition'] == 'unavailable'
    reading, raw = sample(3, x=100)
    assert detector.update(reading, raw_hex=raw) == []
    assert update(detector, 5, x=100)[0]['transition'] == 'resumed'
    assert update(detector, 6, x=100) == []
    assert update(detector, 7, x=100)[0]['transition'] == 'changed'


def test_even_median_remains_finite_and_deviation_overflow_becomes_unavailable():
    detector = room.RoomDetector(baseline_samples=4, confirm_samples=1)
    for index in range(1, 5):
        update(detector, index, x=1e308)
    assert detector.baseline['accel_x_mg'] == 1e308
    assert detector.phase == 'monitoring'
    events = update(detector, 5, x=-1e308)
    assert events[0]['transition'] == 'unavailable'
    assert detector.last_sample['status'] == 'invalid'
    assert detector.failed_count == 1
    assert room._median([1e308, 1e308]) == 1e308


def test_long_query_gap_or_missing_timing_cannot_complete_a_confirmation():
    detector = calibrated()
    assert update(detector, 4, x=100) == []
    events = update(detector, 100, x=100)
    assert events[0]['transition'] == 'unavailable'
    assert update(detector, 101, x=100)[0]['transition'] == 'resumed'
    reading, raw = sample(102, x=100)
    reading['elapsed_s'] = None
    assert detector.update(reading, raw_hex=raw)[0]['transition'] == 'unavailable'
    assert detector.states['movement_mg'] == 'unavailable'


def test_deviation_overflow_during_baseline_ready_cannot_leave_a_false_ready_baseline():
    detector = room.RoomDetector(baseline_samples=3)
    update(detector, 1, x=-1e308)
    update(detector, 2, x=-1e308)
    events = update(detector, 3, x=1e308)
    assert events[0]['transition'] == 'unavailable'
    assert detector.baseline is None and len(detector.seed) == 2
    assert detector.valid_count == 2 and detector.failed_count == 1


@pytest.mark.parametrize('config', [{'baseline_samples': True}, {'baseline_samples': 2},
                                    {'baseline_samples': 121}, {'confirm_samples': 0},
                                    {'confirm_samples': 11}, {'thresholds': {'unknown': 1}},
                                    {'thresholds': {'movement_mg': 0}}, {'thresholds': {'movement_mg': True}},
                                    {'thresholds': {'temperature_c': float('inf')}}])
def test_detector_rejects_invalid_configuration(config):
    with pytest.raises(ValueError):
        room.RoomDetector(**config)


def room_records(*, stop_reason='stopped'):
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=2)
    records = [{'event': 'session', 'time_utc': EPOCH.isoformat(), 'mode': 'room_watch', 'label': 'room',
                'duration_s': 120, 'interval_s': 1, 'timeout_s': .3, 'baseline_samples': 3,
                'confirm_samples': 2, 'thresholds': detector.thresholds, 'baseline_method': 'component_median'}]
    for index in range(1, 8):
        for event in update(detector, index, x=100 if 4 <= index <= 5 else 0):
            records.append({'event': 'room_event', **event})
    detector.stop(stop_reason)
    records.append({'event': 'end', 'time_utc': (EPOCH + timedelta(seconds=8)).isoformat(),
                    'reason': stop_reason, 'room_watch': detector.snapshot(include_events=False)})
    return records


def write_records(tmp_path, records):
    path = tmp_path / 'room.jsonl'
    path.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
    return path


def test_sparse_log_loader_validates_raw_baseline_events_and_compacts_api_snapshots(tmp_path):
    data = room.load_room_capture(write_records(tmp_path, room_records()))
    assert [event['transition'] for event in data['events']] == ['baseline_ready', 'changed', 'recovered']
    assert data['snapshot']['observed_count'] == 7
    assert data['snapshot']['event_count'] == 3
    assert data['snapshot']['phase'] == 'stopped'
    assert 'raw_hex' in data['events'][0] and 'baseline_samples' in data['events'][0]
    assert all('raw_hex' not in event and 'baseline_samples' not in event and 'confirmation_samples' not in event
               for event in data['snapshot']['events'])
    assert [item['index'] for item in data['events'][1]['confirmation_samples']] == [4, 5]
    assert [item['index'] for item in data['events'][2]['confirmation_samples']] == [6, 7]


@pytest.mark.parametrize('field', ['index', 'elapsed_s', 'time_utc'])
def test_heartbeat_observation_frontier_rejects_a_later_record_of_an_older_event(tmp_path, field):
    records = room_records()
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=2)
    for index in range(1, 7 if field == 'index' else 5):
        update(detector, index)
    snapshot = detector.snapshot(include_events=False)
    if field == 'elapsed_s': snapshot['last_sample']['elapsed_s'] = 6.0
    elif field == 'time_utc': snapshot['last_sample']['time_utc'] = (EPOCH + timedelta(seconds=6)).isoformat()
    heartbeat = {'event': 'room_heartbeat', 'room_watch': snapshot, 'raw_hex': response().encode().hex()}
    records.insert(2, heartbeat)
    with pytest.raises(ValueError, match='room event ordering'):
        room.load_room_capture(write_records(tmp_path, records))


def test_heartbeat_frontier_rejects_an_event_from_the_same_observation(tmp_path):
    records = room_records()
    detector = room.RoomDetector(baseline_samples=3, confirm_samples=2)
    for index in range(1, 4):
        update(detector, index)
    update(detector, 4, x=100)
    update(detector, 5, x=100)
    snapshot = detector.snapshot(include_events=False)
    snapshot['event_count'] = 1
    snapshot['states']['movement_mg'] = 'normal'
    heartbeat = {'event': 'room_heartbeat', 'room_watch': snapshot,
                 'raw_hex': response(x=100).encode().hex()}
    records.insert(2, heartbeat)
    with pytest.raises(ValueError, match='room event ordering'):
        room.load_room_capture(write_records(tmp_path, records))


def test_baseline_proof_rejects_a_boolean_even_when_equal_to_raw_numeric_one(tmp_path):
    records = room_records()
    seed = records[1]['baseline_samples'][0]
    seed['values']['accel_x_mg'] = True
    seed['raw_hex'] = response(x=1).encode().hex()
    with pytest.raises(ValueError, match='finite number'):
        room.load_room_capture(write_records(tmp_path, records))


def test_detector_and_loader_reject_an_ok_sample_without_a_completed_reply(tmp_path):
    detector = room.RoomDetector(baseline_samples=3)
    reading, raw = sample(1)
    reading['completion'] = 'error'
    event = detector.update(reading, raw_hex=raw)[0]
    assert event['transition'] == 'unavailable' and event['status'] == 'invalid'

    records = room_records()
    records[1]['baseline_samples'][0]['completion'] = 'error'
    with pytest.raises(ValueError, match='completed reply'):
        room.load_room_capture(write_records(tmp_path, records))

    records = room_records()
    records[2]['completion'] = 'error'
    with pytest.raises(ValueError, match='completed reply'):
        room.load_room_capture(write_records(tmp_path, records))


@pytest.mark.parametrize('mutation', ['missing', 'count', 'gap', 'equal_time', 'reverse_elapsed',
                                      'bool_value', 'raw', 'below_threshold', 'tail', 'recovery_threshold'])
def test_transition_loader_rechecks_every_confirmation_sample(tmp_path, mutation):
    records = room_records()
    event = records[2] if mutation != 'recovery_threshold' else records[3]
    items = event['confirmation_samples']
    if mutation == 'missing': del event['confirmation_samples']
    elif mutation == 'count': items.pop(0)
    elif mutation == 'gap': items[0]['index'] = 3
    elif mutation == 'equal_time': items[0]['time_utc'] = items[-1]['time_utc']
    elif mutation == 'reverse_elapsed': items[0]['elapsed_s'] = 999
    elif mutation == 'bool_value': items[0]['values']['accel_y_mg'] = False
    elif mutation == 'raw': items[0]['raw_hex'] = '00'
    elif mutation in ('below_threshold', 'recovery_threshold', 'tail'):
        item = items[-1] if mutation == 'tail' else items[0]
        x = 101 if mutation == 'tail' else 0 if mutation == 'below_threshold' else 100
        reading, raw = sample(item['index'], x=x)
        item['values'], item['raw_hex'] = reading['values'], raw
    with pytest.raises(ValueError):
        room.load_room_capture(write_records(tmp_path, records))


def test_confirmation_cannot_reuse_a_sample_before_a_missing_data_boundary():
    detector = calibrated()
    assert update(detector, 4, x=100) == []
    missing, _ = sample(5)
    missing.update(status='timeout', completion='timeout')
    assert detector.update(missing)[0]['transition'] == 'unavailable'
    assert update(detector, 6, x=100)[0]['transition'] == 'resumed'
    assert update(detector, 7, x=100) == []
    event = update(detector, 8, x=100)[0]
    assert [item['index'] for item in event['confirmation_samples']] == [6, 7, 8]


@pytest.mark.parametrize('mutation', ['raw', 'baseline', 'state', 'counter', 'last_values', 'duration',
                                      'seed_index', 'event_index', 'end_after', 'duplicate_end', 'reason'])
def test_sparse_loader_rejects_edited_metadata_and_bad_transition_evidence(tmp_path, mutation):
    records = room_records()
    if mutation == 'raw': records[2]['raw_hex'] = 'zz'
    elif mutation == 'baseline': records[1]['baseline']['accel_x_mg'] = 20
    elif mutation == 'state': records[-1]['room_watch']['states']['movement_mg'] = 'changed'
    elif mutation == 'counter': records[-1]['room_watch']['valid_count'] = 999
    elif mutation == 'last_values': del records[-1]['room_watch']['last_sample']['values']['pressure_pa']
    elif mutation == 'duration': records[0]['duration_s'] = 999999
    elif mutation == 'seed_index': records[1]['baseline_samples'][1]['index'] = 1
    elif mutation == 'event_index': records[2]['index'] = 1
    elif mutation == 'end_after': records.append({'event': 'error', 'message': 'after end'})
    elif mutation == 'duplicate_end': records.insert(-1, dict(records[-1]))
    elif mutation == 'reason': records[-1]['reason'] = 'invented'
    with pytest.raises(ValueError):
        room.load_room_capture(write_records(tmp_path, records))


def test_endless_interrupted_sparse_log_keeps_events_and_marks_totals_unknown(tmp_path):
    records = room_records()[:-1]
    data = room.load_room_capture(write_records(tmp_path, records))
    assert data['snapshot']['phase'] == 'stopped' and data['snapshot']['reason'] == 'interrupted'
    assert data['snapshot']['observed_count'] is None and data['snapshot']['valid_count'] is None
    assert data['snapshot']['counts_complete'] is False
    assert data['snapshot']['event_count'] == 3 and len(data['events']) == 3


class FakeStop:
    def __init__(self, clock): self.clock, self.stopped = clock, False
    def is_set(self): return self.stopped
    def set(self): self.stopped = True
    def wait(self, seconds): self.clock.now += seconds; return self.stopped


class FakeSerial:
    closed = opened = False
    def __init__(self, port): self.port = port
    def __enter__(self): type(self).opened = True; return self
    def __exit__(self, *_): type(self).closed = True


def runner(tmp_path, monkeypatch, *, seconds=70, failure=None, max_bytes=room.MAX_LOG_BYTES, stop_after=None):
    clock = SimpleNamespace(now=0.0)
    stop = FakeStop(clock)
    FakeSerial.opened = FakeSerial.closed = False
    calls = []
    snapshots = []
    monkeypatch.setattr(room, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(room, 'SerialSession', FakeSerial)
    monkeypatch.setattr(cli, '_drain', lambda *args: None)
    def query(link, evidence, command, timeout):
        assert command == 'sensors'
        calls.append(command)
        if failure == 'device' and len(calls) == 4: raise FlatSatError('USB disconnected')
        clock.now += .001
        text = response(x=100 if 4 <= len(calls) <= 6 else 0)
        evidence.event('rx', raw_hex=text.encode().hex())
        parsed = cli._sensor_reply(text)
        completion = 'timeout' if failure == 'timeout' else 'complete'
        if completion == 'timeout': parsed['status'] = 'timeout'
        return {'parsed': parsed, 'completion': completion, 'response_ms': 1, 'answered': completion != 'timeout'}
    monkeypatch.setattr(cli, '_query', query)
    def progress(snapshot):
        snapshots.append(snapshot)
        if stop_after and snapshot['observed_count'] >= stop_after: stop.set()
    path = tmp_path / 'runner.jsonl'
    code = room.run_room_watch(PORT, seconds, 1, .3, path, baseline_samples=3, confirm_samples=3,
                               stop_event=stop, progress=progress, max_bytes=max_bytes)
    return code, room.load_room_capture(path), path, calls, snapshots


def test_runner_logs_events_and_minute_heartbeats_without_per_poll_records(tmp_path, monkeypatch):
    code, data, path, calls, snapshots = runner(tmp_path, monkeypatch)
    assert code == 0 and FakeSerial.closed
    assert len(calls) == data['snapshot']['observed_count'] >= 69
    kinds = [json.loads(line)['event'] for line in path.read_text().splitlines()]
    assert set(kinds) == {'session', 'room_event', 'room_heartbeat', 'end'}
    assert kinds.count('room_heartbeat') == 1 and len(kinds) < 10
    assert data['snapshot']['reason'] == 'duration_complete'
    assert len(json.dumps(snapshots[-1])) < 16384


@pytest.mark.parametrize(('failure', 'reason'), [('timeout', 'response_timeout'), ('device', 'device_error')])
def test_runner_stops_after_timeout_or_disconnect_and_closes_usb(tmp_path, monkeypatch, failure, reason):
    code, data, _, calls, _ = runner(tmp_path, monkeypatch, failure=failure)
    assert code == 1 and FakeSerial.closed and data['snapshot']['reason'] == reason
    assert any(event['transition'] == 'unavailable' for event in data['events'])
    if failure == 'timeout': assert len(calls) == 1


def test_requested_stop_finishes_cleanly_without_an_extra_query(tmp_path, monkeypatch):
    code, data, _, calls, _ = runner(tmp_path, monkeypatch, seconds=3600, stop_after=5)
    assert code == 0 and FakeSerial.closed and len(calls) == 5
    assert data['snapshot']['reason'] == 'stopped'


def test_log_limit_stops_before_partial_event_and_retains_a_readable_end(tmp_path, monkeypatch):
    code, data, path, _, _ = runner(tmp_path, monkeypatch, max_bytes=room._END_RESERVE + 1000)
    assert code == 1 and data['snapshot']['reason'] == 'size_limit' and FakeSerial.closed
    assert path.stat().st_size <= room._END_RESERVE + 1000
    assert data['snapshot']['event_count'] == len(data['events']) == 0


def test_size_limit_during_confirmation_batch_keeps_only_persisted_events(tmp_path, monkeypatch):
    original = room.RoomCapture.room_events
    def reduce_budget_after_baseline(capture, events):
        original(capture, events)
        if any(event['transition'] == 'baseline_ready' for event in events):
            capture.max_bytes = capture.written + room._END_RESERVE + 500
    monkeypatch.setattr(room.RoomCapture, 'room_events', reduce_budget_after_baseline)
    code, data, _, _, _ = runner(tmp_path, monkeypatch)
    assert code == 1 and data['snapshot']['reason'] == 'size_limit'
    assert [event['transition'] for event in data['events']] == ['baseline_ready']
    assert data['snapshot']['event_count'] == 1 and data['snapshot']['states']['movement_mg'] == 'normal'
