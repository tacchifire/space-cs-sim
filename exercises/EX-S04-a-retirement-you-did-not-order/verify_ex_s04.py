"""EX-S04: a STOP_SA directive with a valid MAC retired the operator's key, and the operator did
not send it.

SA management by telecommand is a real mission's job - CCSDS 355.1 Extended Procedures - and EX-S03
named it as the next thing: "a telecommand that can deactivate the operator's own SA is a denial of
service with a valid MAC on it." This exercise builds it and measures it.

The attacker is a PARTNER station: a second party holding its own Security Association (SPI 11) on
the same space link, which is how cross-supported ground networks work. Its frames verify - it holds
a real key - and that is precisely the point. Four runs:

  1. vulnerable: the partner sends STOP_SA(9). COMM retires the operator's SA, and the operator's
     next commands are refused DEACTIVATED. A valid MAC took the operator off the air.
  2. hardened: the same directive is REFUSED - a frame authenticated under SPI 11 may not retire an
     SA owned by someone else - and the operator is untouched.
  3. hardened, the feature still works: the OPERATOR retires its own SPI 9 (authenticated under its
     other SA, SPI 10, same owner). This is EX-S03's rotation, now done on the wire.
  4. the finding: from the SPI 9 side, run 1 and run 3 are the SAME observation. "Someone retired my
     key" and "I retired my key" both read as SPI 9 DEACTIVATED. Authentication answered WHO framed
     it and authorisation answered WHETHER they may; neither is ATTRIBUTION of who issued a
     deactivation the operator sees the effect of - which is what EX-S05 is about.

Authentication proves possession of a key. Authorisation binds an action to the key's owner. This
exercise is EX-G02 - authenticated is not authorised - one layer down, in key management.
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
VULN = OUT / "build-comm-s04-vuln" / "zephyr" / "zephyr.elf"   # any authenticated SA may retire any
HARD = OUT / "build-comm-s04-hard" / "zephyr" / "zephyr.elf"   # only the owner may
OBC = OUT / "build-obc-u02-hard" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))
COMMANDS = 4

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not OBC.exists(),
    reason=f"build them: make firmware-s04 firmware-u02 firmware-p1 firmware-s01 ({VULN})")


class Range:
    """One booted spacecraft and a ground station that can frame under any SA on demand.

    The station is the operator on SPI 9 to begin with; `_frame_under` switches which Security
    Association it authenticates the next frames with, which is all that separates the operator, its
    own second key, and the partner - on the wire they are three valid MACs and the exercise is
    about telling them apart by authority rather than by cryptography.
    """

    def __init__(self, comm: Path):
        self.comm = comm

    def __enter__(self):
        for name in ("s04-sat0-comm.uart", "s04-sat0-obc.uart", "s04-sat0-eps.uart",
                     "s04-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$comm=@{self.comm}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exs04-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("s04-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("s04-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.link = SpaceLink(port=link_port(0))
        self.link.connect(retries=60)
        #: The operator, on SPI 9. `uplink_key`/`require_signed_tm` are the mission-layer PUS
        #: trailer key (EX-S02) and do not change; `sdls_key`/`sdls_spi` are the Security
        #: Association and are what `_frame_under` swaps.
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
        return self.console("s04-sat0-comm.uart")

    def _listen(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.station.collect()
            time.sleep(0.25)

    def _frame_under(self, key: bytes, spi: int) -> None:
        self.station.sdls_key, self.station.sdls_spi = key, spi

    def commands(self, n: int, *, key: bytes, spi: int) -> int:
        """Send n connection tests under the given SA, return how many were acknowledged."""
        self._frame_under(key, spi)
        before = len(self.station.acknowledged)
        for _ in range(n):
            self.station.send_connection_test()
            self._listen(1.0)
        return len(self.station.acknowledged) - before

    def stop_sa(self, target_spi: int, *, key: bytes, spi: int) -> None:
        """Frame a STOP_SA(target) directive under the SA (`key`,`spi`) and let COMM act on it."""
        self._frame_under(key, spi)
        self.station.send_sa_stop(target_spi)
        self._listen(2.0)

    def settle(self) -> None:
        self._listen(6.0)


def test_a_partner_retires_the_operators_key_with_a_valid_mac():
    """Run 1, the attack. The partner holds a real key and its directive verifies; the vulnerable
    build acts on it because it never asks whether the partner OWNS the SA it named."""
    with Range(VULN) as r:
        r.settle()
        before = r.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        assert before == COMMANDS, (
            f"the operator's own SA did not work before the attack ({before}/{COMMANDS}); nothing "
            f"below this line would mean anything\n" + r.comm_console()[-900:])

        r.stop_sa(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)
        after = r.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        r.settle()

        assert after == 0, (
            f"{after}/{COMMANDS} still accepted after the partner's STOP_SA; the vulnerable build "
            f"was supposed to have retired the operator's SA\n" + r.comm_console()[-900:])
        console = r.comm_console()
        assert "SA STOP - SPI 9 DEACTIVATED by SPI 11" in console, (
            "the console does not record the partner (SPI 11) retiring the operator's SPI 9\n"
            + console[-900:])
        assert "DEACTIVATED" in console
        #: The operator's own refusals name SPI 9 - the key that was retired out from under them.
        assert r.station.link_refused_spi == SDLS_SPI, (
            f"the radio names SPI {r.station.link_refused_spi}; the operator was locked out of "
            f"SPI {SDLS_SPI}")


def test_the_authorised_build_refuses_a_directive_from_the_wrong_owner():
    """Run 2, the mitigation. The same directive, authenticated the same way, refused - because a
    frame under SPI 11 may not retire an SA owned by the operator - and the operator never notices."""
    with Range(HARD) as r:
        r.settle()
        r.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)  # baseline
        r.stop_sa(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)
        after = r.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        r.settle()

        assert after == COMMANDS, (
            f"the operator lost {COMMANDS - after} of {COMMANDS} commands after a directive that "
            f"should have been refused; the mitigation broke the operator instead of the attack\n"
            + r.comm_console()[-900:])
        console = r.comm_console()
        assert "SA STOP REFUSED" in console and "may not retire SPI 9" in console, (
            "the console does not record the unauthorised directive being refused for its reason\n"
            + console[-900:])
        assert "SPI 9 DEACTIVATED" not in console, (
            "the operator's SA was deactivated on the hardened build; the authorisation did not hold")
        #: The refusal names the REQUESTER, SPI 11 - the attribution the operator needs, and the
        #: thing the vulnerable build could not give because it never refused anything.
        assert r.station.link_refused_spi == SDLS_SPI_PARTNER, (
            f"the radio names SPI {r.station.link_refused_spi} as its refusal; the party that "
            f"overreached is SPI {SDLS_SPI_PARTNER}")


def test_the_operator_can_still_retire_its_own_key():
    """Run 3, the feature. The mitigation must not have made SA management impossible: the operator,
    authenticated under its OTHER association (SPI 10, same owner), retires its own SPI 9. That is
    EX-S03's rotation, performed by telecommand instead of by rebuild."""
    with Range(HARD) as r:
        r.settle()
        on_ten = r.commands(COMMANDS, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        assert on_ten == COMMANDS, (
            f"the operator's second SA did not work ({on_ten}/{COMMANDS}) before the retirement\n"
            + r.comm_console()[-900:])

        r.stop_sa(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        on_nine = r.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        still_ten = r.commands(COMMANDS, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        r.settle()

        console = r.comm_console()
        assert "SA STOP - SPI 9 DEACTIVATED by SPI 10" in console, (
            "the console does not record the operator legitimately retiring SPI 9 from SPI 10\n"
            + console[-900:])
        assert on_nine == 0, (
            f"{on_nine}/{COMMANDS} accepted on the RETIRED SPI 9; the operator's own retirement "
            f"did not take\n" + console[-900:])
        assert still_ten == COMMANDS, (
            f"only {still_ten}/{COMMANDS} accepted on SPI 10 after the retirement; the mitigation "
            f"broke the surviving association\n" + console[-900:])


def test_the_operator_cannot_tell_a_stolen_retirement_from_their_own():
    """Run 4, the finding, and the seed of EX-S05.

    From the SPI 9 side the attack (run 1) and the legitimate retirement (run 3) are the SAME
    observation: the operator's commands come back refused, DEACTIVATED, SPI 9. Authentication said
    who framed the directive and authorisation said whether they could; neither is ATTRIBUTION of a
    deactivation the operator only sees the effect of. The only place the difference exists is the
    on-board console - "by SPI 11" versus "by SPI 10" - which never leaves the spacecraft, exactly
    the gap EX-G04 and EX-U03 are about, now for SA management."""
    with Range(VULN) as attack:
        attack.settle()
        attack.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        attack.stop_sa(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)
        attack.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        attack.settle()
        stolen_locked_out = attack.station.link_refused_spi
        stolen_console = attack.comm_console()

    with Range(HARD) as legit:
        legit.settle()
        legit.commands(COMMANDS, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        legit.stop_sa(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        legit.commands(COMMANDS, key=SDLS_KEY, spi=SDLS_SPI)
        legit.settle()
        legit_locked_out = legit.station.link_refused_spi
        legit_console = legit.comm_console()

    #: The operator-visible fact is identical: SPI 9 is the association that stopped answering. From
    #: the ground, a stolen retirement and a deliberate one are the same event.
    assert stolen_locked_out == legit_locked_out == SDLS_SPI, (
        f"the two runs differ from the SPI 9 side (stolen={stolen_locked_out}, "
        f"legit={legit_locked_out}); the finding is that they do NOT")
    #: The ONLY witness to which it was is a console that never left either spacecraft: one names
    #: SPI 11 as the retiring party, the other names SPI 10, and neither reaches the operator. That
    #: gap - attribution of a deactivation, on the downlink - is EX-S05.
    assert "DEACTIVATED by SPI 11" in stolen_console, (
        "the vulnerable run's console does not attribute the retirement to the partner\n"
        + stolen_console[-600:])
    assert "DEACTIVATED by SPI 10" in legit_console, (
        "the legitimate run's console does not attribute the retirement to the operator\n"
        + legit_console[-600:])
