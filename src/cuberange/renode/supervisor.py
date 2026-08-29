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


class Outcome(str, Enum):
    OK = "ok"                       # exited on its own, or was stopped by us after the work finished
    TIMEOUT = "timeout"             # exceeded the wall-clock budget - retryable
    RSS_EXCEEDED = "rss_exceeded"   # tripped the memory ceiling - retryable
    CRASH = "crash"                 # exited non-zero without being asked to


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
    def __init__(self, cwd: Path, timeout_s: float = 180.0,
                 rss_ceiling_mb: float = 2048.0, poll_s: float = 0.5):
        self.cwd = Path(cwd)
        self.timeout_s = timeout_s
        self.rss_ceiling_mb = rss_ceiling_mb
        self.poll_s = poll_s

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
        if self.proc.poll() is None:
            self._kill_group()

    def result(self) -> RunResult:
        rc = self.proc.poll()
        wall = time.time() - self._started
        if self.breach is not None:
            outcome = self.breach
        elif rc is None or rc in (0, -signal.SIGTERM, -signal.SIGKILL):
            # We stop Renode ourselves once the work is done, so a signal death is expected.
            outcome = Outcome.OK
        else:
            outcome = Outcome.CRASH
        return RunResult(outcome=outcome, returncode=rc, wall_s=wall,
                         peak_rss_mb=self.peak_rss_mb, log_path=self._log_path)
