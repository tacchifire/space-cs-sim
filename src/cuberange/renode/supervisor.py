"""Supervised Renode launches.

Renode hangs on roughly 13% of runs and, when it does, leaks memory hard: 3.7 GB within 100 s and
17.3 GB within 10 minutes was measured across 107 runs, in both the default and tuned
configurations. That is enough to OOM a CI runner or a learner's laptop, and it always recovered on
a retry. So every launch is wrapped in a wall-clock watchdog and an RSS ceiling, and a hang is
classified as a retryable infrastructure failure rather than a test failure.

Two rules encoded here that were learned the hard way:

  - kill the process GROUP, not the pid. `timeout ./renode` leaves the real Renode alive when only
    the wrapper is signalled, and a stray Renode holds its ports and skews the next run's timing.
  - never `pkill -f renode`. It kills other people's instances, and on one occasion the shell
    command doing the killing.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional, Sequence
from ..safety.network import assert_isolated_network


# Defaults, overridable from the environment so a smaller host is retuned in one place instead of
# at every call site. The ceiling has to sit well above the working set - 503 MB peak was measured
# for 4 nodes, ~107 MB more per additional node - and well below the point where the host starts
# swapping, because a leaking Renode reaches 3.7 GB in 100 s and detection plus the kill sequence
# can overshoot the ceiling by ~220 MB. On a 4 GB board use CUBERANGE_RSS_CEILING_MB=1024 and
# CUBERANGE_SUPERVISOR_POLL_S=0.25; below 4 GB there is no ceiling that both fits and avoids false
# positives, so do not run the 4-node scenarios there.
#
# The timeout is wall clock, and Renode's speed is bounded by one host thread. A slower board needs
# a longer budget or a legitimately slow run gets classified as the hang and retried.
DEFAULT_TIMEOUT_S = float(os.environ.get("CUBERANGE_SUPERVISOR_TIMEOUT_S", "180"))
DEFAULT_RSS_CEILING_MB = float(os.environ.get("CUBERANGE_RSS_CEILING_MB", "2048"))
DEFAULT_POLL_S = float(os.environ.get("CUBERANGE_SUPERVISOR_POLL_S", "0.5"))


class Outcome(str, Enum):
    OK = "ok"                       # exited on its own, or was stopped by us after the work finished
    RUNNING = "running"             # still alive; result() was called mid-run
    TIMEOUT = "timeout"             # exceeded the wall-clock budget - retryable
    RSS_EXCEEDED = "rss_exceeded"   # tripped the memory ceiling - retryable
    CRASH = "crash"                 # exited non-zero without being asked to


#: Exit codes that a stop THIS supervisor asked for can produce.
#:
#: Measured on Renode 1.16.1 portable-dotnet, with the process verified alive before signalling:
#: SIGTERM comes back as 143 and SIGKILL as -9. The 128+n form is a process that handled the
#: signal and exited; the negative form is Python reporting a death it could not handle.
#:
#: A first attempt at this measurement signalled a Renode launched with stdin=DEVNULL, which had
#: already exited on EOF, and "measured" 0 for SIGKILL - a result that cannot happen. Keeping
#: stdin open and asserting proc.poll() is None before the signal is what makes these numbers real.
#:
#: Note what this list did NOT contain before: 143. The supervisor sends SIGTERM first, so its own
#: routine stop was landing in the CRASH branch, while an OOM kill from outside landed in OK.
_REQUESTED_STOP_CODES = (128 + signal.SIGTERM, 128 + signal.SIGKILL,
                         -signal.SIGTERM, -signal.SIGKILL)


@dataclass
class RunResult:
    outcome: Outcome
    returncode: Optional[int]
    wall_s: float
    peak_rss_mb: float
    log_path: Path

    @property
    def retryable(self) -> bool:
        return self.outcome in (Outcome.TIMEOUT, Outcome.RSS_EXCEEDED)


def _rss_mb(pid: int) -> float:
    """Resident set of a process group leader and its children, in MB. 0 if it is gone."""
    total = 0
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
                    break
    except OSError:
        return 0.0
    # Renode's real work happens in a child of the launcher, so walk one level down.
    try:
        children = Path(f"/proc/{pid}/task").iterdir()
        for task in children:
            children_file = task / "children"
            if not children_file.exists():
                continue
            for child in children_file.read_text().split():
                try:
                    with open(f"/proc/{int(child)}/status") as f:
                        for line in f:
                            if line.startswith("VmRSS:"):
                                total += int(line.split()[1])
                                break
                except OSError:
                    pass
    except OSError:
        pass
    return total / 1024.0


class RenodeSupervisor:
    def __init__(self, cwd: Path, timeout_s: float = DEFAULT_TIMEOUT_S,
                 rss_ceiling_mb: float = DEFAULT_RSS_CEILING_MB,
                 poll_s: float = DEFAULT_POLL_S, *, require_isolation: bool = True):
        """`require_isolation` defaults to True, and that is a deliberate breaking default.

        Renode 1.16.1 binds every socket terminal and the Monitor to 0.0.0.0 with no way to name an
        address - measured, and not configurable in this version. What those sockets accept is the
        exercise: unauthenticated telecommands, and raw CAN frames from anyone who connects. On a
        laptop on a conference network that is not a range, it is an invitation.

        So the default fails closed. Pass False only when what you are launching is not Renode -
        tests/pytest/test_supervisor.py drives `sleep` and `true` through this class to exercise the
        watchdogs, and containment is irrelevant to those.

        See src/cuberange/safety/network.py for the escape hatch and why it is named in the error.
        """
        self.cwd = Path(cwd)
        self.timeout_s = timeout_s
        self.rss_ceiling_mb = rss_ceiling_mb
        self.poll_s = poll_s
        self.require_isolation = require_isolation
        if require_isolation:
            assert_isolated_network()

    @contextlib.contextmanager
    def launch(self, argv: Sequence[str], log_path: Path) -> Iterator["_Supervised"]:
        """Run `argv` under supervision for the duration of the `with` block."""
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()

        with open(log_path, "w") as log:
            proc = subprocess.Popen(
                list(argv), cwd=str(self.cwd), stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True)

        sup = _Supervised(proc, self, log_path, started)
        watcher = threading.Thread(target=sup._watch, daemon=True)
        watcher.start()
        try:
            yield sup
        finally:
            sup._stop()
            watcher.join(timeout=5)


class _Supervised:
    def __init__(self, proc: subprocess.Popen, sup: RenodeSupervisor,
                 log_path: Path, started: float):
        self.proc = proc
        self._sup = sup
        self._log_path = log_path
        self._started = started
        self._stopping = threading.Event()
        self.peak_rss_mb = 0.0
        self.breach: Optional[Outcome] = None
        # Whether the stop was ours. Without it a signal death is indistinguishable from a clean
        # one, so an OOM kill or somebody else's pkill reported a healthy run - which is how the
        # enum's own comment ("exited non-zero without being asked to") stopped being true of the
        # code beneath it.
        self._requested_stop = False

    def _watch(self) -> None:
        while not self._stopping.is_set():
            if self.proc.poll() is not None:
                return
            rss = _rss_mb(self.proc.pid)
            self.peak_rss_mb = max(self.peak_rss_mb, rss)
            if rss > self._sup.rss_ceiling_mb:
                self.breach = Outcome.RSS_EXCEEDED
                self._kill_group()
                return
            if time.time() - self._started > self._sup.timeout_s:
                self.breach = Outcome.TIMEOUT
                self._kill_group()
                return
            self._stopping.wait(self._sup.poll_s)

    def _kill_group(self) -> None:
        self._requested_stop = True
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(self.proc.pid), sig)
            except OSError:
                return
            try:
                self.proc.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                continue

    def _stop(self) -> None:
        self._stopping.set()
        self._requested_stop = True
        if self.proc.poll() is None:
            self._kill_group()

    def result(self) -> RunResult:
        rc = self.proc.poll()
        wall = time.time() - self._started
        if self.breach is not None:
            outcome = self.breach
        elif rc is None:
            # Still running. test_p0_soak asks for a result mid-run to report alongside its own
            # diagnosis, so this is a legitimate state and not a failure - but it is not OK either.
            outcome = Outcome.RUNNING
        elif rc == 0:
            outcome = Outcome.OK
        elif self._requested_stop and rc in _REQUESTED_STOP_CODES:
            outcome = Outcome.OK
        else:
            outcome = Outcome.CRASH
        return RunResult(outcome=outcome, returncode=rc, wall_s=wall,
                         peak_rss_mb=self.peak_rss_mb, log_path=self._log_path)
