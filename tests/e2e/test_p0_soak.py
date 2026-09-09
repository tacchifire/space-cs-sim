"""P0's remaining acceptance condition: thirty consecutive round trips without loss.

Renode wedges on roughly 13% of launches and leaks memory when it does, so "thirty in a row" is
only meaningful with the supervisor in place. A hang is retried and counted separately from a
failed round trip - conflating the two would either hide a real regression behind infrastructure
noise, or fail the build for something a retry fixes.

Slow by design. Run with:  make soak-p0
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink                      # noqa: E402
from cuberange.gs.station import GroundStation               # noqa: E402
from cuberange.renode.profile import profile_args
from cuberange.renode.supervisor import Outcome, RenodeSupervisor  # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = Path(os.environ.get("OUT", "/tmp/cuberange"))
ITERATIONS = int(os.environ.get("SOAK_ITERATIONS", "30"))
MAX_RETRIES_PER_ITERATION = 3
BOOT_SETTLE_S = 3.0
LINK_PORT = 3777
MONITOR_PORT = 3778

# Four tuned nodes peak around 503 MB; two nodes need far less. A wedged Renode passes 3.7 GB
# within 100 s, so this ceiling separates the two cases with room to spare.
RSS_CEILING_MB = 1536
LAUNCH_TIMEOUT_S = 90


def _attempt(sup: RenodeSupervisor, run_dir: Path, index: int, attempt: int):
    """One launch. Returns (ok, outcome, detail)."""
    for name in ("comm.uart", "obc.uart"):
        (OUT / name).unlink(missing_ok=True)
    log = run_dir / f"run{index:02d}-try{attempt}.log"
    argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers", "--hide-log",
            "--port", str(MONITOR_PORT),
            *profile_args(),
            "-e", f"include @{REPO}/scripts/multi-node/p0.resc",
            "-e", "start"]

    with sup.launch(argv, log) as run:
        link = SpaceLink(port=LINK_PORT)
        try:
            link.connect(retries=40)
        except ConnectionError as exc:
            return False, run.result().outcome, f"link never came up: {exc}"
        try:
            time.sleep(BOOT_SETTLE_S)
            report = GroundStation(link).ping(timeout=20)
        except (ConnectionError, OSError) as exc:
            return False, run.result().outcome, f"link died mid-exchange: {exc}"
        finally:
            link.close()

    result = run.result()
    if report is not None:
        return True, result.outcome, f"{result.wall_s:.1f}s peak {result.peak_rss_mb:.0f}MB"
    if result.retryable:
        return False, result.outcome, f"infrastructure: {result.outcome.value}"
    return False, result.outcome, "no PUS 17,2 report came back"


@pytest.mark.slow
def test_thirty_consecutive_round_trips():
    for name in ("build-comm", "build-obc"):
        elf = OUT / name / "zephyr" / "zephyr.elf"
        if not elf.exists():
            pytest.skip(f"{elf} missing - run 'make firmware-p0' first")

    run_dir = OUT / "soak"
    run_dir.mkdir(parents=True, exist_ok=True)
    sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=LAUNCH_TIMEOUT_S,
                           rss_ceiling_mb=RSS_CEILING_MB)

    successes = 0
    infra_retries = 0
    failures = []

    for i in range(ITERATIONS):
        for attempt in range(1, MAX_RETRIES_PER_ITERATION + 1):
            ok, outcome, detail = _attempt(sup, run_dir, i, attempt)
            if ok:
                successes += 1
                print(f"  run {i:02d}: ok ({detail})")
                break
            if outcome in (Outcome.TIMEOUT, Outcome.RSS_EXCEEDED):
                infra_retries += 1
                print(f"  run {i:02d} attempt {attempt}: {detail} - retrying")
                continue
            failures.append((i, outcome.value, detail))
            print(f"  run {i:02d}: FAILED ({detail})")
            break
        else:
            failures.append((i, "exhausted", f"{MAX_RETRIES_PER_ITERATION} attempts all wedged"))

    print(f"\n{successes}/{ITERATIONS} round trips succeeded, "
          f"{infra_retries} infrastructure retries, {len(failures)} failures")
    for i, outcome, detail in failures:
        print(f"  run {i:02d}: {outcome} - {detail}")

    assert not failures, f"{len(failures)} of {ITERATIONS} round trips failed"
    assert successes == ITERATIONS
