"""Two ground station nodes, one spacecraft, one channel between them.

The request this range was built for asks for ground station nodes, plural. Until now that meant
two integers inside one process: there was no second console for a second student to sit at, and
there could not be, because a Renode socket terminal serves exactly one client.

So the channel serves several, and these are two real nodes with their own identities, their own
logs and their own view of what they may ask for. What the exercise turns on is visible here as
behaviour rather than as a flag:

  - the backup station refuses its own forbidden command, on the ground, before transmitting;
  - `--override` sends it anyway, and the vulnerable spacecraft obeys, because the check the
    backup station just stepped past was never on the spacecraft;
  - both stations hear every downlink, and each keeps only the reports addressed to it.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.channel.link_channel import LinkChannel           # noqa: E402
from cuberange.gs.node import GroundStationNode, Refused         # noqa: E402
from cuberange.identity import spacecraft                        # noqa: E402
from cuberange.paths import out_dir                              # noqa: E402
from cuberange.ports import (channel as channel_port, link as link_port,  # noqa: E402
                             monitor as monitor_port)
from cuberange.renode.monitor import Monitor                     # noqa: E402
from cuberange.renode.powerdomain import PowerDomain             # noqa: E402
from cuberange.renode.profile import profile_args                # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor         # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = REPO / "exercises" / "EX-G02-unauthorized-authority" / "scenario.resc"
VULN_ELF = OUT / "build-obc-g02-vuln" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "30"))
RAIL_TIMEOUT_S = float(os.environ.get("CUBERANGE_RAIL_TIMEOUT_S", "20"))

pytestmark = pytest.mark.skipif(
    not VULN_ELF.exists(), reason=f"build it: make firmware-g02 ({VULN_ELF})")


class Range:
    """The scenario, plus a channel in front of it that more than one station can attach to."""

    def __init__(self):
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)

    def __enter__(self):
        for name in ("comm.uart", "obc.uart", "eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                "-e", f"$obc=@{VULN_ELF}",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "gs-nodes-renode.log")
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
        for obj in (getattr(self, "mon", None), getattr(self, "channel", None)):
            if obj is not None:
                obj.close() if hasattr(obj, "close") else obj.stop()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def station(self, name: str, override: bool = False) -> GroundStationNode:
        return GroundStationNode(name, satellite=0, override=override,
                                 link_port=channel_port(0)).connect()

    def rail_settles_to(self, want: bool, seconds: float = RAIL_TIMEOUT_S) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.power.read_rail() is want:
                return True
            time.sleep(0.3)
        return self.power.read_rail() is want


def test_two_stations_are_on_the_air_at_the_same_time():
    with Range() as r:
        primary = r.station("primary")
        backup = r.station("backup")
        try:
            deadline = time.time() + 5
            while r.channel.attached < 2 and time.time() < deadline:
                time.sleep(0.05)
            assert r.channel.attached == 2, (
                f"{r.channel.attached} station(s) on the channel; two nodes were started")
            assert primary.source_id != backup.source_id
        finally:
            primary.close()
            backup.close()


def test_each_station_gets_the_answer_addressed_to_it():
    """Two stations on one broadcast channel, each keeping only its own report.

    The OBC echoes the requester's source id into the report's destination id, so on a channel
    where both stations hear everything, that field is the only thing separating one operator's
    answer from the other's.

    An earlier version of this test asserted that each station had SEEN the other's report -
    counted in GroundStation.not_for_us - and that is timing-dependent: ping() drains the link
    before transmitting, so whether the primary is still polling when the backup's report arrives
    decides it. It passed standalone and failed inside the gate. The broadcast itself is checked
    deterministically in tests/pytest/test_link_channel.py; what belongs here is the property
    that does not depend on who was listening when.
    """
    with Range() as r:
        primary = r.station("primary")
        backup = r.station("backup")
        try:
            for node in (primary, backup):
                report = node.do("ping", timeout=20)
                assert report is not None, f"the {node.station} station got no answer"
                assert report.dest_id == node.source_id, (
                    f"the {node.station} station accepted a report addressed to "
                    f"0x{report.dest_id:04X}, which is not its own 0x{node.source_id:04X} - on a "
                    f"broadcast channel that means one operator is reading another's telemetry")
        finally:
            primary.close()
            backup.close()


def test_the_backup_station_refuses_its_own_forbidden_command():
    """On the ground, before anything is transmitted. This is the part that works."""
    with Range() as r:
        backup = r.station("backup")
        try:
            assert r.power.read_rail() is True
            with pytest.raises(Refused):
                backup.do("rail-off")
            time.sleep(2.0)
            assert r.power.read_rail() is True, "something was transmitted despite the refusal"
            assert "REFUSED" in backup.log_path.read_text()
        finally:
            backup.close()


def test_the_override_lands_because_the_spacecraft_never_checked():
    """And this is the part that does not.

    The station steps past its own matrix; the vulnerable OBC obeys, because the check the backup
    operator just overrode was never on the spacecraft in the first place.
    """
    with Range() as r:
        backup = r.station("backup", override=True)
        try:
            assert r.power.read_rail() is True
            backup.do("rail-off")
            assert r.rail_settles_to(False), (
                "the overridden command did not land; OBC console:\n" + r.console("obc.uart"))
            assert "OVERRIDE" in backup.log_path.read_text(), (
                "the station did not record that it sent something it was not granted")
        finally:
            backup.close()


def test_the_authorised_station_needs_no_override():
    """The control. Without it, the test above could pass against a station that always sends."""
    with Range() as r:
        primary = r.station("primary")
        try:
            assert r.power.read_rail() is True
            assert primary.permitted("rail-off"), "the premise moved: primary may not power"
            primary.do("rail-off")
            assert r.rail_settles_to(False)
            assert "OVERRIDE" not in primary.log_path.read_text(), (
                "the primary station logged an override for something it is granted")
        finally:
            primary.close()
