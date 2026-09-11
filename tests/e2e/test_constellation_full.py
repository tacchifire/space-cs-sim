"""Every spacecraft the addressing allows, in one emulation.

CSP v1 addresses are five bits and this range gives each spacecraft eight, so four is the ceiling
and `identity.cmake` refuses an index past it. Nothing had ever run four. The design's section 3.3
measured the per-node cost up to six machines and says plainly that it stops being measured after
that; sixteen is well past.

Measured on an eight-core x86-64 host, all nodes booting every time:

    2 spacecraft,  8 nodes   7.0s   4.25x real time   617 MB
    3 spacecraft, 12 nodes   8.5s   2.90x real time   766 MB
    4 spacecraft, 16 nodes  10.0s   1.66x real time   907 MB

So the ceiling is the addressing, not the host - at least on this one. The ratio is reported here
rather than asserted: probe.sh already owns the speed floor and explains why the number is
meaningless on a contended machine, and duplicating that check would mean two places to relax when
CI runs somewhere slower.

tests/e2e/test_constellation.py keeps the two-spacecraft bus isolation test, with its injector and
its positive control. This one is about scale: that the nodes exist, boot, and are each addressable
as themselves.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

import gen_constellation                                      # noqa: E402
from cuberange import ports                                   # noqa: E402
from cuberange.gs.link import SpaceLink                        # noqa: E402
from cuberange.gs.station import GroundStation                 # noqa: E402
from cuberange.identity import MAX_SATELLITES, spacecraft      # noqa: E402
from cuberange.paths import out_dir                            # noqa: E402
from cuberange.renode.monitor import Monitor                   # noqa: E402
from cuberange.renode.profile import profile_args              # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor       # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SATS = int(os.environ.get("CUBERANGE_SATS", MAX_SATELLITES))
ROLES = ("comm", "obc", "eps", "adcs")
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "120"))


def _images_present() -> bool:
    for i in range(SATS):
        for role in ROLES:
            if not (OUT / gen_constellation._build_dir(role, i) / "zephyr" / "zephyr.elf").is_file():
                return False
    return True


pytestmark = pytest.mark.skipif(
    not _images_present(),
    reason=f"build them: make firmware-all firmware-sat1 && make firmware-sat SAT=2 ... ({SATS})")


@pytest.fixture(scope="module")
def fleet():
    scenario = OUT / f"constellation{SATS}.resc"
    scenario.write_text(gen_constellation.render(SATS))
    for i in range(SATS):
        for role in ROLES:
            (OUT / f"sat{i}-{role}.uart").unlink(missing_ok=True)

    sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=300, rss_ceiling_mb=8192)
    argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
            "--port", str(ports.monitor()),
            *profile_args(), "-e", f"$out=@{OUT}",
            "-e", f"include @{scenario}", "-e", "start"]
    started = time.time()
    with sup.launch(argv, OUT / f"constellation{SATS}-renode.log") as run:
        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if _booted() == SATS * len(ROLES):
                break
            time.sleep(0.5)
        run.boot_seconds = time.time() - started
        yield run


def _console(name: str) -> str:
    p = OUT / name
    return p.read_text(errors="replace") if p.exists() else ""


def _booted() -> int:
    return sum(1 for i in range(SATS) for r in ROLES
               if "Booting Zephyr OS" in _console(f"sat{i}-{r}.uart"))


def test_every_node_boots(fleet):
    want = SATS * len(ROLES)
    got = _booted()
    assert got == want, (
        f"{got}/{want} nodes booted in {fleet.boot_seconds:.1f}s. This is the first thing to fail "
        f"if the host cannot carry the fleet, and the number above is the measurement to record.")


def test_no_two_nodes_share_a_csp_address(fleet):
    """Eight addresses per spacecraft in a five-bit space leaves room for four and no more.

    A collision does not announce itself: two nodes answering one address means a command reaches
    the wrong spacecraft's subsystem, and every console still reports nominal.
    """
    seen = {}
    for i in range(SATS):
        sat = spacecraft(i)
        for role in ROLES:
            addr = sat.address(role)
            assert addr not in seen, (
                f"sat{i} {role} and {seen[addr]} both claim CSP address {addr}")
            seen[addr] = f"sat{i} {role}"
    assert len(seen) == SATS * len(ROLES)


def test_each_node_announces_the_address_it_was_built_with(fleet):
    """Derived identity is only worth anything if the image agrees with the derivation."""
    for i in range(SATS):
        sat = spacecraft(i)
        for role in ("obc", "eps", "adcs"):
            console = _console(f"sat{i}-{role}.uart")
            needle = f"{role.upper()} (addr {sat.address(role)})"
            assert needle in console, (
                f"sat{i}'s {role} never announced {needle}; console:\n{console[-400:]}")


def test_every_spacecraft_answers_on_its_own_link(fleet):
    """One link each, and each one talking to the spacecraft whose SCID it carries."""
    for i in range(SATS):
        sat = spacecraft(i)
        link = SpaceLink(port=ports.link(i))
        link.connect(retries=60)
        try:
            station = GroundStation(link, target_apid=sat.apid, target_scid=sat.scid)
            report = station.ping(timeout=30)
            assert report is not None, (
                f"satellite {i} (SCID 0x{sat.scid:03X}) did not answer on port {ports.link(i)}")
        finally:
            link.close()


def test_the_fleet_runs_fast_enough_to_be_worth_using(fleet):
    """Reported, and asserted only against a floor nobody would call usable below.

    probe.sh owns the real speed check and explains why the number is meaningless on a contended
    host. Repeating that assertion here would create a second place to relax when CI runs
    somewhere slower, and a check that gets relaxed twice is a check nobody believes.
    """
    mon = Monitor(port=ports.monitor()).connect()
    try:
        mon.command('emulation RunFor "4"')
        info = mon.command("emulation GetTimeSourceInfo")
    finally:
        mon.close()
    load = None
    for line in info.splitlines():
        if "Cumulative load" in line:
            load = float(line.split(":")[-1].strip())
    assert load and load > 0, f"Renode reported no cumulative load:\n{info}"
    ratio = 1.0 / load
    print(f"\n  {SATS} spacecraft, {SATS * len(ROLES)} nodes: {ratio:.3f}x real time "
          f"(boot {fleet.boot_seconds:.1f}s)")
    assert ratio > 0.25, (
        f"{ratio:.3f}x real time for {SATS * len(ROLES)} nodes. Below a quarter of real time an "
        f"exercise's timeouts stop meaning what they say.")
