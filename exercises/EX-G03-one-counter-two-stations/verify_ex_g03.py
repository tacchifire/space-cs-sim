"""EX-G03: a replay defence that assumes one transmitter.

Four assertions. The usual three, plus the one that says what the defect costs in ordinary
operations rather than under attack:

  1. one frame on a virtual channel that is not the operator's silences the operator, because the
     vulnerable build keeps ONE sequence counter for the whole link;
  2. the mitigated build keeps one per virtual channel, as CCSDS does, and the same frame no
     longer touches the operator;
  3. the mitigated build still rejects a genuine replay on the operator's own channel - EX-L01's
     job, which a fix that simply deleted the counter would also have passed 1 and 2 without;
  4. and two legitimate ground stations can both work, which on the vulnerable build they cannot:
     the backup station's own traffic locks the primary out just by existing.

Everything except COMM is the hardened build. EX-B01's bus forgery, EX-F01's overflow and
EX-G02's missing authority check are all fixed here, so the only thing in play is the anti-replay.
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
from cuberange.renode.profile import profile_args                # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor         # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN_ELF = OUT / "build-comm-g03-vuln" / "zephyr" / "zephyr.elf"
HARD_ELF = OUT / "build-comm-g03-hard" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "30"))

pytestmark = pytest.mark.skipif(
    not VULN_ELF.exists() or not HARD_ELF.exists(),
    reason=f"build them: make firmware-g03 ({VULN_ELF})")


def _solve():
    import importlib.util
    path = Path(__file__).resolve().parent / "solve.py"
    spec = importlib.util.spec_from_file_location("ex_g03_solve", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


solve = _solve()


class Range:
    def __init__(self, comm_elf: Path):
        self.comm_elf = comm_elf
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)

    def __enter__(self):
        for name in ("comm.uart", "obc.uart", "eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                "-e", f"$comm=@{self.comm_elf}",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exg03-renode.log")
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
        return self

    def __exit__(self, *exc):
        if getattr(self, "channel", None) is not None:
            self.channel.stop()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def station(self, name: str) -> GroundStationNode:
        return GroundStationNode(name, satellite=0, link_port=channel_port(0)).connect()

    def transmit(self, frame: bytes) -> None:
        """Put a frame on the channel without being a ground station."""
        self.channel.replay(frame)
        time.sleep(0.5)

    def rejections(self) -> int:
        return self.console("comm.uart").count("REJECTED replayed")


def test_one_frame_on_another_channel_silences_the_operator():
    """Sequence 100, not 255, and the difference is the exercise's best detail.

    The comparison is `(int8_t)(seq - last_seq)` so the counter can wrap, which makes it a
    circular window rather than an ordering. Claiming 200 against an operator at 1 computes to
    +57 - ahead - and the operator walks back into the window almost at once. The first version
    of this test used 200 and watched the operator keep working.
    """
    with Range(VULN_ELF) as r:
        primary = r.station("primary")
        try:
            assert primary.do("ping", timeout=20) is not None, "the operator was not working"
            r.transmit(solve.poison(seq=100, vcid=1))
            assert primary.do("ping", timeout=8) is None, (
                "the operator is still being answered, so the poison frame did not land. "
                "COMM console:\n" + r.console("comm.uart"))
            assert r.rejections() >= 1, "COMM did not log a rejection"
            assert "REJECTED replayed" in r.console("comm.uart"), (
                "the operator's own command was discarded, and the word the console uses for it "
                "is 'replayed' - which is the part that sends them hunting the wrong thing")
        finally:
            primary.close()


def test_the_per_channel_counter_leaves_the_operator_alone():
    with Range(HARD_ELF) as r:
        primary = r.station("primary")
        try:
            assert primary.do("ping", timeout=20) is not None
            r.transmit(solve.poison(seq=100, vcid=1))
            assert primary.do("ping", timeout=20) is not None, (
                "a frame on VC 1 still silenced VC 0. COMM console:\n" + r.console("comm.uart"))
        finally:
            primary.close()


def test_the_mitigated_build_still_rejects_a_real_replay():
    """EX-L01's job. Deleting the counter would pass both tests above and lose this."""
    with Range(HARD_ELF) as r:
        primary = r.station("primary")
        try:
            assert primary.do("ping", timeout=20) is not None
            captured = list(r.channel.uplink_frames)
            assert captured, "the channel captured nothing to replay"
            before = r.rejections()
            for frame in captured:
                r.transmit(frame)
            time.sleep(1.0)
            assert r.rejections() > before, (
                "a captured frame was replayed and accepted; the replay defence is gone")
        finally:
            primary.close()


def test_two_legitimate_stations_both_work_only_on_the_mitigated_build():
    """What the defect costs when nobody is attacking at all.

    The backup station is a real site. On the vulnerable build its traffic locks the primary out
    by existing, and the console calls that a replay.
    """
    with Range(VULN_ELF) as r:
        primary, backup = r.station("primary"), r.station("backup")
        try:
            assert primary.do("ping", timeout=20) is not None
            assert backup.do("ping", timeout=8) is None, (
                "the backup station was answered on the single-counter build, so the premise "
                "of this exercise has changed")
        finally:
            primary.close()
            backup.close()

    with Range(HARD_ELF) as r:
        primary, backup = r.station("primary"), r.station("backup")
        try:
            assert primary.do("ping", timeout=20) is not None, "the primary station stopped working"
            assert backup.do("ping", timeout=20) is not None, (
                "the backup station is still locked out with per-channel counters. COMM "
                "console:\n" + r.console("comm.uart"))
        finally:
            primary.close()
            backup.close()
