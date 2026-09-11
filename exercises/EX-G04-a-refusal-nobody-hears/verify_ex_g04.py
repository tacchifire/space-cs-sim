"""EX-G04: a control that cannot report is a control the ground cannot use.

The three, plus the one that keeps the fix honest:

  1. on the silent build, a refused command and a command that never arrived are the same
     observation from the ground - not similar, identical;
  2. on the reporting build, the refusal arrives as PUS 1,2, naming the request by its own
     header and giving a reason;
  3. the reporting build still executes what the authorised station is allowed to send, and
     does NOT report a failure for it - a build that reported failure for everything would pass
     1 and 2 and be worse than silence;
  4. the report is addressed to the station that sent the refused command, so on a broadcast
     channel the other operator is not told their own command failed.

EX-G02's authority check is ON in both halves. Nothing here is about whether the refusal
happens - it does, correctly, in both. This is about whether anyone off the spacecraft learns it.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.channel.link_channel import LinkChannel           # noqa: E402
from cuberange.gs.node import GroundStationNode                  # noqa: E402
from cuberange.paths import out_dir                              # noqa: E402
from cuberange.ports import (channel as channel_port, link as link_port,  # noqa: E402
                             monitor as monitor_port)
from cuberange.proto.pus import FAILURE_NOT_AUTHORISED           # noqa: E402
from cuberange.renode.monitor import Monitor                     # noqa: E402
from cuberange.renode.powerdomain import PowerDomain             # noqa: E402
from cuberange.renode.profile import profile_args                # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor         # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
SILENT = OUT / "build-obc-g04-vuln" / "zephyr" / "zephyr.elf"
REPORTING = OUT / "build-obc-g04-hard" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "30"))

pytestmark = pytest.mark.skipif(
    not SILENT.exists() or not REPORTING.exists(),
    reason=f"build them: make firmware-g04 ({SILENT})")


class Range:
    def __init__(self, obc_elf: Path):
        self.obc_elf = obc_elf
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)

    def __enter__(self):
        for name in ("comm.uart", "obc.uart", "eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                "-e", f"$obc=@{self.obc_elf}",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exg04-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.channel = LinkChannel(listen_port=channel_port(0), sat_port=link_port(0)).start()
        self.mon = Monitor(port=monitor_port()).connect()
        self.power = PowerDomain(self.mon, comm_elf=str(OUT / "build-comm/zephyr/zephyr.elf"))
        return self

    def __exit__(self, *exc):
        for obj, closer in ((getattr(self, "mon", None), "close"),
                            (getattr(self, "channel", None), "stop")):
            if obj is not None:
                getattr(obj, closer)()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def station(self, name: str, override: bool = False) -> GroundStationNode:
        return GroundStationNode(name, satellite=0, override=override,
                                 link_port=channel_port(0)).connect()


def test_a_refusal_and_a_lost_command_look_the_same():
    """The defect, stated as the comparison an operator actually has to make.

    One command is refused by the spacecraft. The other is corrupted in flight and dropped by
    COMM's CRC before the OBC ever sees it. Two entirely different fates, and the ground sees the
    same thing after both - which is why the operator debugs the radio.

    A first version simulated the lost command by stopping the channel, which closed the
    station's own socket and produced a ConnectionError: a third outcome, distinguishable from
    both, and therefore not the comparison this test is about.
    """
    with Range(SILENT) as r:
        backup = r.station("backup", override=True)
        try:
            backup.do("rail-off")                        # refused on board
            after_refusal = backup._station.await_refusal(timeout=6)

            # Now one the OBC never sees: a well-formed frame with one octet flipped, which
            # COMM's FECF check discards.
            captured = list(r.channel.uplink_frames)
            assert captured, "nothing was captured to corrupt"
            broken = bytearray(captured[-1])
            broken[-1] ^= 0x01
            r.channel.replay(bytes(broken))
            time.sleep(1.0)
            after_loss = backup._station.await_refusal(timeout=6)

            assert after_refusal is None and after_loss is None, (
                "the silent build reported something; this exercise has no premise left")
            assert "not authorised" in r.console("obc.uart"), (
                "the spacecraft did not actually refuse, so the two cases are identical for a "
                "reason other than the one this exercise is about")
        finally:
            backup.close()


def test_the_reporting_build_names_the_request_and_the_reason():
    with Range(REPORTING) as r:
        backup = r.station("backup", override=True)
        try:
            seq = backup.do("rail-off")
            refusal = backup._station.await_refusal(timeout=10)
            assert refusal is not None, (
                "no PUS 1,2 arrived. OBC console:\n" + r.console("obc.uart"))
            assert refusal.code == FAILURE_NOT_AUTHORISED, (
                f"the report says code {refusal.code}; an operator needs the reason, not just "
                f"the fact")
            assert refusal.seq_count == (seq & 0x3FFF), (
                f"the report names sequence {refusal.seq_count}, the command was {seq}. The "
                f"request id is the refused packet's own header, so a mismatch means the ground "
                f"cannot match the report to the command in its log")
        finally:
            backup.close()


def test_the_report_goes_to_the_station_that_was_refused():
    """Both stations hear every downlink. Only one of them sent the command."""
    with Range(REPORTING) as r:
        primary, backup = r.station("primary"), r.station("backup", override=True)
        try:
            backup.do("rail-off")
            assert backup._station.await_refusal(timeout=10) is not None
            primary._station.collect()
            assert not primary._station.refusals, (
                "the primary station recorded a refusal for a command it never sent")
        finally:
            primary.close()
            backup.close()


def test_the_reporting_build_still_obeys_the_authorised_station_silently():
    """A build that reported failure for everything would pass the two tests above."""
    with Range(REPORTING) as r:
        primary = r.station("primary")
        try:
            assert r.power.read_rail() is True
            primary.do("rail-off")
            deadline = time.time() + 20
            while r.power.read_rail() and time.time() < deadline:
                time.sleep(0.3)
            assert r.power.read_rail() is False, (
                "the authorised station's command did not execute. OBC console:\n"
                + r.console("obc.uart"))
            primary._station.collect()
            assert not primary._station.refusals, (
                f"a command that was carried out was also reported as refused: "
                f"{primary._station.refusals}")
        finally:
            primary.close()
