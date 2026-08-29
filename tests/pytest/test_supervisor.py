"""The watchdog must actually fire.

A watchdog that never trips is the same class of defect as a test harness that always passes, so
each guard here is driven with input designed to breach it. No Renode involved - these use plain
shell processes so they run in milliseconds and cannot be blamed on the emulator.
"""
import sys
import time
from pathlib import Path

import pytest

from cuberange.renode.supervisor import Outcome, RenodeSupervisor


@pytest.fixture
def work(tmp_path):
    return tmp_path


def test_a_process_that_exits_cleanly_is_ok(work):
    sup = RenodeSupervisor(cwd=work, timeout_s=10)
    with sup.launch(["true"], work / "ok.log") as run:
        run.proc.wait(timeout=5)
    assert run.result().outcome is Outcome.OK


def test_a_process_that_exits_non_zero_is_a_crash(work):
    sup = RenodeSupervisor(cwd=work, timeout_s=10)
    with sup.launch(["sh", "-c", "exit 3"], work / "crash.log") as run:
        run.proc.wait(timeout=5)
    result = run.result()
    assert result.outcome is Outcome.CRASH
    assert result.returncode == 3


def test_the_wall_clock_watchdog_fires_on_a_hang(work):
    """This is the R22 case: Renode wedges and never returns."""
    sup = RenodeSupervisor(cwd=work, timeout_s=1.0, poll_s=0.1)
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
    sup = RenodeSupervisor(cwd=work, timeout_s=60, rss_ceiling_mb=192, poll_s=0.1)
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
    sup = RenodeSupervisor(cwd=work, timeout_s=1.0, poll_s=0.1)
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
    sup = RenodeSupervisor(cwd=work, timeout_s=20, poll_s=0.05)
    hold = "import time; buf = bytearray(32 * 1024 * 1024); time.sleep(1.5)"
    with sup.launch([sys.executable, "-c", hold], work / "rss.log") as run:
        run.proc.wait(timeout=15)
    result = run.result()
    assert result.outcome is Outcome.OK
    assert result.peak_rss_mb > 20, f"peak RSS looks unmeasured: {result.peak_rss_mb:.0f} MB"
