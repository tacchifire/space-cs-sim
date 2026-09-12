"""EX-X01: a control keyed on what the sender WRITES does not survive a second way in.

Five, because this exercise has two claims and both need a negative:

  1. the attack lands - a peer on the crosslink switches the victim's COMM rail off by filling
     in the ground station's source id, with EX-G02's authority check ON and working;
  2. the same peer, using its OWN identity, is refused - so the difference between success and
     failure is two octets of a field the attacker chooses;
  3. the mitigation refuses the spoof and says why, distinctly from "not authorised";
  4. the mitigated build still obeys a real ground station over the space link - a mitigation
     that closed the uplink would pass 3 and be useless;
  5. the EPS, whose control asks for a token rather than a name, refuses the same attacker on
     both builds. That is the contrast the exercise is for: possession survived the new path
     and assertion did not.
"""
import os
import socket
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.channel.link_channel import LinkChannel             # noqa: E402
from cuberange.gs.node import GroundStationNode                    # noqa: E402
from cuberange.identity import GROUND_STATIONS, spacecraft         # noqa: E402
from cuberange.paths import out_dir                                # noqa: E402
from cuberange.ports import (channel as channel_port,              # noqa: E402
                             crosslink_injector, link as link_port,
                             monitor as monitor_port)
from cuberange.proto.csp import encode_packet                      # noqa: E402
from cuberange.proto.pus import (PusTc, FAILURE_NOT_AUTHORISED,    # noqa: E402
                                 FAILURE_WRONG_ORIGIN)
from cuberange.proto.spacepacket import PacketType, SpacePacket    # noqa: E402
from cuberange.renode.monitor import Monitor                       # noqa: E402
from cuberange.renode.powerdomain import PowerDomain               # noqa: E402
from cuberange.renode.profile import profile_args                  # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor           # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-obc-x01-vuln" / "zephyr" / "zephyr.elf"
HARD = OUT / "build-obc-x01-hard" / "zephyr" / "zephyr.elf"
XCOMM = OUT / "build-comm-xlink" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

VICTIM, PEER = spacecraft(0), spacecraft(1)
CSP_PORT_PUS, CSP_PORT_POWER, SPORT = 10, 11, 20
SERVICE_FUNCTION, SUBTYPE_PERFORM, FUNC_SET_COMM_RAIL = 8, 1, 1

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not XCOMM.exists(),
    reason=f"build them: make firmware-x01 ({VULN})")


def pus_packet(source_id: int, state: int = 0, seq: int = 0) -> bytes:
    """The telecommand. Not wrapped in a transfer frame: the crosslink has no such layer, which
    is the second half of this exercise."""
    tc = PusTc(service=SERVICE_FUNCTION, subtype=SUBTYPE_PERFORM, source_id=source_id,
               app_data=FUNC_SET_COMM_RAIL.to_bytes(2, "big") + bytes([state]))
    return SpacePacket(apid=VICTIM.apid, ptype=PacketType.TC, sec_hdr=True,
                       seq_count=seq, data=tc.encode()).encode()


class Range:
    """Two spacecraft on a shared crosslink, with an injector on it."""

    def __init__(self, obc_elf: Path):
        self.obc_elf = obc_elf
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)

    def __enter__(self):
        for name in ("x01-sat0-comm.uart", "x01-sat0-obc.uart", "x01-sat0-eps.uart",
                     "x01-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                "-e", f"$injector=@{REPO}/attacker/TcpCanInjector.cs",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$obc=@{self.obc_elf}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exx01-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("x01-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("x01-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.channel = LinkChannel(listen_port=channel_port(0), sat_port=link_port(0)).start()
        self.mon = Monitor(port=monitor_port()).connect()
        self.power = PowerDomain(self.mon, comm_elf=str(XCOMM),
                                 eps_machine="SAT0_EPS", comm_machine="SAT0_COMM")
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

    def inject(self, frames) -> None:
        sock = socket.create_connection(("127.0.0.1", crosslink_injector()), timeout=5)
        try:
            for f in frames:
                sock.sendall(f"{f.can_id:x} {f.data.hex()}\n".encode())
        finally:
            sock.close()

    def from_the_crosslink(self, dst: int, dport: int, payload: bytes) -> None:
        """Transmit as the compromised peer: source address is satellite 1's COMM."""
        self.inject(encode_packet(src=PEER.comm, dst=dst, dport=dport, sport=SPORT,
                                  payload=payload))

    def settle(self, seconds: float = 4.0) -> None:
        time.sleep(seconds)

    def station(self, name: str, override: bool = False) -> GroundStationNode:
        return GroundStationNode(name, satellite=0, override=override,
                                 link_port=channel_port(0)).connect()


def test_the_crosslink_carries_a_packet_between_the_two_spacecraft():
    """Before anything else: the link exists, and it is not an assertion in a console line.

    Each COMM pings every other spacecraft's COMM at startup and logs the round trip. Without
    this, every test below could pass against a crosslink that was never up, because they all
    assert on what does NOT happen on the victim.
    """
    with Range(VULN) as r:
        r.settle(2)
        sat0, sat1 = r.console("x01-sat0-comm.uart"), r.console("x01-sat1-comm.uart")
        assert f"crosslink reached COMM {PEER.comm}" in sat0, sat0
        assert f"crosslink reached COMM {VICTIM.comm}" in sat1, sat1


def test_a_peer_using_the_grounds_source_id_switches_the_rail_off():
    """The attack. EX-G02's authority check is ON in this build and does not stop it."""
    with Range(VULN) as r:
        assert r.power.read_rail() is True
        r.from_the_crosslink(VICTIM.obc, CSP_PORT_PUS,
                             pus_packet(GROUND_STATIONS["primary"]))
        r.settle()
        obc = r.console("x01-sat0-obc.uart")
        assert f"from source {GROUND_STATIONS['primary']}" in obc, obc
        assert "PUS 8 executed" in obc, obc
        assert r.power.read_rail() is False, (
            "the rail is still on: the attack did not land\n" + obc)


def test_the_same_peer_using_its_own_identity_is_refused():
    """Two octets. The authority table fails closed on an identity it does not know, which is
    correct and is exactly why the attacker does not use one."""
    with Range(VULN) as r:
        r.from_the_crosslink(VICTIM.obc, CSP_PORT_PUS, pus_packet(PEER.scid))
        r.settle()
        obc = r.console("x01-sat0-obc.uart")
        assert f"from source {PEER.scid}" in obc, obc
        assert "not authorised" in obc, obc
        assert "PUS 8 executed" not in obc, obc
        assert r.power.read_rail() is True, obc


def test_the_mitigated_build_refuses_the_spoof_and_says_which_refusal_it_is():
    """A report that said only "refused" would send the operator looking for an authority
    problem. This is not one: the authority table would have said yes."""
    with Range(HARD) as r:
        station = r.station("primary")
        r.from_the_crosslink(VICTIM.obc, CSP_PORT_PUS,
                             pus_packet(GROUND_STATIONS["primary"]))
        r.settle()
        obc = r.console("x01-sat0-obc.uart")
        assert "arrived from node" in obc, obc
        assert "PUS 8 executed" not in obc, obc
        assert r.power.read_rail() is True, obc

        #: And the ground hears it. The report is addressed to the claimed source, so it leaves
        #: by this spacecraft's own space link - telling the real station that its name was used.
        refusal = station._station.await_refusal(timeout=10)
        assert refusal is not None, (
            "the ground station heard nothing about a command sent in its name\n" + obc)
        assert refusal.code == FAILURE_WRONG_ORIGIN, (
            f"reported code {refusal.code}, which is not distinguishable from "
            f"{FAILURE_NOT_AUTHORISED} (not authorised)")


def test_the_mitigated_build_still_obeys_a_real_ground_station():
    """The one that keeps the fix honest. Closing the uplink would pass every test above."""
    with Range(HARD) as r:
        station = r.station("primary")
        assert r.power.read_rail() is True
        station.do("rail-off")
        deadline = time.time() + 20
        while r.power.read_rail() and time.time() < deadline:
            time.sleep(0.3)
        obc = r.console("x01-sat0-obc.uart")
        assert "PUS 8 executed" in obc, obc
        assert r.power.read_rail() is False, (
            "the mitigated build stopped obeying its own ground station\n" + obc)

        #: And it did not report a failure for a command it carried out. origin_permits_claim
        #: returning false for the ground's own station would show up here and nowhere else.
        station._station.collect()
        assert not station._station.refusals, (
            f"a command that executed was also reported refused: {station._station.refusals}")


def test_the_eps_refuses_the_crosslink_on_both_builds():
    """The contrast. The EPS asks for a token - something the attacker must HAVE - and arriving
    by a new road did not supply one. Same attacker, same link, same moment."""
    for elf, label in ((VULN, "vulnerable"), (HARD, "mitigated")):
        with Range(elf) as r:
            r.from_the_crosslink(VICTIM.eps, CSP_PORT_POWER,
                                 bytes([1, 0, 0]) + b"\x00\x00\x00\x00")
            r.settle()
            eps = r.console("x01-sat0-eps.uart")
            assert f"REJECTED unauthenticated rail command from node {PEER.comm}" in eps, (
                f"{label} build: {eps}")
            assert r.power.read_rail() is True, f"{label} build: {eps}"
