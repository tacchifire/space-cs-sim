"""EX-S03: a rotation that activated the new key and retired nothing.

There is no attacker in this exercise. The operator has activated a second Security Association
and is commanding on it, and the question is the one every rotation raises and almost nothing
answers: IS THE OLD KEY STILL GOOD?

Four runs, and the pair that matters is not 1-against-3:

  1. deactivated, the retired SA tested   -> 4 accepted on SPI 10, 4 REFUSED on SPI 9, and the
     radio names SPI 9 as the association it refused. The rotation is proven;
  2. deactivated, NOT tested             -> 4 accepted, nothing refused;
  3. still operational, the retired SA tested -> 8 accepted. The retired key opens the door;
  4. still operational, NOT tested       -> 4 accepted, nothing refused.

ROWS 2 AND 4 ARE THE SAME OBSERVATION. A spacecraft that retired the key and one that did not are
indistinguishable to an operator who never transmits on the old association - and transmitting on
a key you have just retired is not an obvious thing to do, which is why rotations go untested. The
exercise is the negative test, and rows 2 and 4 are why it has to exist.
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
from cuberange.keys import (SDLS_KEY, SDLS_KEY_ROTATED, SDLS_SPI,  # noqa: E402
                            SDLS_SPI_ROTATED)
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import link as link_port, monitor as monitor_port   # noqa: E402
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-comm-s03-vuln" / "zephyr" / "zephyr.elf"     # the retired SA stays operational
HARD = OUT / "build-comm-s03-hard" / "zephyr" / "zephyr.elf"     # the retired SA is deactivated
OBC = OUT / "build-obc-u02-hard" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))
COMMANDS = 4

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not OBC.exists(),
    reason=f"build them: make firmware-s03 firmware-u02 ({VULN})")


class Rotation:
    def __init__(self, comm: Path, *, test_retired: bool):
        self.comm = comm
        self.test_retired = test_retired

    def __enter__(self):
        for name in ("s03-sat0-comm.uart", "s03-sat0-obc.uart", "s03-sat0-eps.uart",
                     "s03-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$comm=@{self.comm}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exs03-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("s03-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("s03-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.link = SpaceLink(port=link_port(0))
        self.link.connect(retries=60)
        self.station = GroundStation(self.link, station_id=GROUND_STATIONS["primary"],
                                     vcid=GROUND_VCIDS["primary"],
                                     require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY,
                                     sdls_key=SDLS_KEY_ROTATED, sdls_spi=SDLS_SPI_ROTATED)
        return self

    def __exit__(self, *exc):
        if getattr(self, "link", None) is not None:
            self.link.close()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def _listen(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.station.collect()
            time.sleep(0.25)

    def fly(self) -> tuple[int, int]:
        """Returns (acknowledged on the new SA, acknowledged on the retired SA)."""
        self._listen(6.0)
        for _ in range(COMMANDS):
            self.station.send_connection_test()
            self._listen(1.0)
        on_new = len(self.station.acknowledged)

        if self.test_retired:
            #: One station, one virtual channel, one frame counter - only the association
            #: changes. Two stations would collide on the per-VC anti-replay counter and this
            #: exercise would be measuring EX-G03 instead.
            self.station.sdls_key, self.station.sdls_spi = SDLS_KEY, SDLS_SPI
            for _ in range(COMMANDS):
                self.station.send_connection_test()
                self._listen(1.0)
        self._listen(6.0)
        return on_new, len(self.station.acknowledged) - on_new


def test_the_retired_association_is_refused_and_the_radio_names_it():
    """The exercise. Proof, not hope."""
    with Rotation(HARD, test_retired=True) as p:
        on_new, on_retired = p.fly()
        assert on_new == COMMANDS, (
            f"{on_new} of {COMMANDS} accepted on the NEW association - the rotation itself is "
            f"broken and nothing below this line means anything\n"
            + p.console("s03-sat0-comm.uart")[-900:])
        assert on_retired == 0, (
            f"{on_retired} commands on the RETIRED association were accepted")
        assert p.station.link_frames_refused == COMMANDS, (
            f"the radio reports {p.station.link_frames_refused} refusals; {COMMANDS} frames were "
            f"sent on the retired association")
        #: Naming the association is what turns "something was refused" into "the key you
        #: retired was refused", and those send an operator to different places.
        assert p.station.link_refused_spi == SDLS_SPI, (
            f"the radio names SPI {p.station.link_refused_spi} as its last refusal; the retired "
            f"association is SPI {SDLS_SPI}")
        #: And the REASON, on the console, distinct from every other refusal this radio can make.
        assert "DEACTIVATED" in p.console("s03-sat0-comm.uart"), (
            "the frames were refused for some reason other than the SA being deactivated, so "
            "this run measured something else")


def test_the_new_association_alone_proves_nothing():
    """Row 2. This is a hardened spacecraft and the run is indistinguishable from row 4's."""
    with Rotation(HARD, test_retired=False) as p:
        on_new, on_retired = p.fly()
        assert (on_new, on_retired) == (COMMANDS, 0)
        assert p.station.link_frames_refused == 0
        assert "DEACTIVATED" not in p.console("s03-sat0-comm.uart")


def test_the_retired_association_still_opens_the_door():
    """Row 3. The rotation activated a new key and retired nothing."""
    with Rotation(VULN, test_retired=True) as p:
        on_new, on_retired = p.fly()
        assert on_new == COMMANDS
        assert on_retired == COMMANDS, (
            f"{on_retired} of {COMMANDS} accepted on the retired association; the vulnerable half "
            f"is supposed to accept all of them\n" + p.console("s03-sat0-comm.uart")[-900:])
        assert p.station.link_frames_refused == 0, (
            f"{p.station.link_frames_refused} refused, so something other than the SA state "
            f"turned these frames away")
        #: The radio reports, and has nothing to report. Note what this rules out: the
        #: vulnerable half is not quiet because its reporting is off - EX-U03's flag is ON here.
        assert p.station.link_refused_spi == 0


def test_the_vulnerable_spacecraft_also_looks_perfect_untested():
    """Row 4, and rows 2 and 4 together are the reason the negative test exists.

    Same numbers as row 2, from a spacecraft where the retired key still works. An operator who
    completes a rotation and checks that their new key commands the spacecraft has learned that
    their new key commands the spacecraft.
    """
    with Rotation(VULN, test_retired=False) as p:
        on_new, on_retired = p.fly()
        assert (on_new, on_retired) == (COMMANDS, 0)
        assert p.station.link_frames_refused == 0
        assert p.station.link_refused_spi == 0
