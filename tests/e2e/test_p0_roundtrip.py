"""P0 acceptance: a real PUS 17,1 goes out and a real PUS 17,2 comes back.

Renode runs free (`start`) here rather than being stepped, because the ground station is an
independent process talking over TCP and the exchange is millisecond-scale. Exercises that need
determinism use the CI profile instead - see the design's execution-profile section.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink          # noqa: E402
from cuberange.gs.station import GroundStation   # noqa: E402
from cuberange.proto.pus import SERVICE_TEST, SUBTYPE_CONNECTION_TEST_REPORT  # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = Path(os.environ.get("OUT", "/tmp/cuberange"))
LINK_PORT = 3777
MONITOR_PORT = 3778
BOOT_SETTLE_S = 3.0


def _dump_consoles():
    """Print both node consoles. When the round trip fails, these say exactly how far it got."""
    for name in ("comm.uart", "obc.uart"):
        path = OUT / name
        print(f"--- {name} ---")
        if path.exists():
            print(path.read_text(errors="replace"))
        else:
            print("(missing)")


@pytest.fixture
def renode():
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("build-comm", "build-obc"):
        elf = OUT / name / "zephyr" / "zephyr.elf"
        if not elf.exists():
            pytest.skip(f"{elf} missing - run 'make firmware-p0' first")
    for name in ("comm.uart", "obc.uart"):
        (OUT / name).unlink(missing_ok=True)

    proc = subprocess.Popen(
        ["./renode", "--disable-xwt", "--plain", "--hide-analyzers", "--hide-log",
         "--port", str(MONITOR_PORT),
         "-e", f"include @{REPO}/scripts/multi-node/p0.resc",
         "-e", "start"],
        cwd=RENODE_DIR, stdout=open(OUT / "p0-renode.log", "w"),
        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    yield proc
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except OSError:
        pass
    proc.wait(timeout=10)


def test_pus17_round_trip(renode):
    link = SpaceLink(port=LINK_PORT)
    link.connect()
    try:
        time.sleep(BOOT_SETTLE_S)      # both nodes must have their CAN interfaces up
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
        assert report.dest_id == 0x0042, f"report addressed to {report.dest_id:#x}, not the ground"
    finally:
        link.close()


def test_link_carries_no_console_output(renode):
    """The space link must be free of Zephyr's console. If this fails, someone pointed the link at
    usart3 or enabled logging on usart2."""
    link = SpaceLink(port=LINK_PORT)
    link.connect()
    try:
        time.sleep(BOOT_SETTLE_S)
        link.poll()      # drain whatever the firmware volunteered
        assert b"Booting Zephyr" not in bytes(link.raw_rx), (
            f"console output leaked onto the link: {bytes(link.raw_rx)[:120]!r}")
    finally:
        link.close()
