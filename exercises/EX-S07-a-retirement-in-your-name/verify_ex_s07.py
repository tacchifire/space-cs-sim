"""EX-S07: an SA retirement in your name, and the one record that can say it was not you.

EX-S06 gave the ground the SEQUENCE of SA-management events, each attributed to the SA whose MAC
verified the directive. Its mitigation ended on the limit this takes: attribution names the KEY,
never the hand holding it. An intruder holding the operator's SPI 10 key retires SPI 9, and the log
faithfully reports "SPI 9 retired by SPI 10" - the operator's own SA.

This is a HOST-SIDE exercise, like EX-G01. The firmware is EX-S06's hardened build in both runs; the
spacecraft log is the same octets either way. The one difference is on the ground: whether the
station knows which SAs are its own (`owned_spis`) and reconciles the log against its own ledger
of the directives it sent.

  1. trusting: every instrument this range built says nothing is wrong or says it was you -
     unexplained_commands 0 (a directive is not a telecommand), link refused 0 (the key was real),
     one more frame received than sent (someone transmitted, not what), and the log names SPI 10.
  2. reconciling: the same run, and the entry the station never sent is named - SPI 9 retired by
     SPI 10, unexplained.
  3. the feature still works: the operator's own rotation reconciles to nothing, and EX-U02's
     counter no longer reads it as a lost command (it did, -1, until directives left commands_sent).
  4. and it answers one question only: a partner's retirement under the partner's own SA is not
     charged to you - EX-S05 and EX-S06 already name the partner.
  5. and it is a detector because nothing prevents this: EX-S04's owner check, on its own build,
     refuses the partner and authorises the copy of your key. That build reports no SA log, and
     the ledger reads it as nothing unexplained - a detector whose input does not exist.
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
from cuberange.keys import (SA_OWNER_OPERATOR, SA_OWNER_PARTNER, SA_OWNERS,  # noqa: E402
                            SDLS_KEY, SDLS_KEY_PARTNER, SDLS_KEY_ROTATED, SDLS_SPI,
                            SDLS_SPI_PARTNER, SDLS_SPI_ROTATED)
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import link as link_port, monitor as monitor_port   # noqa: E402
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
COMM = OUT / "build-comm-s06-hard" / "zephyr" / "zephyr.elf"   # EX-S06's log, in both runs
#: EX-S04's hardened COMM: authority ON, no SA-management report. Run 5 only.
COMM_AUTHORITY = OUT / "build-comm-s04-hard" / "zephyr" / "zephyr.elf"
OBC = OUT / "build-obc-u02-hard" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

#: The operator's SAs, derived from the owner table rather than written out: SPI 9 and SPI 10.
OPERATOR_SPIS = frozenset(spi for spi, owner in SA_OWNERS.items() if owner == SA_OWNER_OPERATOR)

pytestmark = pytest.mark.skipif(
    not COMM.exists() or not OBC.exists(),
    reason=f"build them: make firmware-s06 firmware-u02 firmware-p1 firmware-s01 ({COMM})")


class Range:
    """One booted spacecraft and the OPERATOR's station, reading the beacon. `reconcile` is the
    whole difference between the two halves of this exercise: whether the operator's station knows
    which SAs are its own. Nothing else about the range, the firmware or the station changes."""

    def __init__(self, *, reconcile: bool, comm: Path = COMM):
        self.reconcile = reconcile
        self.comm = comm

    def __enter__(self):
        for name in ("s07-sat0-comm.uart", "s07-sat0-obc.uart", "s07-sat0-eps.uart",
                     "s07-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$comm=@{self.comm}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exs07-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("s07-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("s07-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.link = SpaceLink(port=link_port(0))
        self.link.connect(retries=60)
        self.station = GroundStation(self.link, station_id=GROUND_STATIONS["primary"],
                                     vcid=GROUND_VCIDS["primary"],
                                     require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY,
                                     sdls_key=SDLS_KEY, sdls_spi=SDLS_SPI,
                                     owned_spis=OPERATOR_SPIS if self.reconcile else None)
        return self

    def __exit__(self, *exc):
        if getattr(self, "link", None) is not None:
            self.link.close()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def comm_console(self) -> str:
        return self.console("s07-sat0-comm.uart")

    def _listen(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.station.collect()
            time.sleep(0.25)

    def settle(self) -> None:
        #: Long enough for at least one 2 s beacon to carry whatever the spacecraft now knows.
        self._listen(7.0)

    def someone_else_retires(self, target_spi: int, *, key: bytes, spi: int) -> None:
        """A SEPARATE station on the same link frames the directive. Its sends are its own: nothing
        it transmits enters the operator's ledger, which is the point - one station switching keys
        would record the intruder's directive as the operator's."""
        other = GroundStation(self.link, sdls_key=key, sdls_spi=spi)
        other.send_sa_stop(target_spi)
        self._listen(2.0)

    def operator_retires(self, target_spi: int) -> None:
        """The operator's own station, framing under its own second SA, SPI 10."""
        self.station.sdls_key, self.station.sdls_spi = SDLS_KEY_ROTATED, SDLS_SPI_ROTATED
        self.station.send_sa_stop(target_spi)
        self._listen(2.0)


def test_with_your_key_every_instrument_says_it_was_you():
    """Run 1, the finding. A second station holding SPI 10's key retires SPI 9. Nothing the
    operator's station has flags it, and the one record that says WHAT happened names the
    operator."""
    with Range(reconcile=False) as r:
        r.settle()                  # acquisition: every baseline taken before anything happens
        r.someone_else_retires(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        r.settle()
        st = r.station

        assert "DEACTIVATED by SPI 10" in r.comm_console(), (
            "the retirement did not land\n" + r.comm_console()[-900:])
        assert st.sa_retire_log == [(SDLS_SPI, SDLS_SPI_ROTATED)], (
            f"the log is {st.sa_retire_log}; it should name the operator's own SPI 10")
        #: The instruments this range built to notice intruders, one by one.
        assert st.unexplained_commands == 0, (
            f"unexplained_commands read {st.unexplained_commands}; a directive is not a "
            f"telecommand, so EX-U02's counter is expected to be blind to it")
        assert st.link_frames_refused == 0, "the radio refused something; the key was real"
        #: One frame arrived and this station sent none: someone else transmitted, and nothing says
        #: what. Run 4 reads the same +1 for a partner's directive under the partner's own SA.
        assert st.commands_sent == 0 and st.sa_directives_sent == []
        assert st.link_frames_received == 1, (
            f"the radio counted {st.link_frames_received} frames; one was sent, by the other "
            f"station")
        #: And the station that trusts attribution cannot say it was not you.
        assert st.unexplained_sa_retirements is None


def test_your_ledger_names_the_retirement_you_never_sent():
    """Run 2, the mitigation. The same run, with a station that knows SPI 9 and SPI 10 are its own:
    the entry in its name that it never sent is named."""
    with Range(reconcile=True) as r:
        r.settle()
        r.someone_else_retires(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        r.settle()

        assert r.station.sa_directives_sent == [], "the operator's station sent nothing in this run"
        assert r.station.unexplained_sa_retirements == [(SDLS_SPI, SDLS_SPI_ROTATED)], (
            f"expected SPI 9 retired by SPI 10 to be unexplained; got "
            f"{r.station.unexplained_sa_retirements} from log {r.station.sa_retire_log}\n"
            + r.comm_console()[-900:])


def test_your_own_rotation_reconciles_to_nothing():
    """Run 3, the feature. A detector that fired on its own operator would be switched off by the
    end of the week. The operator retires SPI 9 from its own SPI 10: the log names SPI 10, the
    ledger holds the directive, and nothing is unexplained - and EX-U02's counter reads 0, not the
    -1 it read while a directive was counted as a telecommand."""
    with Range(reconcile=True) as r:
        r.settle()
        r.operator_retires(SDLS_SPI)
        r.settle()

        assert r.station.sa_retire_log == [(SDLS_SPI, SDLS_SPI_ROTATED)], (
            f"the log is {r.station.sa_retire_log}\n" + r.comm_console()[-900:])
        assert r.station.sa_directives_sent == [(SDLS_SPI, SDLS_SPI_ROTATED)]
        assert r.station.unexplained_sa_retirements == [], (
            f"the operator's own rotation was reported as unexplained: "
            f"{r.station.unexplained_sa_retirements}")
        assert r.station.unexplained_commands == 0, (
            f"unexplained_commands read {r.station.unexplained_commands} after the operator's own "
            f"directive; a directive is not a telecommand and must not count as one")


def test_a_partners_retirement_is_not_charged_to_you():
    """Run 4, the scope. A partner retiring SPI 9 under its own SPI 11 is not a use of YOUR key - it
    is attribution EX-S05 and EX-S06 already make visible. The detector answers one question."""
    with Range(reconcile=True) as r:
        r.settle()
        r.someone_else_retires(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)
        r.settle()

        assert r.station.sa_retire_log == [(SDLS_SPI, SDLS_SPI_PARTNER)], (
            f"the log is {r.station.sa_retire_log}\n" + r.comm_console()[-900:])
        #: The frame counter reads exactly what run 1 read - one frame this station did not send.
        #: On a link a partner shares that is routine, which is why it cannot be the detector.
        assert r.station.link_frames_received == 1, (
            f"the radio counted {r.station.link_frames_received} frames; the partner sent one")
        assert r.station.unexplained_sa_retirements == [], (
            "a partner's retirement under the partner's own SA was charged to the operator")


@pytest.mark.skipif(not COMM_AUTHORITY.exists(),
                    reason=f"build it: make firmware-s04 ({COMM_AUTHORITY})")
def test_the_owner_check_authorises_it_and_without_a_log_the_ledger_sees_nothing():
    """Run 5, why this is a detector at all. EX-S04's hardened COMM asks whether the requester OWNS
    the SA it retires, and a copy of the owner's key is the owner as far as a MAC can tell.

    The partner goes first, and is refused: the positive control that the owner check is in this
    build, without which "authorised" could mean a build that never checked. Then the copy of SPI
    10's key, authorised, and SPI 9 goes dark with the owner's name on it.

    That build reports no SA-management log, and the reconciling station reads the absence as
    nothing unexplained. Its beacon is the same fourteen octets a reporting spacecraft sends when
    nothing was retired - the length is EX-S06's feature test, and it cannot say which."""
    with Range(reconcile=True, comm=COMM_AUTHORITY) as r:
        r.settle()
        r.someone_else_retires(SDLS_SPI, key=SDLS_KEY_PARTNER, spi=SDLS_SPI_PARTNER)
        r.someone_else_retires(SDLS_SPI, key=SDLS_KEY_ROTATED, spi=SDLS_SPI_ROTATED)
        r.settle()
        con = r.comm_console()

        assert (f"SPI {SDLS_SPI_PARTNER} (owner {SA_OWNER_PARTNER}) may not retire SPI {SDLS_SPI} "
                f"(owner {SA_OWNER_OPERATOR})") in con, (
            "the partner was not refused, so this build is not checking ownership and the next "
            "line proves nothing\n" + con[-900:])
        assert (f"SPI {SDLS_SPI} DEACTIVATED by SPI {SDLS_SPI_ROTATED} "
                f"(owner {SA_OWNER_OPERATOR})") in con, (
            "the owner check refused a copy of the owner's key; EX-S07's premise is that it "
            "cannot\n" + con[-900:])
        assert r.station.sa_retire_log == [], (
            f"EX-S04's build reported an SA log: {r.station.sa_retire_log}")
        assert r.station.unexplained_sa_retirements == [], (
            "expected the ledger to read a spacecraft that reports nothing as nothing unexplained; "
            f"got {r.station.unexplained_sa_retirements}")
