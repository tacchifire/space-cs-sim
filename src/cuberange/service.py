"""The range as a service: session lifecycle and verification, without a UI.

Three different callers used to grow their own version of "run the range":

  - a learner runs tools/exercise.sh, which launches Renode under a shell trap;
  - every exercise's verify script builds a RenodeSupervisor launch by hand;
  - the browser GUI (cuberange.gui) is a read-only HTTP shell that tells the browser where
    Renode's server mode lives, and manages nothing itself.

So "is anything running, which exercise, since when, what did the last verification actually
establish" had no single answer, and the GUI and a future CLI would each have invented a
second, divergent one. This module is the one place that knows: start, stop, status, log
tails, recovery, and verification dispatch. It is deliberately UI-free - no HTTP, no
printing - so the GUI can grow session endpoints on top of it later and a CLI can use it
today, and both will be looking at the same state. The GUI's read-only posture is a
deliberate security choice and stays: nothing here changes gui/server.py.

VERIFICATION RESULTS DISTINGUISH FOUR THINGS, because the range's recurring failure mode is
reporting them as one another:

  - PASSED / FAILED: a runner actually ran and the verdict is in the output;
  - NOT_RUN: the exercise could be verified here, but was not - the usual cause is that the
    firmware builds are missing, which the verify scripts themselves report as "every test
    skipped" (their pytestmark skipif is the prerequisite check; this module reads that
    signal rather than keeping a second list of which ELFs each exercise needs);
  - NOT_APPLICABLE: no runner exists for that target at all. Hardware ("hw") is the standing
    example: until a hardware runner lands, asking for it is a category error, not a failure,
    and the answer names the compatibility table that holds the manual evidence list instead.

A supervisor's hang classification (renode.supervisor.Outcome) is a different axis - whether
the infrastructure behaved - and is not reused here on purpose: a verification that timed
out at the supervisor level has FAILED to establish anything, and the detail says why.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import ports
from .gui import catalog
from .paths import out_dir
from .renode.profile import profile_args
from .renode.supervisor import RenodeSupervisor

REPO = Path(__file__).resolve().parents[2]
EXERCISES_DIR = REPO / "exercises"

#: Where Renode lives. The verify scripts read the same variable for the same default; two
#: derivations of one fact are a liability, but this one is an operator override, not a
#: computed value, and both readers honour RENODE_DIR first.
RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))

#: The targets verify() knows how to talk about. "renode" has a runner (each exercise's
#: verify script under pytest). "hw" is named so it can be answered NOT_APPLICABLE honestly
#: instead of raising as if the caller had typo'd.
KNOWN_TARGETS = ("renode", "hw")


class Outcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class VerificationResult:
    exercise_id: str
    target: str
    outcome: Outcome
    detail: str
    duration_s: float = 0.0


class SessionState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"


@dataclass
class ServiceStatus:
    state: SessionState
    exercise_id: Optional[str]
    started_at: Optional[float]
    log_path: Optional[Path]
    last_results: tuple[VerificationResult, ...] = ()


def _tail(path: Path, lines: int) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(errors="replace").splitlines()[-lines:]


class RangeService:
    """One range session at a time, plus verification dispatch.

    Dependencies are injectable (supervisor factory, catalog lookup, verify runner) so the
    lifecycle and the result mapping are unit-testable without Renode - see
    tests/pytest/test_service.py. The defaults are the real ones.
    """

    def __init__(self, *, renode_dir: Path = RENODE_DIR, out: Optional[Path] = None,
                 exercises_dir: Path = EXERCISES_DIR,
                 supervisor_factory: Optional[Callable] = None,
                 catalog_get: Optional[Callable] = None,
                 runner: Optional[Callable] = None):
        self.renode_dir = Path(renode_dir)
        self.out = Path(out) if out is not None else out_dir()
        self.exercises_dir = Path(exercises_dir)
        self._supervisor_factory = supervisor_factory or self._default_supervisor
        self._catalog_get = catalog_get or catalog.get_exercise
        self._runner = runner or self._default_runner
        self._ctx = None
        self._session: Optional[ServiceStatus] = None
        self._results: list[VerificationResult] = []

    # ------------------------------------------------------------------ defaults
    def _default_supervisor(self, argv: Sequence[str], log_path: Path):
        sup = RenodeSupervisor(cwd=self.renode_dir, timeout_s=240, rss_ceiling_mb=2048)
        return sup.launch(argv, log_path)

    def _default_runner(self, verify_file: Path) -> tuple[int, str]:
        env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(verify_file), "-q", "--tb=no"],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=7200)
        return proc.returncode, (proc.stdout + proc.stderr)

    # ------------------------------------------------------------------ session
    def _argv(self, entry: dict) -> list[str]:
        """The launch tools/exercise.sh and the verify scripts agree on, in one place."""
        scenario = self.exercises_dir / entry["dir"] / "scenario.resc"
        return ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(ports.monitor()),
                "-e", f"$injector=@{REPO}/attacker/TcpCanInjector.cs",
                *profile_args(),
                "-e", f"$out=@{self.out}",
                "-e", f"include @{scenario}",
                "-e", "start"]

    def start(self, exercise_id: str) -> ServiceStatus:
        if self._session is not None:
            raise RuntimeError(
                f"a session for {self._session.exercise_id} is already running; stop it first")
        entry = self._catalog_get(exercise_id)
        if entry is None:
            raise ValueError(f"unknown exercise {exercise_id!r}")
        log_path = self.out / f"{entry['dir']}-renode.log"
        self._ctx = self._supervisor_factory(self._argv(entry), log_path)
        self._ctx.__enter__()
        self._session = ServiceStatus(
            state=SessionState.RUNNING, exercise_id=exercise_id,
            started_at=time.time(), log_path=log_path,
            last_results=tuple(self._results))
        return self.status()

    def stop(self) -> ServiceStatus:
        """Idempotent: stopping a stopped service is the current status, not an error."""
        if self._ctx is not None:
            self._ctx.__exit__(None, None, None)
            self._ctx = None
        self._session = None
        return self.status()

    def recover(self) -> ServiceStatus:
        """Back to a known state after a caller died mid-session or lost track of us.

        Recovery is the supervisor's stop - kill the process GROUP, so no stray Renode holds
        the ports and skews the next run's timing - plus dropping the session record. The
        logs are deliberately kept: they are the evidence for whatever went wrong, and a
        recovery that destroyed them would be tidier and worse.
        """
        return self.stop()

    def status(self) -> ServiceStatus:
        if self._session is None:
            return ServiceStatus(state=SessionState.STOPPED, exercise_id=None,
                                 started_at=None, log_path=None,
                                 last_results=tuple(self._results))
        self._session.last_results = tuple(self._results)
        return self._session

    def log_tail(self, lines: int = 50) -> dict:
        """The Renode log and every node console of the current (or last) session."""
        result: dict = {"renode": [], "consoles": {}}
        if self._session is not None and self._session.log_path is not None:
            result["renode"] = _tail(self._session.log_path, lines)
        for uart in sorted(self.out.glob("*.uart")):
            result["consoles"][uart.name] = _tail(uart, lines)
        return result

    # ------------------------------------------------------------------ verification
    def _verify_file(self, entry: dict) -> Optional[Path]:
        found = sorted((self.exercises_dir / entry["dir"]).glob("verify_*.py"))
        return found[0] if found else None

    def verify(self, exercise_id: str, target: str = "renode") -> VerificationResult:
        if target not in KNOWN_TARGETS:
            raise ValueError(f"unknown target {target!r}; known targets are {KNOWN_TARGETS}")
        entry = self._catalog_get(exercise_id)
        if entry is None:
            raise ValueError(f"unknown exercise {exercise_id!r}")

        if target == "hw":
            result = VerificationResult(
                exercise_id, target, Outcome.NOT_APPLICABLE,
                "no hardware verification runner exists yet; the manual evidence list for "
                "this exercise is its row in docs/testbed/hw-compat-table.md")
        else:
            verify_file = self._verify_file(entry)
            if verify_file is None:
                result = VerificationResult(
                    exercise_id, target, Outcome.NOT_APPLICABLE,
                    f"{entry['dir']} ships no verify_*.py for the renode target")
            else:
                started = time.time()
                returncode, output = self._runner(verify_file)
                result = self._map_result(exercise_id, target, returncode, output,
                                          time.time() - started)
        self._results.append(result)
        return result

    @staticmethod
    def _map_result(exercise_id: str, target: str, returncode: int, output: str,
                    duration_s: float) -> VerificationResult:
        def count(word: str) -> int:
            match = re.search(rf"(\d+) {word}", output)
            return int(match.group(1)) if match else 0

        passed, failed, skipped = count("passed"), count("failed"), count("skipped")
        errors = count("error") + count("errors")
        summary = output.strip().splitlines()[-1] if output.strip() else ""
        if returncode == 5:
            outcome, why = Outcome.NOT_APPLICABLE, "pytest collected no tests"
        elif returncode == 0 and passed == 0 and skipped > 0:
            outcome, why = Outcome.NOT_RUN, (
                "every test skipped: prerequisites (the firmware builds) are missing")
        elif returncode == 0:
            outcome, why = Outcome.PASSED, f"{passed} passed"
        else:
            outcome, why = Outcome.FAILED, (
                f"{failed} failed, {errors} errors, {passed} passed (exit {returncode})")
        return VerificationResult(exercise_id, target, outcome,
                                  f"{why}; pytest: {summary}", duration_s)
