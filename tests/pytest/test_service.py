"""The range service layer: lifecycle and verification results, with fakes.

No Renode here. The supervisor factory and the verify runner are injected fakes, which is
the point of the service's shape: what is under test is the state machine and the mapping
from pytest outcomes to the four Outcome values - in particular that "everything skipped"
reads as NOT_RUN (prerequisites missing) and never as PASSED, and that asking for the
hardware target is NOT_APPLICABLE rather than a failure.
"""
import contextlib
from pathlib import Path

import pytest

from cuberange.service import Outcome, RangeService, SessionState


ENTRY = {"id": "EX-T01", "dir": "EX-T01-fake", "title": "fake", "scenario": "scenario.resc"}


class FakeRun:
    def __init__(self):
        self.stopped = False


class FakeFactory:
    """Stands in for RenodeSupervisor.launch: records argv, yields a handle."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, log_path):
        self.calls.append((list(argv), Path(log_path)))
        run = FakeRun()

        @contextlib.contextmanager
        def ctx():
            try:
                yield run
            finally:
                run.stopped = True

        return ctx()


def make_service(tmp_path, runner=None, with_verify=True):
    exercises = tmp_path / "exercises"
    ex_dir = exercises / ENTRY["dir"]
    ex_dir.mkdir(parents=True)
    if with_verify:
        (ex_dir / "verify_ex_t01.py").write_text("# fake\n")
    factory = FakeFactory()
    service = RangeService(
        out=tmp_path / "out", exercises_dir=exercises,
        supervisor_factory=factory,
        catalog_get=lambda exercise_id: ENTRY if exercise_id == "EX-T01" else None,
        runner=runner or (lambda verify_file: (0, "=== 3 passed in 1.0s ===")))
    return service, factory


# --------------------------------------------------------------------------- lifecycle

def test_start_status_stop(tmp_path):
    service, factory = make_service(tmp_path)
    assert service.status().state is SessionState.STOPPED

    status = service.start("EX-T01")
    assert status.state is SessionState.RUNNING
    assert status.exercise_id == "EX-T01"
    argv, log_path = factory.calls[0]
    assert "scenario.resc" in " ".join(argv) and log_path.name.endswith("-renode.log")

    with pytest.raises(RuntimeError):
        service.start("EX-T01")

    assert service.stop().state is SessionState.STOPPED
    assert service.stop().state is SessionState.STOPPED  # idempotent


def test_start_unknown_exercise_raises(tmp_path):
    service, _ = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.start("EX-NOPE")


def test_recover_stops_and_keeps_logs(tmp_path):
    service, _ = make_service(tmp_path)
    service.start("EX-T01")
    log_path = service.status().log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("evidence\n")
    assert service.recover().state is SessionState.STOPPED
    assert log_path.read_text() == "evidence\n"


def test_log_tail_reads_log_and_consoles(tmp_path):
    service, _ = make_service(tmp_path)
    service.start("EX-T01")
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    service.status().log_path.write_text("line1\nline2\nline3\n")
    (out / "node.uart").write_text("boot\nready\n")
    tail = service.log_tail(lines=2)
    assert tail["renode"] == ["line2", "line3"]
    assert tail["consoles"]["node.uart"] == ["boot", "ready"]


# --------------------------------------------------------------------------- verification

def test_verify_passed(tmp_path):
    service, _ = make_service(tmp_path)
    result = service.verify("EX-T01")
    assert result.outcome is Outcome.PASSED
    assert service.status().last_results == (result,)


def test_verify_all_skipped_is_not_run_never_passed(tmp_path):
    service, _ = make_service(
        tmp_path, runner=lambda verify_file: (0, "=== 3 skipped in 0.5s ==="))
    result = service.verify("EX-T01")
    assert result.outcome is Outcome.NOT_RUN
    assert "prerequisites" in result.detail


def test_verify_failed(tmp_path):
    service, _ = make_service(
        tmp_path, runner=lambda verify_file: (1, "=== 1 failed, 2 passed in 3.0s ==="))
    assert service.verify("EX-T01").outcome is Outcome.FAILED


def test_verify_hw_is_not_applicable(tmp_path):
    service, _ = make_service(tmp_path)
    result = service.verify("EX-T01", target="hw")
    assert result.outcome is Outcome.NOT_APPLICABLE
    assert "hw-compat-table" in result.detail


def test_verify_without_script_is_not_applicable(tmp_path):
    service, _ = make_service(tmp_path, with_verify=False)
    assert service.verify("EX-T01").outcome is Outcome.NOT_APPLICABLE


def test_verify_unknown_target_and_exercise_raise(tmp_path):
    service, _ = make_service(tmp_path)
    with pytest.raises(ValueError):
        service.verify("EX-T01", target="moon")
    with pytest.raises(ValueError):
        service.verify("EX-NOPE")
