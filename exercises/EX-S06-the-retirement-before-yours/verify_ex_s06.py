"""EX-S06: two retirements of one SA, and the snapshot that keeps only the last.

EX-S05 put WHO retired WHICH SA on the beacon - but only the last retirement, a single value
overwritten each time. Its mitigation ended on that limit, and this is the exercise that takes it.
Authority is held OFF in both builds, exactly as in EX-S05, so the retirements land; what differs is
whether COMM reports the whole SA-management LOG or only its most recent entry.

The sequence is two retirements of the SAME association, SPI 9:

  1. a PARTNER (SPI 11) retires SPI 9  - a theft; the operator's primary SA goes dark.
  2. the OPERATOR (SPI 10) retires SPI 9 - a planned rotation that formally retires the same SPI,
     arriving after the theft.

  - vulnerable (snapshot): the beacon carries only the last, "SPI 9 retired by SPI 10". The theft
    is not merely lost - it is REATTRIBUTED to the operator's own key, so the ground sees a benign
    self-rotation and never learns a partner got there first.
  - hardened (log): the beacon carries both, oldest first - "SPI 9 by SPI 11" THEN "SPI 9 by
    SPI 10" - so the theft the later retirement overwrote is still on the downlink.
  - the feature still works: a single legitimate retirement logs exactly one entry (a log that
    always showed two, or fabricated one, would pass the test above and be useless), and a
    spacecraft that retired nothing sends the short beacon with no log at all.

The requester SPI is what the SDLS MAC PROVED, and the beacon is signed (EX-D01), so each log entry
is attribution rather than a claim. What it does not solve: the ring is bounded (SA_LOG_MAX), so a
sequence longer than that still loses its oldest events.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.gs.station import GroundStation                    # noqa: E402
from cuberange.identity import GROUND_STATIONS, GROUND_VCIDS      # noqa: E402
from cuberange.keys import (SDLS_KEY, SDLS_KEY_PARTNER, SDLS_KEY_ROTATED,  # noqa: E402
                            SDLS_SPI, SDLS_SPI_PARTNER, SDLS_SPI_ROTATED)
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import link as link_port, monitor as monitor_port   # noqa: E402
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-comm-s06-vuln" / "zephyr" / "zephyr.elf"   # reports only the last retirement
HARD = OUT / "build-comm-s06-hard" / "zephyr" / "zephyr.elf"   # reports the whole log
OBC = OUT / "build-obc-u02-hard" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not OBC.exists(),
    reason=f"build them: make firmware-s06 firmware-u02 firmware-p1 firmware-s01 ({VULN})")


class Range:
    """One booted spacecraft and a ground station that frames under any SA on demand and reads the
    beacon it sends back. Same shape as EX-S05's harness; what it watches is different - there it was
    whether the ground could see WHO retired the SA, here it is whether it can see a SEQUENCE of
    retirements or only the last."""

    def __init__(self, comm: Path):
        self.comm = comm

    def __enter__(self):
        for name in ("s06-sat0-comm.uart", "s06-sat0-obc.uart", "s06-sat0-eps.uart",
                     "s06-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$comm=@{self.comm}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exs06-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("s06-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("s06-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.link = SpaceLink(port=link_port(0))
        self.link.connect(retries=60)
        self.station = GroundStation(self.link, station_id=GROUND_STATIONS["primary"],
                                     vcid=GROUND_VCIDS["primary"],
                                     require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY,
                                     sdls_key=SDLS_KEY, sdls_spi=SDLS_SPI)
        return self

    def __exit__(self, *exc):
        if getattr(self, "link", None) is not None:
            self.link.close()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def comm_console(self) -> str:
        return self.console("s06-sat0-comm.uart")

    def _listen(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.station.collect()
            time.sleep(0.25)

    def retire(self, target_spi: int, *, key: bytes, spi: int) -> None:
        """Frame a STOP_SA(target) under the SA (`key`,`spi`) and let COMM act on it and beacon."""
        self.station.sdls_key, self.station.sdls_spi = key, spi
        self.station.send_sa_stop(target_spi)
        self._listen(2.0)

    def settle(self) -> None:
        #: Long enough for at least one 2 s beacon to carry whatever the spacecraft now knows.
        self._listen(7.0)


def test_a_theft_is_overwritten_by_a_later_retirement_of_the_same_sa():
    """Run 1, the finding. The partner retires SPI 9, then the operator retires the same SPI 9 - and
    the vulnerable snapshot keeps only the last, so the theft is not just lost but reattributed to
    the operator's own key. The console records both retirements; the beacon carries one."""
    with Range(VULN) as r:
        r.settle()
        r.retire(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)   # theft
        r.retire(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)   # planned rotation
        r.settle()

        console = r.comm_console()
        #: Both retirements really happened - the console, which never leaves the spacecraft, has both.
        assert "DEACTIVATED by SPI 11" in console, ("the partner's theft did not land\n"
                                                    + console[-900:])
        assert "DEACTIVATED by SPI 10" in console, ("the operator's retirement did not land\n"
                                                    + console[-900:])
        #: But the beacon carries only the last: one entry, attributing SPI 9 to the operator's own
        #: SPI 10. The theft by SPI 11 is gone from the downlink.
        assert len(r.station.sa_retire_log) == 1, (
            f"the vulnerable build reported {len(r.station.sa_retire_log)} log entries; the "
            f"snapshot is supposed to carry exactly one: {r.station.sa_retire_log}")
        assert r.station.sa_retire_log[0] == (SDLS_SPI, SDLS_SPI_ROTATED), (
            f"the surviving entry is {r.station.sa_retire_log[0]}; the finding is that the last "
            f"retirement ({(SDLS_SPI, SDLS_SPI_ROTATED)}) overwrote the theft")
        assert (SDLS_SPI, SDLS_SPI_PARTNER) not in r.station.sa_retire_log, (
            "the theft (SPI 9 by SPI 11) is on the beacon; on the vulnerable build the later "
            "retirement of the same SPI is supposed to have overwritten it")


def test_the_history_shows_the_theft_the_snapshot_lost():
    """Run 2, the mitigation. The same two retirements, and now the beacon carries both in arrival
    order: SPI 9 by SPI 11 (the theft) THEN SPI 9 by SPI 10. The operator can see a partner got
    there first."""
    with Range(HARD) as r:
        r.settle()
        r.retire(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)   # theft
        r.retire(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)   # planned rotation
        r.settle()

        assert r.station.sa_retire_log == [(SDLS_SPI, SDLS_SPI_PARTNER),
                                           (SDLS_SPI, SDLS_SPI_ROTATED)], (
            f"the log is {r.station.sa_retire_log}; the hardened build must carry both retirements "
            f"of SPI 9, oldest first - the theft by SPI {SDLS_SPI_PARTNER}, then the rotation by "
            f"SPI {SDLS_SPI_ROTATED}\n" + r.comm_console()[-900:])
        #: The theft is the FIRST entry - the one the snapshot in run 1 overwrote.
        assert r.station.sa_retire_log[0] == (SDLS_SPI, SDLS_SPI_PARTNER)


def test_a_single_legitimate_retirement_logs_exactly_one_entry():
    """Run 3, the feature. A log that always showed two entries, or fabricated one, would pass run 2
    and be useless. When only the operator retires its own SPI 9 from SPI 10 - no attacker - the log
    has exactly one entry, attributed to SPI 10."""
    with Range(HARD) as r:
        r.settle()
        r.retire(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        r.settle()

        assert r.station.sa_retire_log == [(SDLS_SPI, SDLS_SPI_ROTATED)], (
            f"a single legitimate retirement logged {r.station.sa_retire_log}; it must be exactly "
            f"one entry attributed to the operator's SPI {SDLS_SPI_ROTATED}\n"
            + r.comm_console()[-900:])


def test_no_retirement_leaves_the_log_empty():
    """Run 4, the anti-vacuous check and the length feature test. A spacecraft that retired nothing
    sends the short beacon with no SA-management log - not a fabricated 'SPI 0 retired SPI 0', which
    is the trap EX-S05 named. So an empty log means nothing happened, not that the report is broken."""
    with Range(HARD) as r:
        r.settle()
        assert r.station.sa_retire_log == [], (
            f"the spacecraft reported an SA-management log without any retirement: "
            f"{r.station.sa_retire_log}")
        assert r.station.sa_retired_target is None and r.station.sa_retired_by is None, (
            "a retirement was attributed on a spacecraft that retired nothing - the length is "
            "supposed to be the feature test, and a 14-octet beacon carries no attribution")
