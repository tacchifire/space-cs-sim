"""EX-U03: every control works, every frame is refused, and nothing leaves the spacecraft.

This attacker has no key, which is the ordinary case - EX-U02's is the rare one. Ten replays and
ten forgeries go up. The SDLS MAC refuses the forgeries, the anti-replay counter refuses the
replays, and not one payload reaches the on-board computer. The defence is complete.

Four runs:

  1. the radio reports, an attacker transmitting -> 20 refused of 23 received. Everything else in
     the range says ordinary pass: four commands acknowledged, no counter gaps, and
     `unexplained_commands` - EX-U02's detector, built specifically to notice attackers - reads
     ZERO, because it counts what the COMPUTER heard and the radio is in front of it;
  2. the radio reports, nobody transmitting -> 0 refused of 3. Which is what makes row 1's twenty
     a measurement and not a constant;
  3. the radio does not report, an attacker transmitting -> cannot tell;
  4. the radio does not report, nobody transmitting -> cannot tell. Rows 3 and 4 differ in nothing
     an operator can see, which is what "a successful defence is indistinguishable from no attack"
     means when it is measured rather than asserted.

A forgery needs no key: the FECF is a CRC and anybody can recompute it over changed octets. That
is what a checksum is for and what it is not for, and it is why there is a MAC underneath.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.channel.link_channel import LinkChannel            # noqa: E402
from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.gs.station import GroundStation                    # noqa: E402
from cuberange.identity import GROUND_STATIONS, GROUND_VCIDS      # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import (channel as channel_port, link as link_port,   # noqa: E402
                             monitor as monitor_port)
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

def _load_sibling(module_name: str, filename: str):
    """Import a file from this exercise's directory under a name nothing else will claim."""
    import importlib.util
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

#: By path, under a name of its own - see EX-U02's verifier for what sharing `solve` costs.
forge = _load_sibling("solve_u03", "solve.py").forge

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-comm-u03-vuln" / "zephyr" / "zephyr.elf"      # the radio says nothing
HARD = OUT / "build-comm-u03-hard" / "zephyr" / "zephyr.elf"      # the radio reports what it threw
OBC = OUT / "build-obc-u02-hard" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))
COMMANDS, REPLAYS, FORGERIES = 4, 10, 10
THROWN = REPLAYS + FORGERIES

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not OBC.exists(),
    reason=f"build them: make firmware-u03 firmware-u02 ({VULN})")


class Barrage:
    def __init__(self, comm: Path, *, attacker: bool):
        self.comm = comm
        self.attacker = attacker

    def __enter__(self):
        for name in ("u03-sat0-comm.uart", "u03-sat0-obc.uart", "u03-sat0-eps.uart",
                     "u03-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$comm=@{self.comm}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exu03-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("u03-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("u03-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.channel = LinkChannel(listen_port=channel_port(0), sat_port=link_port(0)).start()
        self.link = SpaceLink(port=channel_port(0))
        self.link.connect(retries=60)
        self.station = GroundStation(self.link, station_id=GROUND_STATIONS["primary"],
                                     vcid=GROUND_VCIDS["primary"],
                                     require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY,
                                     sdls_spi=SDLS_SPI)
        return self

    def __exit__(self, *exc):
        for obj, closer in ((getattr(self, "link", None), "close"),
                            (getattr(self, "channel", None), "stop")):
            if obj is not None:
                getattr(obj, closer)()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def _listen(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.station.collect()
            time.sleep(0.25)

    def fly(self) -> int:
        """Acquire, command, be attacked, listen. Returns how many frames were thrown."""
        deadline = time.time() + 8.0
        while time.time() < deadline and self.station._link_baseline is None \
                and self.station._tc_baseline is None:
            self.station.collect()
            time.sleep(0.2)

        for _ in range(COMMANDS):
            self.station.send_connection_test()
            self._listen(1.2)
        assert self.station.commands_sent == COMMANDS

        thrown = 0
        if self.attacker:
            captured = self.channel.uplink_frames[-1]
            for _ in range(REPLAYS):
                self.channel.replay(captured)
                thrown += 1
                time.sleep(0.12)
            for _ in range(FORGERIES):
                self.channel.replay(forge(captured))
                thrown += 1
                time.sleep(0.12)
        self._listen(6.0)
        return thrown


def test_the_radio_reports_what_it_threw_away():
    """The exercise. Twenty refused, and every other indicator says ordinary pass."""
    with Barrage(HARD, attacker=True) as p:
        assert p.fly() == THROWN
        assert p.station.link_frames_refused == THROWN, (
            f"the radio reports {p.station.link_frames_refused} refusals of {THROWN} frames "
            f"thrown at it\n" + p.console("u03-sat0-comm.uart")[-900:])
        #: A ratio, not a count. Twenty refused is not a sentence; twenty of twenty-three is.
        assert p.station.link_frames_received >= THROWN, (
            f"{p.station.link_frames_received} received but {THROWN} refused, which cannot be")
        #: THE PART WORTH THE EXERCISE. EX-U02's detector was built to notice an attacker and it
        #: reads zero all the way through an attack, because it counts what the COMPUTER heard and
        #: the control that stopped this one is in front of it.
        assert p.station.unexplained_commands == 0, (
            f"{p.station.unexplained_commands} unexplained telecommands - something got past COMM "
            f"and this exercise is about an attack that does not")
        assert len(p.station.acknowledged) == COMMANDS
        assert p.station.counter_gaps == []
        #: The refusals happened on the spacecraft either way. What the flag changes is whether
        #: anybody off it can know - so the console must show them in BOTH halves, or the pair is
        #: measuring a difference in the attack rather than in the reporting.
        console = p.console("u03-sat0-comm.uart")
        #: "the MAC does not verify" and not merely "refused": a forged frame whose FECF
        #: was recomputed passes every layout and integrity check this radio has, and the
        #: MAC is the thing that stops it. An assertion on the generic word would pass on
        #: a frame refused for its shape, which is not what this exercise sends.
        assert console.count("the MAC does not verify") == FORGERIES
        assert console.count("REPLAY") == REPLAYS


def test_the_same_radio_reports_zero_when_nobody_transmits():
    with Barrage(HARD, attacker=False) as p:
        assert p.fly() == 0
        assert p.station.link_frames_refused == 0, (
            f"{p.station.link_frames_refused} refusals on a run with no attacker")
        assert p.station.link_frames_received > 0, (
            "the radio reports receiving nothing, so this run proves nothing about a radio that "
            "reports")
        assert len(p.station.acknowledged) == COMMANDS


def test_the_vulnerable_radio_refuses_everything_and_says_nothing():
    with Barrage(VULN, attacker=True) as p:
        assert p.fly() == THROWN
        #: It DID refuse them. That is the exercise: the control worked perfectly.
        console = p.console("u03-sat0-comm.uart")
        assert console.count("the MAC does not verify") == FORGERIES, (
            "the vulnerable half did not refuse the forgeries, so the pair's flag changes the "
            "defence rather than the reporting")
        assert console.count("REPLAY") == REPLAYS
        assert p.station.link_frames_refused is None
        assert p.station.link_frames_received is None
        #: And every number an operator can actually see is the number they see on a quiet day.
        assert p.station.unexplained_commands == 0
        assert len(p.station.acknowledged) == COMMANDS
        assert p.station.counter_gaps == []


def test_the_vulnerable_radio_looks_exactly_the_same_with_nobody_there():
    """Which is what "indistinguishable" means once it is measured instead of asserted."""
    with Barrage(VULN, attacker=False) as p:
        assert p.fly() == 0
        assert p.station.link_frames_refused is None
        assert p.station.unexplained_commands == 0
        assert len(p.station.acknowledged) == COMMANDS
        assert p.station.counter_gaps == []
