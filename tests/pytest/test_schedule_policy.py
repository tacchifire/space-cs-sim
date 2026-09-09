"""EX-G01's anti-strawman proof, and the import policy's behaviour.

The firmware exercises prove they are not manufactured by comparing two builds: identical Kconfig,
one cache variable, identical compiler lines. Host-side Python has no such artefact, and
CONTRIBUTING.md's rule was written entirely in Zephyr terms - so an EX-G01 whose two halves were
"two code paths in the same process" could claim "we only changed one thing" with nothing able to
check it.

The analogue used here: ONE scheduler, TWO policy objects, and the scheduler must not know which
it holds. These tests assert that structurally - the module's source may not mention either policy
class, and both runs must go through the same code - so "only the policy differs" is checked rather
than asserted.
"""
import inspect
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs import schedule as schedule_mod                      # noqa: E402
from cuberange.gs.import_policy import (AcceptAnything, ImportRejected,  # noqa: E402
                                        IMPORT_ALLOWED_SERVICES, SignedAndBounded,
                                        sign, signed_plan)
from cuberange.gs.schedule import (ScheduleStore, Scheduler,           # noqa: E402
                                   operator_entry)

SERVICE_FUNCTION, SUBTYPE_PERFORM = 8, 1        # switching a power rail
SERVICE_TEST = 17                               # allowed to an import


def _plan(service: int, subtype: int = 1, args: str = "", due_offset: float = -1.0) -> dict:
    return {"version": 1,
            "entries": [{"due": time.time() + due_offset, "service": service,
                         "subtype": subtype, "args": args, "note": "pass prediction"}]}


def _run(policy, doc_bytes: bytes, tmp_path: Path):
    (tmp_path / "passes.plan.json").write_bytes(doc_bytes)
    sent = []
    store = ScheduleStore()
    try:
        sched = Scheduler(store, policy, tmp_path, sent.append)
        imported = sched.import_pending()
        fired = sched.tick()
        return sched, imported, fired, sent
    finally:
        store.close()


# --------------------------------------------------------------------- the anti-strawman proof

def test_the_scheduler_does_not_know_which_policy_it_holds():
    """The structural claim. If the scheduler branched on the policy, "one thing differs" would be
    untrue and the two halves of the exercise would be two programs."""
    src = inspect.getsource(schedule_mod)
    for forbidden in ("AcceptAnything", "SignedAndBounded", "isinstance(self.policy",
                      "policy.name =="):
        assert forbidden not in src, (
            f"schedule.py mentions {forbidden!r}. The vulnerable and mitigated ground segments "
            f"must differ only in the policy object they are given, and a scheduler that inspects "
            f"its policy can differ in anything at all.")


def test_both_policies_satisfy_the_same_interface():
    """A mitigation that needed a different call shape would be a different scheduler."""
    for policy in (AcceptAnything(), SignedAndBounded()):
        sig = inspect.signature(policy.accept)
        assert list(sig.parameters) == ["raw", "source"], (
            f"{type(policy).__name__}.accept has parameters {list(sig.parameters)}")


# --------------------------------------------------------------------- the three directions

def test_the_attack_works_against_the_permissive_importer(tmp_path):
    """A plugin writes a plan that commands a power rail, and the ground segment transmits it."""
    sched, imported, fired, sent = _run(AcceptAnything(), json.dumps(
        _plan(SERVICE_FUNCTION, args="000100")).encode(), tmp_path)
    assert imported == 1 and len(fired) == 1
    assert sent[0].service == SERVICE_FUNCTION
    assert sent[0].origin == "passes.plan.json", (
        "the transmitted command does not record where it came from, so an incident responder "
        "cannot tell an operator's entry from an imported one")


def test_the_mitigation_refuses_the_plugin_file(tmp_path):
    sched, imported, fired, sent = _run(SignedAndBounded(), json.dumps(
        _plan(SERVICE_FUNCTION, args="000100")).encode(), tmp_path)
    assert imported == 0 and fired == [] and sent == []
    assert sched.rejected and "unsigned" in sched.rejected[0][1]


def test_the_mitigation_still_imports_the_operator_s_own_plan(tmp_path):
    """A control that refuses every import is not a mitigation, it is a removed feature."""
    sched, imported, fired, sent = _run(
        SignedAndBounded(), signed_plan(_plan(SERVICE_TEST)), tmp_path)
    assert imported == 1 and len(fired) == 1
    assert sent[0].service == SERVICE_TEST


# --------------------------------------------------------------------- the policy's own edges

def test_a_signature_alone_does_not_authorise_any_command(tmp_path):
    """The control that is easy to leave out: signed is not the same as permitted.

    An operator's key proves who wrote the file. It says nothing about whether an IMPORT should be
    able to schedule a power command, and conflating the two turns a signed-import feature into a
    signed-anything feature.
    """
    sched, imported, fired, sent = _run(
        SignedAndBounded(), signed_plan(_plan(SERVICE_FUNCTION, args="000100")), tmp_path)
    assert imported == 0 and sent == []
    assert "may not be scheduled by import" in sched.rejected[0][1]


def test_a_tampered_plan_is_refused(tmp_path):
    """Sign a permitted plan, then edit it. The signature must stop covering it."""
    doc = _plan(SERVICE_TEST)
    raw = json.loads(signed_plan(doc).decode())
    raw["entries"][0]["service"] = SERVICE_FUNCTION
    sched, imported, _fired, sent = _run(SignedAndBounded(), json.dumps(raw).encode(), tmp_path)
    assert imported == 0 and sent == []
    assert "signature" in sched.rejected[0][1]


def test_the_signature_ignores_key_order(tmp_path):
    """A file re-serialised by any JSON writer must still verify, or the control is unusable."""
    doc = _plan(SERVICE_TEST)
    signed = json.loads(signed_plan(doc).decode())
    reordered = json.dumps({k: signed[k] for k in sorted(signed, reverse=True)}).encode()
    _sched, imported, _fired, sent = _run(SignedAndBounded(), reordered, tmp_path)
    assert imported == 1 and len(sent) == 1


def test_provenance_is_checked_before_shape(tmp_path):
    """Rejecting on a schema error first would tell an unauthenticated caller which field to fix."""
    unsigned_and_malformed = json.dumps({"version": 99, "entries": "not a list"}).encode()
    sched, _imported, _fired, _sent = _run(SignedAndBounded(), unsigned_and_malformed, tmp_path)
    assert "unsigned" in sched.rejected[0][1], (
        f"the schema was checked before the signature: {sched.rejected[0][1]}")


def test_rejected_files_are_kept_as_evidence(tmp_path):
    """A ground segment that consumed its inputs would destroy what a responder needs."""
    _sched, _imported, _fired, _sent = _run(SignedAndBounded(), b"{}", tmp_path)
    assert (tmp_path / "passes.plan.json").exists()


def test_an_unreadable_file_is_recorded_rather_than_raised(tmp_path):
    """One bad file must not stop the import of the others, and must not vanish silently."""
    (tmp_path / "a.plan.json").write_bytes(b"\xff\xfe not json")
    (tmp_path / "b.plan.json").write_bytes(signed_plan(_plan(SERVICE_TEST)))
    sent = []
    store = ScheduleStore()
    try:
        sched = Scheduler(store, SignedAndBounded(), tmp_path, sent.append)
        assert sched.import_pending() == 1
        assert len(sched.rejected) == 1 and sched.rejected[0][0] == "a.plan.json"
    finally:
        store.close()


def test_an_operator_entry_is_not_bounded_by_the_import_policy():
    """The policy governs IMPORTS. An operator with a console can still command anything.

    Said out loud because the opposite would be a nasty surprise: this control does not stop a
    compromised operator account, and mitigation.md says so.
    """
    sent = []
    store = ScheduleStore()
    try:
        sched = Scheduler(store, SignedAndBounded(), Path("/nonexistent"), sent.append)
        store.add(operator_entry(time.time() - 1, SERVICE_FUNCTION, SUBTYPE_PERFORM, b"\x00\x01\x00"))
        fired = sched.tick()
        assert len(fired) == 1 and fired[0].service == SERVICE_FUNCTION
    finally:
        store.close()


def test_the_allowed_set_excludes_function_management():
    """If service 8 were ever added to the allowlist the exercise would silently stop working."""
    assert SERVICE_FUNCTION not in IMPORT_ALLOWED_SERVICES
    assert SERVICE_TEST in IMPORT_ALLOWED_SERVICES


def test_sign_is_stable_and_key_dependent():
    doc = _plan(SERVICE_TEST)
    assert sign(doc) == sign(doc)
    assert sign(doc) != sign(doc, key=b"another operator")
