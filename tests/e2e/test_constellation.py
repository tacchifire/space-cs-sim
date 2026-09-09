"""Two spacecraft in one emulation, and the ways that can be wrong.

Adding a second satellite is mostly bookkeeping, and every piece of that bookkeeping fails quietly
when it is wrong. Hub names are emulation-scope, so two scenarios that both say "canHub" join both
spacecraft to one bus and an attack on either lands on both. CSP addresses are five bits, so a
second satellite that reuses the first's numbers is answered by the wrong node. Frame identities
were module constants until recently, so both spacecraft emitted 0x0A9 and a ground station
accepted whichever answered first.

None of those produce an error. All of them produce a range that appears to work and teaches
something false, so each gets a test here.

The isolation test is the load-bearing one, and it carries a positive control rather than only an
absence: it sends satellite 1's EPS a command on satellite 0's bus and requires nothing to happen,
then sends the IDENTICAL command on satellite 1's own bus and requires it to work. Asserting only
that nothing happened would pass just as well against a typo, or against a satellite 1 that ignores
the command for some reason of its own - and the hubs could have been joined the whole time.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                        # noqa: E402
from cuberange.paths import out_dir                             # noqa: E402
from cuberange.gs.link import SpaceLink                            # noqa: E402
from cuberange.gs.station import GroundStation                     # noqa: E402
from cuberange.proto.csp import encode_packet                      # noqa: E402
from cuberange.proto.frame import SCID                             # noqa: E402
from cuberange.renode.monitor import Monitor                       # noqa: E402
from cuberange.renode.supervisor import Outcome, RenodeSupervisor  # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = REPO / "scripts" / "multi-node" / "constellation.resc"
INJECTOR = REPO / "attacker" / "TcpCanInjector.cs"

BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "90"))

# Identities, from firmware/common/identity.cmake. Written out rather than imported so that a
# change to the derivation has to be made here too, deliberately.
SAT0 = dict(scid=SCID, obc=1, eps=2, adcs=4, comm=5, link=ports.link(0))
SAT1 = dict(scid=SCID + 1, obc=9, eps=10, adcs=12, comm=13, link=ports.link(1))

CSP_PORT_POWER = 11
INJ0_PORT = ports.injector(0)
INJ1_PORT = ports.injector(1)

CONSOLES = [f"sat{n}-{role}.uart" for n in (0, 1)
            for role in ("comm", "obc", "eps", "adcs")]


def _images_present() -> bool:
    needed = ["build-comm", "build-obc", "build-eps-vuln", "build-adcs-vuln",
              "build-comm-sat1", "build-obc-sat1", "build-eps-sat1", "build-adcs-sat1"]
    return all((OUT / d / "zephyr" / "zephyr.elf").is_file() for d in needed)


class Constellation:
    def __init__(self):
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=300, rss_ceiling_mb=4096)

    def __enter__(self):
        for name in CONSOLES:
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(ports.monitor()),
                "-e", f"$profile=@{REPO}/scripts/profiles/interactive.resc",
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                # The injector is added here rather than in the scenario: the constellation exists
                # to show two spacecraft coexisting, and putting a raw CAN entry point on both
                # hubs by default would expose a satellite no exercise had asked to expose.
                "-e", 'mach create "attacker0"',
                "-e", f"include @{INJECTOR}",
                "-e", f'machine CreateTcpCanInjector "inj0" {INJ0_PORT}',
                "-e", "connector Connect inj0 canHub0",
                # A second injector, on the other hub. The isolation test needs it: without a way
                # to reach satellite 1's bus, "the command did nothing on hub 0" cannot be told
                # apart from "that command does nothing anywhere".
                "-e", 'mach create "attacker1"',
                "-e", f'machine CreateTcpCanInjector "inj1" {INJ1_PORT}',
                "-e", "connector Connect inj1 canHub1",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "constellation-renode.log")
        self.run = self._ctx.__enter__()
        self.mon = Monitor(port=ports.monitor()).connect()
        return self

    def __exit__(self, *exc):
        mon = getattr(self, "mon", None)
        if mon is not None:
            mon.close()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        path = OUT / name
        return path.read_text(errors="replace") if path.exists() else ""

    def wait_all_ready(self, seconds: float = BOOT_TIMEOUT_S) -> list[str]:
        """Wait for every node's own readiness line. Returns the ones that never appeared."""
        wanted = {name: needle for name, needle in (
            ("sat0-comm.uart", "COMM"), ("sat0-obc.uart", "OBC listening"),
            ("sat0-eps.uart", "EPS listening"), ("sat0-adcs.uart", "ADCS listening"),
            ("sat1-comm.uart", "COMM"), ("sat1-obc.uart", "OBC listening"),
            ("sat1-eps.uart", "EPS listening"), ("sat1-adcs.uart", "ADCS listening"))}
        deadline = time.time() + seconds
        while time.time() < deadline:
            missing = [n for n, needle in wanted.items() if needle not in self.console(n)]
            if not missing:
                return []
            time.sleep(0.5)
        return [n for n, needle in wanted.items() if needle not in self.console(n)]


def _forge_rail_off(port: int, dst_addr: int, src_addr: int) -> None:
    """A power-off command for whichever EPS you name, on whichever hub the injector is on."""
    payload = bytes([1, 0, 0]) + b"\x00\x00\x00\x00"   # opcode=set rail, rail=COMM, off, no token
    frames = encode_packet(src=src_addr, dst=dst_addr, dport=CSP_PORT_POWER, sport=20,
                           payload=payload, transfer_id=0x2C)
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        for frame in frames:
            sock.sendall(f"{frame.can_id:x} {frame.data.hex()}\n".encode())
    finally:
        sock.close()


@pytest.fixture(scope="module")
def constellation():
    if not _images_present():
        pytest.skip("satellite images missing - run `make firmware-all firmware-sat1` first")
    with Constellation() as c:
        missing = c.wait_all_ready()
        if missing:
            consoles = "\n".join(f"--- {n} ---\n{c.console(n)}" for n in missing)
            pytest.fail(f"these nodes never reported ready: {missing}\n{consoles}")
        yield c


def test_eight_nodes_boot(constellation):
    """Two spacecraft of four nodes each, in one emulation. Six was the previous measured ceiling."""
    for name in CONSOLES:
        text = constellation.console(name)
        assert "Booting Zephyr OS" in text, f"{name} never booted:\n{text}"


def test_each_spacecraft_uses_its_own_csp_addresses(constellation):
    """Satellite 1's nodes must announce the addresses identity.cmake derived, not satellite 0's."""
    assert f"OBC (addr {SAT0['obc']})" in constellation.console("sat0-obc.uart")
    assert f"OBC (addr {SAT1['obc']})" in constellation.console("sat1-obc.uart")
    assert f"EPS (addr {SAT0['eps']})" in constellation.console("sat0-eps.uart")
    assert f"EPS (addr {SAT1['eps']})" in constellation.console("sat1-eps.uart")
    assert f"ADCS (addr {SAT0['adcs']})" in constellation.console("sat0-adcs.uart")
    assert f"ADCS (addr {SAT1['adcs']})" in constellation.console("sat1-adcs.uart")


def test_both_spacecraft_answer_on_their_own_link(constellation):
    """Each ground station talks to its own satellite, and the identity check does not refuse it."""
    for sat in (SAT0, SAT1):
        link = SpaceLink(port=sat["link"])
        link.connect(retries=60)
        try:
            station = GroundStation(link, station_id=0x0042 + sat["obc"],
                                    target_apid=sat["scid"], target_scid=sat["scid"])
            assert station.ping(timeout=15) is not None, (
                f"satellite with SCID 0x{sat['scid']:03X} did not answer on port {sat['link']}")
        finally:
            link.close()


def test_the_buses_are_isolated(constellation):
    """The load-bearing one: an injector on hub 0 must not reach satellite 1.

    The control has to be about SATELLITE 1, not satellite 0. An earlier version proved only that
    the injector and satellite 0's command path worked, which would have passed even if satellite
    1's EPS were hardened, stale, or ignoring the command for some unrelated reason - and the hubs
    could have been joined the whole time.

    So: the same command, to the same address, from the same kind of injector, delivered on the
    other hub. If it works there and not here, the difference is the hub.
    """
    def sat1_eps_new_output(mark: int) -> str:
        return constellation.console("sat1-eps.uart")[mark:]

    mark = len(constellation.console("sat1-eps.uart"))

    # 1. Satellite 1's EPS, addressed correctly, injected on satellite 0's bus. Must do nothing.
    _forge_rail_off(INJ0_PORT, dst_addr=SAT1["eps"], src_addr=SAT1["obc"])
    time.sleep(6)
    leaked = sat1_eps_new_output(mark)
    assert "COMM rail OFF" not in leaked, (
        "satellite 1's EPS acted on a frame injected into satellite 0's CAN hub - the two hubs are "
        "joined, and every bus exercise on one spacecraft is silently affecting the other:\n"
        + leaked)

    # 2. The control: the identical command on satellite 1's own bus. If this does not work, step 1
    #    proved nothing about hubs and this test says so rather than passing.
    _forge_rail_off(INJ1_PORT, dst_addr=SAT1["eps"], src_addr=SAT1["obc"])
    deadline = time.time() + 12
    while time.time() < deadline:
        if "COMM rail OFF" in sat1_eps_new_output(mark):
            break
        time.sleep(0.3)
    else:
        pytest.fail(
            "the control failed: satellite 1's EPS did not act on this command even when it was "
            "delivered on satellite 1's own hub. The isolation result above therefore says nothing "
            "about the hubs - it may just be a command this EPS never accepts.\n"
            + sat1_eps_new_output(mark))

    # 3. And the traffic on hub 1 must not have reached satellite 0 either. Symmetry matters: a
    #    scenario that leaked in one direction only would still be a joined bus.
    assert "COMM rail OFF" not in constellation.console("sat0-eps.uart"), (
        "satellite 0's rail dropped when satellite 1's bus was attacked")
