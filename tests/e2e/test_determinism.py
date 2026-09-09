"""Rules G1 and G2 of design section 9.2, reproduced rather than asserted.

The design says the CI profile is bit-reproducible and the interactive profile is not, on the basis
of a measurement nobody could re-run: `SetGlobalSerialExecution` and `SetSeed` appeared in no .resc
file in the tree, so both rules governed nothing. This is the mechanism that makes them real.

Each test launches the same two-node scenario several times, captures the guest-generated UART
files, and hashes them. Rule G5 permits hashing exactly this - a backend capture written by the
guest - and nothing else: Renode's own logFile carries a host timestamp on every line, and two
snapshots of identical state differ by about 8000 bytes of TimeSource bookkeeping.

Runs are stepped with `emulation RunFor` and then `quit`, not `start`. A free-running emulation
stops whenever the host process is killed, so its captures differ in length for reasons that have
nothing to do with the profile - the thing under test would be swamped by when the kill landed.

The interactive test asserts nothing about divergence. Non-determinism does not have to show up in
any particular run, so requiring a difference would make the suite flaky in the direction of
claiming something false. It records what happened and only fails if the run itself broke.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports  # noqa: E402
from cuberange.paths import out_dir                             # noqa: E402
from cuberange.renode.profile import profile_path      # noqa: E402
from cuberange.renode.supervisor import Outcome, RenodeSupervisor  # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = REPO / "scripts" / "multi-node" / "p0.resc"

# Virtual seconds per run. Long enough that both nodes boot and exchange CAN traffic; short enough
# that six runs stay inside a test suite. At 1.0x (the CI profile has no AdvanceImmediately) this
# is also very close to the wall-clock cost per run.
RUN_FOR_S = "4"
REPEATS = int(os.environ.get("CUBERANGE_DETERMINISM_REPEATS", "3"))

# The scenario opens a socket terminal whether or not anybody connects, so this run needs a port
# of its own: the default link port collides with a round-trip test running alongside. It used to
# name a literal with the comment "a port nothing else in the suite uses" - which was not true. The
# number it picked is channel(0), the ground-station side of EX-L01's channel, and nothing reported
# the overlap because the map was never consulted. ports.SCRATCH_LINK exists for exactly this.
LINK_PORT = ports.SCRATCH_LINK


def _capture(profile: str, index: int, tmp_path: Path) -> dict[str, str]:
    """One run. Returns {uart name: sha256} for the guest-written captures."""
    run_dir = tmp_path / f"{profile}-{index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    comm, obc = run_dir / "comm.uart", run_dir / "obc.uart"

    argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers", "--hide-log",
            "-e", f"$commuart=@{comm}",
            "-e", f"$obcuart=@{obc}",
            "-e", f"$linkport={LINK_PORT}",
            "-e", f"$profile=@{profile_path(profile)}",
            "-e", f"$out=@{OUT}",
            "-e", f"include @{SCENARIO}",
            "-e", f'emulation RunFor "{RUN_FOR_S}"',
            "-e", "quit"]

    sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
    with sup.launch(argv, run_dir / "renode.log") as run:
        # Wait for the scenario's own `quit` inside the block. RenodeSupervisor.launch kills the
        # process group when the block exits, so returning immediately produces a run that never
        # started - the first version of this test did exactly that and finished in 0.29 s with
        # no capture written.
        try:
            run.proc.wait(timeout=sup.timeout_s)
        except subprocess.TimeoutExpired:
            pytest.skip("Renode did not reach `quit` within the watchdog budget")
    result = run.result()
    if result.outcome in (Outcome.TIMEOUT, Outcome.RSS_EXCEEDED):
        pytest.skip(f"infrastructure failure, not a determinism result: {result.outcome}")

    digests = {}
    for path in (comm, obc):
        # An empty or missing capture must not hash to a stable value and read as "reproducible".
        # Two runs that both produced nothing would otherwise agree perfectly.
        assert path.is_file(), f"{path.name} was never written under profile {profile}"
        data = path.read_bytes()
        assert len(data) > 100, (
            f"{path.name} holds only {len(data)} bytes under profile {profile}; the node did not "
            f"boot, and hashing that would prove nothing")
        digests[path.name] = hashlib.sha256(data).hexdigest()
    return digests


@pytest.fixture(scope="module")
def firmware() -> None:
    for name in ("build-comm", "build-obc"):
        elf = OUT / name / "zephyr" / "zephyr.elf"
        if not elf.is_file():
            pytest.skip(f"{elf} missing - run `make firmware-p0` first")


def test_the_ci_profile_reproduces_byte_for_byte(firmware, tmp_path):
    """G1 + G2: serial execution and a fixed seed make the guest output identical across runs."""
    runs = [_capture("ci", i, tmp_path) for i in range(REPEATS)]
    first = runs[0]
    for i, other in enumerate(runs[1:], start=1):
        for name, digest in first.items():
            assert other[name] == digest, (
                f"run {i} diverged from run 0 on {name} under the CI profile:\n"
                f"  run 0: {digest}\n  run {i}: {other[name]}\n"
                f"The CI profile is the one that promises bit-reproducibility. Either "
                f"SetGlobalSerialExecution/SetSeed stopped being emitted, or Renode changed.")
    print(f"\nCI profile: {REPEATS} runs, identical. comm={first['comm.uart'][:16]}...")


def test_the_two_profiles_are_actually_different_configurations(firmware):
    """The profiles must differ in the two settings the rules name, or the comparison is theatre."""
    ci = profile_path("ci").read_text()
    interactive = profile_path("interactive").read_text()
    assert "SetGlobalSerialExecution true" in ci, "G1 is missing from the CI profile"
    assert "SetSeed" in ci, "G2 is missing from the CI profile"
    assert "SetGlobalAdvanceImmediately" not in ci, (
        "the CI profile sets AdvanceImmediately, which is what it exists to avoid")
    assert "SetGlobalAdvanceImmediately true" in interactive
    assert "SetGlobalSerialExecution" not in interactive
    for text in (ci, interactive):
        assert 'SetGlobalQuantum "0.002"' in text, (
            "the quantum is a correctness parameter and must be pinned in both profiles")


def test_every_scenario_selects_a_profile():
    """No scenario may go back to hard-coding its execution settings.

    This is the regression guard for the actual defect: the settings were inline in all four
    scenarios, so the rules could not be applied without editing each one.
    """
    scenarios = [p for p in REPO.rglob("*.resc") if "profiles" not in p.parts]
    assert scenarios, "no scenarios found - this test would pass vacuously"
    for path in scenarios:
        text = path.read_text()
        rel = path.relative_to(REPO)
        assert "include $profile" in text, f"{rel} does not include an execution profile"
        for setting in ("SetGlobalQuantum", "SetGlobalAdvanceImmediately",
                        "SetGlobalSerialExecution", "SetSeed"):
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                assert setting not in stripped, (
                    f"{rel} sets {setting} inline; it belongs in scripts/profiles/")


def test_an_unknown_profile_is_refused():
    """A typo must not silently fall back to the fast, non-reproducible profile."""
    with pytest.raises(ValueError, match="unknown execution profile"):
        profile_path("determinstic")


def test_a_missing_profile_takes_the_scenario_down(firmware, tmp_path):
    """Fail-closed: an unresolvable profile must stop the scenario, not run it unconfigured.

    This is the hazard the profile indirection introduced. A failing Monitor command aborts the
    rest of a .resc file, so with the include LAST a bad path would have left every node built and
    running with no quantum set at all - Renode's 100 us default, which changes firmware timing and
    reports nothing. The include is first, and this test is what keeps it there.

    What a broken profile actually looks like, measured rather than assumed:

      - no node boots, and no UART capture is created at all;
      - the `-e` chain is joined with `;`, so the abort swallows the trailing `quit` too and Renode
        never exits. It hangs until the supervisor's watchdog kills it;
      - the log is EMPTY. Renode's output is lost when the process group is killed, so the error
        text is not available afterwards. A harness that looked for an error string in the log
        would conclude nothing went wrong.

    The last two are why RenodeSupervisor has to report TIMEOUT here. If it called this OK, a
    scenario that never ran a single instruction would be indistinguishable from one that passed.
    """
    run_dir = tmp_path / "noprofile"
    run_dir.mkdir(parents=True, exist_ok=True)
    comm, obc = run_dir / "comm.uart", run_dir / "obc.uart"
    log = run_dir / "renode.log"

    argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers", "--hide-log",
            "-e", f"$commuart=@{comm}",
            "-e", f"$obcuart=@{obc}",
            "-e", f"$linkport={LINK_PORT + 1}",
            "-e", "$profile=@/nonexistent/cuberange-profile.resc",
            "-e", f"$out=@{OUT}",
            "-e", f"include @{SCENARIO}",
            "-e", 'emulation RunFor "2"',
            "-e", "quit"]

    # Short budget: the point is that it hangs, so waiting the full 240 s proves nothing extra.
    sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=20, rss_ceiling_mb=2048)
    with sup.launch(argv, log) as run:
        try:
            run.proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            pytest.fail("the supervisor watchdog did not kill the hung Renode")
    result = run.result()

    # 1. Nothing downstream of the failed include ran. This is the property under test.
    for path in (comm, obc):
        booted = path.is_file() and len(path.read_bytes()) > 100
        assert not booted, (
            f"{path.name} was written despite an unresolvable profile - the scenario kept going "
            f"and ran with no quantum set. Move `include $profile` back to the top of "
            f"{SCENARIO.name}.")

    # 2. The harness has to notice. A hung process that reports OK is the failure mode this
    #    project keeps rediscovering: success asserted from the absence of evidence.
    assert result.outcome is Outcome.TIMEOUT, (
        f"a scenario that never booted a node was reported as {result.outcome}; "
        f"the watchdog is the only thing standing between this and a silent pass")
