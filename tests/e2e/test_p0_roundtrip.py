"""P0 acceptance: a real PUS 17,1 goes out and a real PUS 17,2 comes back.

Renode runs free (`start`) here rather than being stepped, because the ground station is an
independent process talking over TCP and the exchange is millisecond-scale. Exercises that need
determinism use the CI profile instead - see the design's execution-profile section.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports  # noqa: E402
from cuberange.identity import GROUND_SOURCE_ID                  # noqa: E402
from cuberange.paths import out_dir                             # noqa: E402
from cuberange.gs.link import SpaceLink          # noqa: E402
from cuberange.gs.station import GroundStation   # noqa: E402
from cuberange.proto.pus import SERVICE_TEST, SUBTYPE_CONNECTION_TEST_REPORT  # noqa: E402
from cuberange.renode.profile import profile_args
from cuberange.renode.supervisor import Outcome, RenodeSupervisor  # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
LINK_PORT = ports.link(0)
MONITOR_PORT = ports.monitor()
# Upper bound on how long the two nodes may take to come up, not a fixed wait - the code waits
# for their own readiness lines and only uses this to give up. Raise it on a slow host.
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "30"))


def _dump_consoles():
    """Print both node consoles. When the round trip fails, these say exactly how far it got."""
    for name in ("comm.uart", "obc.uart"):
        path = OUT / name
        print(f"--- {name} ---")
        if path.exists():
            print(path.read_text(errors="replace"))
        else:
            print("(missing)")


def _wait_ready(timeout: float = BOOT_TIMEOUT_S) -> None:
    """Block until COMM and OBC have both announced themselves on their consoles.

    This replaced a fixed sleep. GroundStation.ping sends its request once and then only listens,
    so a request issued before both CAN interfaces are up is dropped and the whole timeout is spent
    waiting for an answer to a question nobody heard. Waiting on the nodes' own readiness lines is
    both faster on a quick host and correct on a slow one.
    """
    deadline = time.time() + timeout
    want = {"comm.uart": "COMM ready", "obc.uart": "OBC ready"}
    while time.time() < deadline:
        seen = {}
        for name, needle in want.items():
            path = OUT / name
            seen[name] = path.exists() and needle in path.read_text(errors="replace")
        if all(seen.values()):
            return
        time.sleep(0.2)
    missing = [n for n, needle in want.items()
               if not ((OUT / n).exists() and needle in (OUT / n).read_text(errors="replace"))]
    _dump_consoles()
    raise AssertionError(f"nodes never reported ready within {timeout}s: {missing}")


@pytest.fixture
def renode():
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("build-comm", "build-obc"):
        elf = OUT / name / "zephyr" / "zephyr.elf"
        if not elf.exists():
            pytest.skip(f"{elf} missing - run 'make firmware-p0' first")
    for name in ("comm.uart", "obc.uart"):
        (OUT / name).unlink(missing_ok=True)

    # Supervised, like every other launch. This is the first thing most people run, and for a
    # while it was the one path with no watchdog and no memory ceiling - which is backwards, since
    # a hung Renode here leaks 3.7 GB in 100 s and on a small board that is an OOM kill rather
    # than a slow test.
    sup = RenodeSupervisor(cwd=RENODE_DIR)
    with sup.launch(
        ["./renode", "--disable-xwt", "--plain", "--hide-analyzers", "--hide-log",
         "--port", str(MONITOR_PORT),
         *profile_args(),
         "-e", f"$out=@{OUT}",
         "-e", f"include @{REPO}/scripts/multi-node/p0.resc",
         "-e", "start"],
        OUT / "p0-renode.log",
    ) as run:
        yield run
        result = run.result()

    if result.outcome in (Outcome.TIMEOUT, Outcome.RSS_EXCEEDED):
        pytest.skip(f"Renode {result.outcome.value} after {result.wall_s:.0f}s "
                    f"(peak RSS {result.peak_rss_mb:.0f} MB) - retryable infrastructure failure, "
                    f"see {result.log_path}")


def test_pus17_round_trip(renode):
    link = SpaceLink(port=LINK_PORT)
    link.connect()
    try:
        _wait_ready()
        station = GroundStation(link)
        report = station.ping(timeout=20)
        if report is None:
            _dump_consoles()
            print(f"undecodable frames: {station.undecodable}")
            print(f"raw link bytes: {bytes(link.raw_rx)[:200]!r}")
        assert report is not None, "no PUS 17,2 report came back"
        assert (report.service, report.subtype) == (SERVICE_TEST, SUBTYPE_CONNECTION_TEST_REPORT)
        # The OBC echoes the TC's source ID into the TM's destination ID, so a report that came
        # back for someone else's command would be caught here.
        assert report.dest_id == GROUND_SOURCE_ID, f"report addressed to {report.dest_id:#x}, not the ground"
    finally:
        link.close()


def test_link_carries_no_console_output(renode):
    """The space link must be free of Zephyr's console. If this fails, someone pointed the link at
    usart3 or enabled logging on usart2."""
    link = SpaceLink(port=LINK_PORT)
    link.connect()
    try:
        _wait_ready()
        link.poll()      # drain whatever the firmware volunteered
        assert b"Booting Zephyr" not in bytes(link.raw_rx), (
            f"console output leaked onto the link: {bytes(link.raw_rx)[:120]!r}")
    finally:
        link.close()
