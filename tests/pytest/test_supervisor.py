"""The watchdog must actually fire.

A watchdog that never trips is the same class of defect as a test harness that always passes, so
each guard here is driven with input designed to breach it. No Renode involved - these use plain
shell processes so they run in milliseconds and cannot be blamed on the emulator.
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cuberange.renode.supervisor import Outcome, RenodeSupervisor


@pytest.fixture
def work(tmp_path):
    return tmp_path


def test_a_process_that_exits_cleanly_is_ok(work):
    sup = RenodeSupervisor(cwd=work, timeout_s=10, require_isolation=False)
    with sup.launch(["true"], work / "ok.log") as run:
        run.proc.wait(timeout=5)
    assert run.result().outcome is Outcome.OK


def test_a_process_that_exits_non_zero_is_a_crash(work):
    sup = RenodeSupervisor(cwd=work, timeout_s=10, require_isolation=False)
    with sup.launch(["sh", "-c", "exit 3"], work / "crash.log") as run:
        run.proc.wait(timeout=5)
    result = run.result()
    assert result.outcome is Outcome.CRASH
    assert result.returncode == 3


def test_the_wall_clock_watchdog_fires_on_a_hang(work):
    """This is the R22 case: Renode wedges and never returns."""
    sup = RenodeSupervisor(cwd=work, timeout_s=1.0, poll_s=0.1, require_isolation=False)
    with sup.launch(["sleep", "60"], work / "hang.log") as run:
        deadline = time.time() + 10
        while run.proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
    result = run.result()
    assert result.outcome is Outcome.TIMEOUT
    assert result.retryable, "a hang must be retryable infrastructure failure, not a test failure"
    assert result.wall_s < 8, f"the watchdog took {result.wall_s:.1f}s to fire"


def test_the_rss_ceiling_fires_on_a_leak(work):
    """The other half of R22: Renode leaks to 3.7 GB in 100 s. The ceiling must catch it long
    before the machine notices."""
    hog = ("import time\n"
           "buf = []\n"
           "for _ in range(400):\n"
           "    buf.append(bytearray(8 * 1024 * 1024))\n"
           "    time.sleep(0.02)\n"
           "time.sleep(60)\n")
    sup = RenodeSupervisor(cwd=work, timeout_s=60, rss_ceiling_mb=192, poll_s=0.1, require_isolation=False)
    with sup.launch([sys.executable, "-c", hog], work / "leak.log") as run:
        deadline = time.time() + 30
        while run.proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
    result = run.result()
    assert result.outcome is Outcome.RSS_EXCEEDED, (
        f"expected the RSS ceiling to trip, got {result.outcome} "
        f"with peak {result.peak_rss_mb:.0f} MB")
    assert result.retryable
    assert result.peak_rss_mb > 150, f"peak RSS was only {result.peak_rss_mb:.0f} MB"


def test_the_whole_process_group_dies(work):
    """`timeout ./renode` leaves the real Renode alive if only the wrapper is signalled, and a
    stray Renode holds its ports and skews the next run. Killing the group is the fix."""
    marker = work / "child-alive"
    script = (f"sh -c 'while true; do touch {marker}; sleep 0.1; done' & "
              "wait")
    sup = RenodeSupervisor(cwd=work, timeout_s=1.0, poll_s=0.1, require_isolation=False)
    with sup.launch(["sh", "-c", script], work / "group.log") as run:
        deadline = time.time() + 10
        while run.proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
    assert run.result().outcome is Outcome.TIMEOUT

    # If the grandchild survived it keeps touching the marker; a stale mtime proves it is gone.
    if marker.exists():
        marker.unlink()
    time.sleep(1.0)
    assert not marker.exists(), "a grandchild outlived the process-group kill"


def test_peak_rss_is_reported_even_on_a_clean_run(work):
    sup = RenodeSupervisor(cwd=work, timeout_s=20, poll_s=0.05, require_isolation=False)
    hold = "import time; buf = bytearray(32 * 1024 * 1024); time.sleep(1.5)"
    with sup.launch([sys.executable, "-c", hold], work / "rss.log") as run:
        run.proc.wait(timeout=15)
    result = run.result()
    assert result.outcome is Outcome.OK
    assert result.peak_rss_mb > 20, f"peak RSS looks unmeasured: {result.peak_rss_mb:.0f} MB"


def test_a_kill_we_did_not_ask_for_is_not_a_clean_run(work):
    """An OOM kill and a routine stop produce the same signal. Only one of them is fine.

    The classifier used to accept any SIGTERM or SIGKILL death as OK, with the comment "we stop
    Renode ourselves once the work is done" - an assumption, not a fact. The OOM killer sends
    SIGKILL, and this project already records Renode reaching 17 GB of RSS in ten minutes.
    """
    import os
    import signal as _signal

    sup = RenodeSupervisor(cwd=work, timeout_s=30, poll_s=0.1, require_isolation=False)
    with sup.launch(["sleep", "60"], work / "killed.log") as run:
        for _ in range(50):
            if run.proc.poll() is None:
                break
            time.sleep(0.1)
        assert run.proc.poll() is None, "the process under test exited before it could be killed"
        # From outside, exactly as an OOM kill or a stray pkill would arrive.
        os.kill(run.proc.pid, _signal.SIGKILL)
        run.proc.wait(timeout=10)
        outcome_before_our_stop = run.result().outcome

    assert outcome_before_our_stop is Outcome.CRASH, (
        f"a kill this supervisor never asked for was reported as {outcome_before_our_stop}; an OOM "
        f"kill would read as a healthy run")


def test_a_stop_we_did_ask_for_is_ok(work):
    """The other half. Without it the test above passes against a classifier that says CRASH."""
    sup = RenodeSupervisor(cwd=work, timeout_s=30, poll_s=0.1, require_isolation=False)
    with sup.launch(["sleep", "60"], work / "stopped.log") as run:
        for _ in range(50):
            if run.proc.poll() is None:
                break
            time.sleep(0.1)
    assert run.result().outcome is Outcome.OK, (
        "the supervisor's own stop was reported as a failure")


def test_a_result_read_mid_run_says_running(work):
    """test_p0_soak asks for a result while the process is alive; that is not OK and not a crash."""
    sup = RenodeSupervisor(cwd=work, timeout_s=30, poll_s=0.1, require_isolation=False)
    with sup.launch(["sleep", "30"], work / "midrun.log") as run:
        for _ in range(50):
            if run.proc.poll() is None:
                break
            time.sleep(0.1)
        assert run.result().outcome is Outcome.RUNNING


def test_a_process_that_handles_sigterm_and_exits_143_is_still_our_stop(work):
    """Renode is not `sleep`: it catches SIGTERM and exits 143, not -15.

    Measured on 1.16.1 portable-dotnet with the process verified alive first. `sleep` dies from the
    signal and reports -15, so every test above exercises only the negative-code path - dropping
    143 from _REQUESTED_STOP_CODES broke nothing until this test existed, while in a real run it
    put the supervisor's own routine stop into the CRASH branch.
    """
    # Python rather than `sh -c 'trap ...; sleep 60'`: a shell exec's its last command, replacing
    # itself and taking the trap with it, so that version reported -15 and modelled nothing.
    #
    # And the readiness file is not politeness. `poll() is None` is true the instant Popen returns,
    # so waiting on it stopped the process 6 ms in - before the interpreter had reached
    # signal.signal() - and the child died from the default disposition with -15. The test was
    # measuring startup latency, not signal handling.
    ready = work / "ready"
    child = ("import os, signal, sys, time\n"
             "signal.signal(signal.SIGTERM, lambda *a: os._exit(143))\n"
             "open(sys.argv[1], 'w').close()\n"
             "time.sleep(60)\n")
    sup = RenodeSupervisor(cwd=work, timeout_s=30, poll_s=0.1, require_isolation=False)
    with sup.launch([sys.executable, "-c", child, str(ready)], work / "term143.log") as run:
        deadline = time.time() + 15
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "the child never installed its handler"
        assert run.proc.poll() is None
    result = run.result()
    assert result.returncode == 143, (
        f"the shell did not report 128+SIGTERM (got {result.returncode}); this test no longer "
        f"models how Renode exits")
    assert result.outcome is Outcome.OK


def test_the_rss_ceiling_sees_a_grandchild(work):
    """The walk's depth, driven with memory only a full walk can find.

    The accounting stopped one level down, with a comment asserting that Renode's work happens in
    a child of the launcher. Measured on 1.16.1 portable-dotnet, `./renode` is a single native
    process with no children at all, so one level and the whole tree agreed and the limit never
    bit. It would bite the moment Renode is packaged as a wrapper again - and silently, because an
    RSS ceiling that undercounts never fires.

    Eighty megabytes, touched page by page so it is resident rather than merely mapped.
    """
    from cuberange.renode.supervisor import _rss_mb

    script = work / "nest.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "depth, ready = int(sys.argv[1]), sys.argv[2]\n"
        "if depth:\n"
        "    subprocess.Popen([sys.executable, __file__, str(depth - 1), ready])\n"
        "    time.sleep(120)\n"
        "else:\n"
        "    blob = bytearray(80 * 1024 * 1024)\n"
        "    for i in range(0, len(blob), 4096):\n"
        "        blob[i] = 1\n"
        "    open(ready, 'w').close()\n"
        "    time.sleep(120)\n")
    ready = work / "grandchild-ready"

    proc = subprocess.Popen([sys.executable, str(script), "2", str(ready)])
    try:
        deadline = time.time() + 60
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.1)
        assert ready.exists(), "the grandchild never finished allocating"

        total = _rss_mb(proc.pid)
        assert total > 80, (
            f"the accounting saw {total:.1f} MB; the grandchild alone holds 80 MB, so the walk "
            f"is not reaching it and an RSS ceiling would never fire on it")
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_nothing_launches_renode_without_this_class():
    """CLAUDE.md: "Always launch through src/cuberange/renode/supervisor.py."

    That was prose with an exception nobody had noticed. tests/manual/spike_inject_and_observe.py
    held the last raw subprocess.Popen of ./renode - no wall-clock watchdog, no RSS ceiling -
    which is design section 16's W25 exactly, found in test_p0_roundtrip.py and fixed there while
    this one stayed. A manual spike is precisely where an unattended wedge or a 17 GB leak is left
    behind, because nobody is watching a run they started by hand.

    Read by AST, and that is the second version. The first checked whether the file MENTIONED
    RenodeSupervisor anywhere, which the offending file did - in the comment explaining why it
    now uses it. Restoring the raw Popen underneath left the comment in place and the guard
    passed. A guard whose exemption is a substring exempts anyone who writes the substring.
    """
    import ast

    repo = Path(__file__).resolve().parents[2]
    EXEMPT = {"src/cuberange/renode/supervisor.py",     # the mechanism itself
              "tests/pytest/test_supervisor.py"}        # this file, which names the pattern

    def launches_renode(node: ast.AST) -> bool:
        """A spawn whose argv literal starts with ./renode."""
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        name = (f"{getattr(func.value, 'id', '')}.{func.attr}"
                if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
        if name not in ("subprocess.Popen", "subprocess.run", "subprocess.call",
                        "subprocess.check_call", "subprocess.check_output") \
           and not name.startswith("os.exec"):
            return False
        for arg in node.args:
            if isinstance(arg, ast.List) and arg.elts:
                first = arg.elts[0]
                if isinstance(first, ast.Constant) and first.value == "./renode":
                    return True
            if isinstance(arg, ast.Constant) and arg.value == "./renode":
                return True
        return False

    offenders = []
    for path in sorted(repo.glob("**/*.py")):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(repo))
        if rel in EXEMPT:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError as exc:
            # Not skipped. A file this guard cannot read is a file it is not guarding, and
            # `except SyntaxError: continue` hid exactly that during its own mutation test - the
            # mutation produced an unparseable file and the guard reported a pass.
            offenders.append(f"{rel}: does not parse ({exc.msg} at line {exc.lineno})")
            continue
        for node in ast.walk(tree):
            if launches_renode(node):
                offenders.append(f"{rel}:{getattr(node, 'lineno', '?')}")
    assert not offenders, (
        "these spawn Renode directly instead of through RenodeSupervisor, so a wedged or leaking "
        "run has nothing watching it: " + ", ".join(offenders))


# --------------------------------------------------------------------------------------------
# Liveness preconditions
#
# Five exercise verifiers each carried their own
#
#     return self.station.ping(timeout=...) is not None
#
# and asserted it as a PRECONDITION more than ten times: `assert r.alive(), "the satellite was
# not answering before the attack"`. One unanswered ping failed the whole test before the attack
# under test had been sent. That happened in CI on 2026-09-12 in EX-G01, and three re-runs of the
# same test passed - which is the worst outcome, because the next person re-runs it until it is
# green and stops reading what it says.
#
# They now delegate to GroundStation.alive, which retries to a deadline. This is what stops a
# sixth one from re-implementing it.

def test_no_verifier_decides_liveness_on_a_single_ping():
    import re

    REPO = Path(__file__).resolve().parents[2]

    offenders = []
    for f in sorted(REPO.glob("exercises/*/verify_*.py")) + sorted(REPO.glob("tests/e2e/*.py")):
        text = f.read_text()
        for m in re.finditer(r"^.*\.ping\([^)]*\)\s*is not None.*$", text, re.M):
            line = m.group(0)
            #: A retry loop around a ping is the correct shape and reads the same way; what this
            #: forbids is one ping standing for "the spacecraft is up".
            if "while" in line or "def alive" in line:
                continue
            offenders.append(f"{f.relative_to(REPO)}: {line.strip()[:70]}")
    assert not offenders, (
        "these decide whether a spacecraft is alive on one round trip; use "
        "GroundStation.alive, which retries to a deadline:\n  " + "\n  ".join(offenders))


def test_the_shared_liveness_check_actually_retries():
    """A helper that only pings once would satisfy the test above and fix nothing."""
    import inspect

    from cuberange.gs.station import GroundStation

    src = inspect.getsource(GroundStation.alive)
    assert "while" in src, "GroundStation.alive does not loop"
    assert "deadline" in src, "GroundStation.alive has no deadline to loop until"


def test_the_shared_liveness_check_gives_up():
    """And it must return False rather than hang, or a dead spacecraft becomes a test timeout."""
    import time as _time

    from cuberange.gs.station import GroundStation

    calls = []

    class NeverAnswers(GroundStation):
        def __init__(self):
            pass

        def ping(self, timeout=10.0):
            calls.append(timeout)
            return None

    started = _time.time()
    assert NeverAnswers().alive(timeout=1.0, per_ping_s=0.2) is False
    assert _time.time() - started < 10, "alive() took far longer than its deadline"
    assert len(calls) >= 2, f"it gave up after {len(calls)} ping(s); that is the old behaviour"


def test_every_alive_call_in_the_repository_matches_the_signature():
    """The mistake this catches was made while fixing W48, and it broke three tests in CI.

    GroundStation.alive was added with a `deadline_s` parameter. Five verifiers already called
    their own `alive(timeout=15)` and `not alive(timeout=8)`, so every one of those became a
    TypeError - and pytest reports a TypeError in a precondition exactly the way it reports a
    mitigation that stopped working. The signature is checked against its callers here because
    the callers are in files this repository's other gates do not import.
    """
    import inspect
    import re

    from cuberange.gs.station import GroundStation

    REPO = Path(__file__).resolve().parents[2]
    accepted = set(inspect.signature(GroundStation.alive).parameters) - {"self"}
    assert accepted, "GroundStation.alive takes no parameters; this test has nothing to check"

    offenders = []
    for f in sorted(REPO.glob("exercises/*/verify_*.py")) + sorted(REPO.glob("tests/e2e/*.py")):
        text = f.read_text()
        #: The per-file shims delegate, so their own signature has to accept what they are passed
        #: too. Both are collected and compared against the shared one.
        local = set()
        m = re.search(r"def alive\(self,([^)]*)\)", text)
        if m:
            local = {k.strip() for k in re.findall(r"(\w+)\s*:", m.group(1))}
        for call in re.finditer(r"\.alive\(([^)]*)\)", text):
            for kw in re.findall(r"(\w+)\s*=", call.group(1)):
                if kw not in accepted and kw not in local:
                    offenders.append(f"{f.relative_to(REPO)}: .alive({kw}=...)")
    assert not offenders, (
        "these pass a keyword neither GroundStation.alive nor the local shim accepts, which "
        "raises TypeError inside a precondition and reads like a broken mitigation:\n  "
        + "\n  ".join(sorted(set(offenders))))
