"""The room job shares USB exclusion and exposes cancelable, persistent evidence."""
import json
from pathlib import Path
import sys
import threading
import time

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
from cuberange.flatsat import room_watch as room
from cuberange.flatsat import web
from test_flatsat_web import FakeAdapter, request, running_server, wait_job
from test_flatsat_room_watch import EPOCH, sample, update


class FakeRoomAdapter(FakeAdapter):
    def __init__(self, *, wait_stop=True, release=None, failure=False):
        super().__init__()
        self.wait_stop, self.release, self.failure = wait_stop, release, failure
        self.stop_seen = threading.Event()
        self.room_calls = []

    def run_room_watch(self, request_body, output, stop_event, progress):
        self.room_calls.append(request_body)
        detector = room.RoomDetector(baseline_samples=request_body['baseline_samples'],
                                     confirm_samples=request_body['confirm_samples'],
                                     thresholds=request_body['thresholds'])
        records = [{'event': 'session', 'time_utc': EPOCH.isoformat(), 'mode': 'room_watch',
                    'ports': [], 'label': request_body['label'], 'duration_s': request_body['duration'],
                    'interval_s': request_body['interval'], 'timeout_s': request_body['timeout'],
                    'baseline_samples': request_body['baseline_samples'],
                    'confirm_samples': request_body['confirm_samples'], 'thresholds': detector.thresholds,
                    'baseline_method': 'component_median'}]
        for index in range(1, request_body['baseline_samples'] + 1):
            for event in update(detector, index):
                records.append({'event': 'room_event', **event})
        output.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
        progress(detector.snapshot())
        self.opened.set()
        try:
            if self.wait_stop:
                assert stop_event.wait(5), 'room stop was not requested by test'
                self.stop_seen.set()
                if self.release:
                    assert self.release.wait(5), 'test did not release fake USB close'
            reason = 'stopped' if stop_event.is_set() else 'duration_complete'
            if self.failure:
                reason = 'response_timeout'
                missing, _ = sample(request_body['baseline_samples'] + 1)
                missing.update(status='timeout', completion='timeout')
                records.extend({'event': 'room_event', **event} for event in detector.update(missing))
            detector.stop(reason)
            records.append({'event': 'end', 'time_utc': detector.last_sample['time_utc'],
                            'reason': reason, 'room_watch': detector.snapshot(include_events=False)})
            output.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
            progress(detector.snapshot())
            return 1 if self.failure else 0
        finally:
            self.closed.set()


@pytest.mark.parametrize('extra', [{'duration': True}, {'duration': 86400.1}, {'interval': .199},
                                   {'interval': 60.1}, {'timeout': 5.1}, {'baseline_samples': 2},
                                   {'baseline_samples': 121}, {'baseline_samples': 3.0},
                                   {'confirm_samples': True}, {'confirm_samples': 11},
                                   {'thresholds': {'temperature_c': 0}}, {'thresholds': {'movement_mg': False}},
                                   {'thresholds': {'accel_x_mg': 1}}, {'thresholds': []},
                                   {'notes': ['not allowed']}, {'limits': []}, {'port': 'radio0'}])
def test_room_job_rejects_unsafe_configuration_before_any_usb_reservation(tmp_path, extra):
    with running_server(tmp_path, FakeRoomAdapter()) as (server, adapter):
        status, error, _ = request(server, 'POST', '/api/jobs', {'task': 'room_watch', **extra})
        assert status == 400 and error['error']
        assert not adapter.room_calls and not adapter.calls and not list(tmp_path.iterdir())


def test_room_defaults_stop_reservation_progress_capture_and_completed_stop_are_consistent(tmp_path):
    release = threading.Event()
    adapter = FakeRoomAdapter(release=release)
    with running_server(tmp_path, adapter) as (server, _):
        try:
            status, created, _ = request(server, 'POST', '/api/jobs', {'task': 'room_watch', 'baseline_samples': 3})
            assert status == 202 and adapter.opened.wait(1)
            job_id = created['job']['id']
            body = adapter.room_calls[0]
            assert body['duration'] == 3600 and body['interval'] == 1 and body['confirm_samples'] == 3
            state = request(server)[1]
            assert state['captures'] == []
            snapshot = state['active_job']['room_watch']
            assert snapshot['phase'] == 'monitoring' and snapshot['baseline_count'] == 3
            assert snapshot['observed_count'] == snapshot['valid_count'] == 3
            assert snapshot['event_count'] == 1 and 'raw_hex' not in snapshot['events'][0]
            assert not state['active_job']['stop_requested']
            for body, expected in (({'task': 'info'}, 409), ({'task': 'watch'}, 409), ({'task': 'monitor'}, 409)):
                assert request(server, 'POST', '/api/jobs', body)[0] == expected
            assert request(server, 'POST', f'/api/jobs/{job_id}/stop', {'unexpected': True})[0] == 400
            assert request(server, 'POST', f'/api/jobs/{"f" * 32}/stop', {})[0] == 404
            assert request(server, 'POST', f'/api/jobs/{job_id}/stop', {}, {'X-CubeRange-Token': 'wrong'})[0] == 403
            status, stopped, _ = request(server, 'POST', f'/api/jobs/{job_id}/stop', {})
            assert status == 202 and stopped['job']['stop_requested']
            assert adapter.stop_seen.wait(1) and not adapter.closed.is_set()
            assert request(server, 'POST', '/api/jobs', {'task': 'info'})[0] == 409
            release.set()
            state = wait_job(server)
            job = state['recent_jobs'][0]
            assert adapter.closed.is_set() and job['status'] == 'completed'
            assert job['room_watch']['phase'] == 'stopped' and job['room_watch']['reason'] == 'stopped'
            capture = state['captures'][0]
            assert capture['mode'] == 'room_watch' and capture['event_count'] == 1
            status, detail, _ = request(server, path=f'/api/captures/{job_id}')
            assert status == 200 and detail['summary']['sample_count'] == 0
            assert detail['room_watch']['snapshot']['observed_count'] == 3
            assert 'baseline_samples' not in detail['room_watch']['events'][0]
            status, raw, _ = request(server, path=detail['raw_url'])
            assert status == 200 and b'"raw_hex"' in raw and b'"baseline_samples"' in raw
            assert request(server, path=detail['report_url'])[0] == 200
            status, repeated, _ = request(server, 'POST', f'/api/jobs/{job_id}/stop', {})
            assert status == 200 and repeated['job']['status'] == 'completed'
            assert request(server, 'POST', '/api/jobs', {'task': 'info'})[0] == 202
            assert wait_job(server)['recent_jobs'][0]['status'] == 'completed'
        finally:
            release.set()
            for job in server.jobs:
                if '_stop_event' in job: job['_stop_event'].set()


def test_room_shutdown_requests_stop_and_joins_until_usb_is_really_closed(tmp_path):
    release = threading.Event()
    adapter = FakeRoomAdapter(release=release)
    with running_server(tmp_path, adapter) as (server, _):
        try:
            assert request(server, 'POST', '/api/jobs', {'task': 'room_watch', 'baseline_samples': 3})[0] == 202
            assert adapter.opened.wait(1)
            closer = threading.Thread(target=server.server_close)
            closer.start()
            assert adapter.stop_seen.wait(1)
            assert closer.is_alive() and not adapter.closed.is_set()
            assert not web._USB_LOCK.acquire(blocking=False)
            release.set()
            closer.join(2)
            assert not closer.is_alive() and adapter.closed.is_set()
            assert server.jobs[0]['status'] == 'completed'
            assert server.jobs[0]['room_watch']['reason'] == 'stopped'
        finally:
            release.set()
            for job in server.jobs:
                if '_stop_event' in job: job['_stop_event'].set()


def test_room_timeout_is_a_failed_visible_recording_and_ordinary_jobs_cannot_be_stopped(tmp_path):
    with running_server(tmp_path, FakeRoomAdapter(wait_stop=False, failure=True)) as (server, _):
        assert request(server, 'POST', '/api/jobs', {'task': 'room_watch', 'baseline_samples': 3})[0] == 202
        state = wait_job(server)
        assert state['recent_jobs'][0]['status'] == 'failed'
        assert 'response_timeout' in state['recent_jobs'][0]['error']
        assert state['recent_jobs'][0]['room_watch']['reason'] == 'response_timeout'
        capture_id = state['captures'][0]['id']
        assert request(server, path=f'/api/captures/{capture_id}')[1]['room_watch']['snapshot']['failed_count'] == 1
        assert request(server, 'POST', '/api/jobs', {'task': 'info'})[0] == 202
        state = wait_job(server)
        assert request(server, 'POST', f'/api/jobs/{state["recent_jobs"][0]["id"]}/stop', {})[0] == 409


def test_edited_room_import_metadata_is_a_4xx_and_interrupted_events_remain_available(tmp_path):
    from test_flatsat_room_watch import room_records
    with running_server(tmp_path, FakeRoomAdapter(wait_stop=False)) as (server, _):
        for edit in ('thresholds', 'sample_index', 'values', 'states', 'unknown_reason'):
            records = room_records()
            last = records[-1]['room_watch']
            if edit == 'thresholds': last['thresholds'] = None
            elif edit == 'sample_index': last['last_sample']['index'] = True
            elif edit == 'values': del last['last_sample']['values']['pressure_pa']
            elif edit == 'states': last['states']['movement_mg'] = 'changed'
            elif edit == 'unknown_reason': last['reason'] = 'unknown'
            content = ''.join(json.dumps(record) + '\n' for record in records)
            status, error, _ = request(server, 'POST', '/api/captures/import', {'content': content})
            assert status == 400 and error['error']
            assert request(server)[1]['captures'] == []
        content = ''.join(json.dumps(record) + '\n' for record in room_records()[:-1])
        status, result, _ = request(server, 'POST', '/api/captures/import', {'content': content})
        assert status == 201
        detail = request(server, path=f'/api/captures/{result["capture"]["id"]}')[1]
        assert detail['room_watch']['snapshot']['reason'] == 'interrupted'
        assert detail['room_watch']['snapshot']['valid_count'] is None
        assert len(detail['room_watch']['events']) == 3
        assert all('confirmation_samples' not in event for event in detail['room_watch']['events'])
        raw = request(server, path=detail['raw_url'])[1]
        assert b'"confirmation_samples"' in raw
