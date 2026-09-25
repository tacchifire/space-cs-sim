"""EX-S05: an SA was retired, and the ground can finally see WHO did it.

EX-S04 refused an unauthorised retirement, and its mitigation ended on the gap this exercise
closes: from the ground, a stolen retirement and the operator's own rotation are the same
observation - SPI 9 stops answering - because the only record of who retired the SA is a COMM
console that never leaves the spacecraft. This is the detection half. Authority is held OFF in both
builds (a partner CAN retire the operator's SA here, on purpose, so there is a retirement to
attribute); what differs is whether COMM REPORTS who retired which SA on the beacon.

  1. vulnerable: the partner retires SPI 9 and the operator goes dark, and the ground learns
     nothing - the beacon carries no attribution, so a theft is indistinguishable from a rotation.
  2. hardened: the same retirement, and now the beacon names it - SPI 9 was retired BY SPI 11. The
     operator sees it was not their own SPI 10.
  3. hardened, the feature still works: the OPERATOR retires its own SPI 9 from SPI 10, and the
     beacon attributes it to SPI 10 - a rotation reads as self, not as an alarm. The report names
     the actual retirer rather than crying "attack" at every retirement.
  4. the finding: on the vulnerable build a theft (by SPI 11) and a rotation (by SPI 10) leave the
     SAME ground-visible state - no attribution at all - which is exactly EX-S04's blind spot.

The attribution is trustworthy because the beacon is signed (EX-D01) and the requester SPI is what
the SDLS MAC PROVED, not what a header asserted. What it does not do is keep a history: only the
last retirement is carried, the single-value limit EX-S03's refused-SPI has, and EX-S06's to take.
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
VULN = OUT / "build-comm-s05-vuln" / "zephyr" / "zephyr.elf"   # retires in silence
HARD = OUT / "build-comm-s05-hard" / "zephyr" / "zephyr.elf"   # reports who retired what
OBC = OUT / "build-obc-u02-hard" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))
COMMANDS = 4

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not OBC.exists(),
    reason=f"build them: make firmware-s05 firmware-u02 firmware-p1 firmware-s01 ({VULN})")


class Range:
    """One booted spacecraft and a ground station that frames under any SA on demand, and reads the
    beacon the spacecraft sends back. Same shape as EX-S04's harness; what it watches is different -
    there it was whether the retirement happened, here it is whether the ground can see who caused
    it."""

    def __init__(self, comm: Path):
        self.comm = comm

    def __enter__(self):
        for name in ("s05-sat0-comm.uart", "s05-sat0-obc.uart", "s05-sat0-eps.uart",
                     "s05-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$comm=@{self.comm}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exs05-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("s05-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("s05-sat0-obc.uart")
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
        return self.console("s05-sat0-comm.uart")

    def _listen(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.station.collect()
            time.sleep(0.25)

    def commands(self, n: int, *, key: bytes, spi: int) -> int:
        self.station.sdls_key, self.station.sdls_spi = key, spi
        before = len(self.station.acknowledged)
        for _ in range(n):
            self.station.send_connection_test()
            self._listen(1.0)
        return len(self.station.acknowledged) - before

    def retire(self, target_spi: int, *, key: bytes, spi: int) -> None:
        """Frame a STOP_SA(target) under the SA (`key`,`spi`) and let COMM act on it and beacon."""
        self.station.sdls_key, self.station.sdls_spi = key, spi
        self.station.send_sa_stop(target_spi)
        self._listen(2.0)

    def settle(self) -> None:
        #: Long enough for at least one 2 s beacon to carry whatever the spacecraft now knows.
        self._listen(7.0)


def test_a_stolen_retirement_is_invisible_without_the_report():
    """Run 1, the blind spot. The partner retires the operator's SPI 9 and the operator falls off
    the air - but the beacon says nothing about who did it, so from the ground it is a key that
    stopped working for no visible reason."""
    with Range(VULN) as r:
        r.settle()
        before = r.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        assert before == COMMANDS, (
            f"the operator's SA did not work before the attack ({before}/{COMMANDS})\n"
            + r.comm_console()[-900:])
        r.retire(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)
        after = r.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        r.settle()

        assert after == 0, (
            f"{after}/{COMMANDS} still accepted; the partner's retirement did not land on the "
            f"vulnerable build\n" + r.comm_console()[-900:])
        #: The console knows - it always did - but the ground does not.
        assert "DEACTIVATED by SPI 11" in r.comm_console()
        assert r.station.sa_retired_by is None and r.station.sa_retired_target is None, (
            f"the vulnerable build reported an attribution (by SPI {r.station.sa_retired_by}); it "
            f"is supposed to retire in silence - that is the whole blind spot")


def test_the_hardened_build_names_who_retired_the_sa():
    """Run 2, the mitigation. The same theft, and now the beacon carries who did it: SPI 9 retired
    BY SPI 11. The operator can see it was not their own key."""
    with Range(HARD) as r:
        r.settle()
        r.retire(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)
        r.settle()

        assert r.station.sa_retired_target == SDLS_SPI, (
            f"the beacon names SPI {r.station.sa_retired_target} as the retired association; the "
            f"operator's is SPI {SDLS_SPI}\n" + r.comm_console()[-900:])
        assert r.station.sa_retired_by == SDLS_SPI_PARTNER, (
            f"the beacon attributes the retirement to SPI {r.station.sa_retired_by}; the partner "
            f"that did it is SPI {SDLS_SPI_PARTNER} - and that it is NOT the operator's own SPI "
            f"{SDLS_SPI_ROTATED} is the whole point")


def test_a_legitimate_retirement_is_attributed_to_the_operator():
    """Run 3, the feature. The mitigation must name the ACTUAL retirer, not shout "attack" at every
    retirement: when the operator retires its own SPI 9 from SPI 10, the beacon says so, and a
    rotation reads as self."""
    with Range(HARD) as r:
        r.settle()
        on_ten = r.commands(COMMANDS, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        assert on_ten == COMMANDS, (
            f"the operator's second SA did not work ({on_ten}/{COMMANDS})\n"
            + r.comm_console()[-900:])
        r.retire(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        r.settle()

        assert r.station.sa_retired_target == SDLS_SPI
        assert r.station.sa_retired_by == SDLS_SPI_ROTATED, (
            f"the beacon attributes the operator's own retirement to SPI {r.station.sa_retired_by}; "
            f"it was ordered from SPI {SDLS_SPI_ROTATED} and should read as self, not as SPI "
            f"{SDLS_SPI_PARTNER}\n" + r.comm_console()[-900:])


def test_without_the_report_a_theft_and_a_rotation_are_the_same():
    """Run 4, the finding, and EX-S04's does-not-solve made concrete. On the vulnerable build the
    partner's theft and the operator's own rotation both leave the ground with NO attribution -
    identical - which is why the report has to exist. The hardened build separates them (runs 2 and
    3); here we show the vulnerable one cannot."""
    with Range(VULN) as theft:
        theft.settle()
        theft.retire(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)
        theft.settle()
        stolen_by = theft.station.sa_retired_by

    with Range(VULN) as rotation:
        rotation.settle()
        rotation.retire(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        rotation.settle()
        rotated_by = rotation.station.sa_retired_by

    assert stolen_by is None and rotated_by is None, (
        f"the vulnerable build distinguished a theft (by {stolen_by}) from a rotation (by "
        f"{rotated_by}); the finding is that from the ground it CANNOT - both are silence")
