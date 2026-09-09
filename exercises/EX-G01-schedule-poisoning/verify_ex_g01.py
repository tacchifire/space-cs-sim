"""EX-G01 verification, in both directions, against a real spacecraft.

  1. with the permissive importer, a file written by a plugin ends up as a transmitted telecommand
     and the satellite's radio goes off
  2. with the signed-and-bounded importer, the same file is refused and the radio stays up
  3. with the signed-and-bounded importer, the OPERATOR's own signed plan is still imported and
     still transmitted

The end-to-end half matters. `tests/pytest/test_schedule_policy.py` already proves the policy
behaves, using a fake transmitter - but a policy test cannot show that the resulting bytes are a
telecommand a real OBC executes and a real EPS honours. That is the difference between "the
importer accepted an entry" and "the spacecraft did what the attacker wanted", and only the second
is the exercise.

Everything spacecraft-side runs its HARDENED image here. The point is that all of it works.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                        # noqa: E402
from cuberange.paths import out_dir                             # noqa: E402
from cuberange.gs.import_policy import (AcceptAnything,            # noqa: E402
                                        SignedAndBounded, signed_plan)
from cuberange.gs.link import SpaceLink                            # noqa: E402
from cuberange.gs.schedule import ScheduleStore, Scheduler         # noqa: E402
from cuberange.gs.station import GroundStation                     # noqa: E402
from cuberange.renode.monitor import Monitor                       # noqa: E402
from cuberange.renode.powerdomain import PowerDomain               # noqa: E402
from cuberange.renode.profile import profile_args                  # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor           # noqa: E402


def _sibling_solve():
    """Load THIS exercise's solve.py by path, not by name.

    Every exercise has a module called `solve`, and `sys.path.insert` makes whichever was imported
    first win for all of them: EX-G01's verification silently received EX-F01's module and failed
    at collection with "cannot import name PLUGIN_FILE from solve". CONTRIBUTING.md already warns
    that two `verify_*.py` files cannot share a basename; `solve.py` is the same hazard and every
    exercise has one.
    """
    import importlib.util
    path = Path(__file__).resolve().parent / "solve.py"
    spec = importlib.util.spec_from_file_location(f"cuberange_solve_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_solve = _sibling_solve()
PLUGIN_FILE, poison = _solve.PLUGIN_FILE, _solve.poison

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"

BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "45"))
SERVICE_TEST = 17


class Range:
    """A spacecraft, and a ground segment with a plugin directory somebody else can write to."""

    def __init__(self, policy, plugin_dir: Path):
        self.policy = policy
        self.plugin_dir = plugin_dir
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)

    def __enter__(self):
        for name in ("comm.uart", "obc.uart", "eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(ports.monitor()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exg01-renode.log")
        self.run = self._ctx.__enter__()

        self.link = SpaceLink(port=ports.link(0))
        self.link.connect(retries=60)
        self.station = GroundStation(self.link)
        self.mon = Monitor(port=ports.monitor()).connect()
        self.power = PowerDomain(self.mon, comm_elf=str(OUT / "build-comm/zephyr/zephyr.elf"))

        if not self.wait_console("eps.uart", "EPS listening", seconds=BOOT_TIMEOUT_S):
            console = self.console("eps.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"EPS never reported ready within {BOOT_TIMEOUT_S}s:\n{console}")
        self.power.poll()          # latch the initial rail state

        # The ground segment. Its transmitter is the same GroundStation an operator drives, which
        # is the point: nothing about the outgoing command says it came from a plan rather than a
        # console.
        self.store = ScheduleStore()
        self.scheduler = Scheduler(self.store, self.policy, self.plugin_dir, self._transmit)
        return self

    def _transmit(self, cmd) -> None:
        if (cmd.service, cmd.subtype) == (8, 1):
            self.station.set_comm_rail(on=bool(cmd.args[2]) if len(cmd.args) > 2 else False)
        elif cmd.service == SERVICE_TEST:
            self.station.send_connection_test()
        else:
            raise AssertionError(f"the plan asked for PUS {cmd.service},{cmd.subtype}, which this "
                                 f"ground segment cannot transmit")

    def __exit__(self, *exc):
        for closer in (getattr(self, "store", None), getattr(self, "mon", None),
                       getattr(self, "link", None)):
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

    def alive(self, timeout: float = 8.0) -> bool:
        return self.station.ping(timeout=timeout) is not None

    def settle_power(self, seconds: float = 8.0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            event = self.power.poll()
            if event is not None:
                return event
            time.sleep(0.3)
        return None

    def run_ground_segment(self, seconds: float = 8.0) -> int:
        """Import whatever is in the plugin directory and transmit whatever falls due."""
        imported = self.scheduler.import_pending()
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.scheduler.tick():
                break
            time.sleep(0.2)
        return imported


def _require():
    for d in ("build-comm", "build-obc-hard", "build-eps-hard"):
        elf = OUT / d / "zephyr" / "zephyr.elf"
        if not elf.exists():
            pytest.skip(f"{elf} missing - run 'make firmware-g01' first")


def _plant(plugin_dir: Path, signed: bool = False) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    import json
    doc = poison(delay_s=0.5)
    (plugin_dir / PLUGIN_FILE).write_bytes(
        signed_plan(doc) if signed else json.dumps(doc).encode())


def test_the_attack_works_against_the_permissive_importer(tmp_path):
    _require()
    _plant(tmp_path)
    with Range(AcceptAnything(), tmp_path) as r:
        assert r.alive(), "the satellite was not answering before the attack"
        assert r.power.state is True, "the COMM rail should start powered"

        imported = r.run_ground_segment()
        assert imported == 1, "the ground segment did not ingest the plugin's file"

        event = r.settle_power()
        assert event is not None and event.powered is False, (
            f"the rail never dropped; EPS console said:\n{r.console('eps.uart')}")
        assert "COMM rail OFF" in r.console("eps.uart")

        # And the spacecraft-side controls all worked while it happened. If the EPS had rejected
        # the command, this attack would have failed for a reason that has nothing to do with the
        # ground segment - and the exercise would be teaching the wrong lesson.
        assert "REJECTED" not in r.console("eps.uart"), (
            "the EPS refused the command, so what this test observed was not a poisoned plan "
            "being honoured:\n" + r.console("eps.uart"))
        assert not r.alive(timeout=8), "the satellite still answered after its radio lost power"


def test_the_mitigation_refuses_the_plugin_s_file(tmp_path):
    _require()
    _plant(tmp_path)
    with Range(SignedAndBounded(), tmp_path) as r:
        assert r.alive(), "the satellite was not answering before the attack"

        imported = r.run_ground_segment(seconds=4)
        assert imported == 0
        assert r.scheduler.rejected and "unsigned" in r.scheduler.rejected[0][1], (
            f"the importer did not record a rejection, so the file may never have been read - "
            f"which would make this test pass for the wrong reason: {r.scheduler.rejected}")

        assert r.settle_power(seconds=5) is None, "the rail dropped despite the import policy"
        assert r.alive(), "the satellite went quiet even though nothing was transmitted"


def test_the_mitigation_still_transmits_the_operator_s_own_plan(tmp_path):
    """A control that refuses every import is not a mitigation, it is a removed feature."""
    _require()
    tmp_path.mkdir(parents=True, exist_ok=True)
    import json
    # A connection test, signed, and inside the set an import may schedule.
    doc = {"version": 1, "entries": [{"due": time.time() + 0.5, "service": SERVICE_TEST,
                                      "subtype": 1, "args": "", "note": "pass 1 health check"}]}
    (tmp_path / PLUGIN_FILE).write_bytes(signed_plan(doc))

    with Range(SignedAndBounded(), tmp_path) as r:
        assert r.alive(), "the satellite was not answering before the command"

        imported = r.run_ground_segment()
        assert imported == 1, (
            f"the operator's own signed plan was refused: {r.scheduler.rejected}")
        assert r.scheduler.sent and r.scheduler.sent[0].service == SERVICE_TEST
        assert r.alive(), "the satellite stopped answering after a health check"
        assert r.settle_power(seconds=4) is None, "a health check moved a power rail"
