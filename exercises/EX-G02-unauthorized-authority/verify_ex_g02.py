"""EX-G02: authority the spacecraft does not check.

Three assertions, as every exercise here must have:

  1. against the vulnerable OBC, a ground station the range's own matrix grants only "ping" and
     "observe" switches the COMM rail off - and the frame it sends differs from an authorised
     one by a single octet;
  2. against the mitigated OBC, the identical command is refused and the rail does not move;
  3. against the mitigated OBC, the AUTHORISED station's identical command still works - because
     a fix that stopped the spacecraft obeying anybody would pass the first two and be useless.

A fourth asserts the premise, which is easy to leave implicit and worth stating: the ground
segment's matrix really does forbid this. Without that, assertion 1 measures nothing.

Both builds have CUBERANGE_OBC_PUS8_LENGTH_CHECK=1. EX-F01's flaw is fixed in both halves on
purpose - the point here is that a memory-safe service 8 still executes for anyone, and leaving
the overflow in would let a reader conclude the overflow was the problem.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.authority import may, station_id                # noqa: E402
from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.gs.station import GroundStation                    # noqa: E402
from cuberange.identity import spacecraft                         # noqa: E402
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import link as link_port, monitor as monitor_port  # noqa: E402
from cuberange.renode.monitor import Monitor                      # noqa: E402
from cuberange.renode.powerdomain import PowerDomain              # noqa: E402
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
LINK_PORT, MONITOR_PORT = link_port(0), monitor_port()
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "30"))
RAIL_TIMEOUT_S = float(os.environ.get("CUBERANGE_RAIL_TIMEOUT_S", "20"))

VULN_ELF = OUT / "build-obc-g02-vuln" / "zephyr" / "zephyr.elf"
HARD_ELF = OUT / "build-obc-g02-hard" / "zephyr" / "zephyr.elf"


def _solve():
    """Load this exercise's own solve.py by path.

    Every exercise has one, and `sys.path.insert` makes whichever was imported first win - EX-G01's
    verification once silently received EX-F01's module. CONTRIBUTING.md records the hazard.
    """
    import importlib.util
    path = Path(__file__).resolve().parent / "solve.py"
    spec = importlib.util.spec_from_file_location("ex_g02_solve", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


solve = _solve()


class Range:
    def __init__(self, obc_elf: Path):
        self.obc_elf = obc_elf
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)

    def __enter__(self):
        for name in ("comm.uart", "obc.uart", "eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(MONITOR_PORT),
                "-e", f"$obc=@{self.obc_elf}",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exg02-renode.log")
        self.run = self._ctx.__enter__()

        self.link = SpaceLink(port=LINK_PORT)
        self.link.connect(retries=60)
        self.station = GroundStation(self.link)
        self.mon = Monitor(port=MONITOR_PORT).connect()
        self.power = PowerDomain(self.mon, comm_elf=str(OUT / "build-comm/zephyr/zephyr.elf"))

        for console, needle in (("obc.uart", "OBC listening"), ("eps.uart", "EPS")):
            if not self.wait_console(console, needle, BOOT_TIMEOUT_S):
                text = self.console(console)
                self.__exit__(None, None, None)
                raise AssertionError(
                    f"{console} never reported ready within {BOOT_TIMEOUT_S}s:\n{text}")
        return self

    def __exit__(self, *exc):
        for closer in (getattr(self, "mon", None), getattr(self, "link", None)):
            if closer is not None:
                closer.close()
        self._ctx.__exit__(*exc)

    def wait_console(self, name: str, needle: str, seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if needle in self.console(name):
                return True
            time.sleep(0.2)
        return False

    def console(self, name: str) -> str:
        path = OUT / name
        return path.read_text(errors="replace") if path.exists() else ""

    def send_as(self, station: str, state: int, seq: int = 3) -> None:
        """The command, over the link the Range already holds.

        Not a fresh SpaceLink: Renode's socket terminal serves one client, and a second connection
        is accepted by the kernel and then served by nobody. EX-F01 lost an afternoon to that.
        """
        self.link.send_frame(solve.build_command(station, state, seq=seq))

    def rail_settles_to(self, want: bool, seconds: float = RAIL_TIMEOUT_S) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.power.read_rail() is want:
                return True
            time.sleep(0.3)
        return self.power.read_rail() is want


# --------------------------------------------------------------------------- the premise

def test_the_ground_segments_own_matrix_forbids_this():
    """Assertion 1 measures nothing if the backup station were allowed to do this anyway."""
    assert may("primary", 0, "power"), "the primary station must be allowed, or test 3 is vacuous"
    assert not may("backup", 0, "power"), (
        "the backup station is granted power in src/cuberange/gs/authority.py, so this exercise "
        "is not about an unauthorised command any more")
    assert may("backup", 0, "observe") and may("backup", 0, "ping"), (
        "the backup station must be a real station with real business, not a stand-in attacker")


def test_the_two_commands_differ_by_one_octet_of_identity():
    """What makes this exercise different from every other one here.

    There is no malformed field, no overflow, no replay. Nothing distinguishes the unauthorised
    telecommand from the authorised one except who it says it is from.
    """
    a = solve.build_command("primary", 0, seq=3)
    b = solve.build_command("backup", 0, seq=3)
    differing = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    # The source id octet, and the two FECF octets that necessarily follow any change.
    assert len(differing) == 3, f"expected one identity octet plus the FECF, got {differing}"
    assert a[differing[0]] == station_id("primary") & 0xFF
    assert b[differing[0]] == station_id("backup") & 0xFF


# --------------------------------------------------------------------------- the three

@pytest.mark.skipif(not VULN_ELF.exists(), reason=f"build it: make firmware-g02 ({VULN_ELF})")
def test_an_observe_only_station_switches_the_radio_off():
    with Range(VULN_ELF) as r:
        assert r.power.read_rail() is True, "the COMM rail should start powered"
        r.send_as("backup", state=0)
        assert r.rail_settles_to(False), (
            "the rail stayed up, so the unauthorised command did not land. OBC console:\n"
            + r.console("obc.uart"))
        assert "PUS 8 executed" in r.console("obc.uart"), (
            "the rail moved but the OBC never logged executing the function; something other "
            "than this telecommand switched it")


@pytest.mark.skipif(not HARD_ELF.exists(), reason=f"build it: make firmware-g02 ({HARD_ELF})")
def test_the_mitigated_obc_refuses_the_same_command():
    with Range(HARD_ELF) as r:
        assert r.power.read_rail() is True
        r.send_as("backup", state=0)
        time.sleep(3.0)                     # long enough for the vulnerable build to have acted
        assert r.power.read_rail() is True, (
            "the mitigated OBC still switched the rail for an unauthorised station")
        console = r.console("obc.uart")
        assert "not authorised" in console, (
            f"the rail held, but the OBC never said why - a silent refusal is indistinguishable "
            f"from a dropped frame:\n{console}")


@pytest.mark.skipif(not HARD_ELF.exists(), reason=f"build it: make firmware-g02 ({HARD_ELF})")
def test_the_mitigated_obc_still_obeys_the_authorised_station():
    """Without this, a mitigation that ignored every telecommand would pass."""
    with Range(HARD_ELF) as r:
        assert r.power.read_rail() is True
        r.send_as("primary", state=0)
        assert r.rail_settles_to(False), (
            "the authorised station's command was refused too, so the fix broke the feature. "
            "OBC console:\n" + r.console("obc.uart"))
