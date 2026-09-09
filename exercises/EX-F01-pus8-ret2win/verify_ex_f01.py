"""EX-F01 verification, in both directions.

  1. against the vulnerable OBC, an over-long PUS 8 argument block redirects the handler's return
     into a maintenance function the ground cannot legally invoke
  2. against the mitigated OBC, the same telecommand is refused and control flow stays put
  3. against the mitigated OBC, a legitimate PUS 8,1 still switches the rail

Without (3) a mitigation that refused every PUS 8 would pass, and the satellite would be safe in
the way a brick is safe.

Two things are asserted that a simpler test would leave out, and both come from this project's own
history of gates that pass without proving anything:

  - the vulnerable run must show BOTH the legitimate effect and the hijack. If only the hijack
    line appeared, the packet could have been mishandled rather than executed, and "the exploit
    worked" would be an interpretation rather than an observation.
  - the mitigated run must show the rejection line. A quiet OBC that never received the packet
    would otherwise look exactly like a working mitigation.
"""
import os
import re
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports  # noqa: E402
from cuberange.paths import out_dir                             # noqa: E402
from cuberange.gs.link import SpaceLink                       # noqa: E402
from cuberange.gs.station import GroundStation                # noqa: E402
from cuberange.proto.frame import encode_tc_frame             # noqa: E402
from cuberange.proto.pus import PusTc                         # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket  # noqa: E402
from cuberange.renode.monitor import Monitor                  # noqa: E402
from cuberange.renode.profile import profile_args             # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor      # noqa: E402


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
ARG_BUF_LEN, RET_OFFSET, WIN_SYMBOL = _solve.ARG_BUF_LEN, _solve.RET_OFFSET, _solve.WIN_SYMBOL
build_payload, read_symbol = _solve.build_payload, _solve.read_symbol

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"

LINK_PORT, MONITOR_PORT = ports.link(0), ports.monitor()
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "45"))

OBC_APID = 0x0A9
GROUND_SOURCE_ID = 0x0042

# gpioPortD base 0x58020C00 + ODR offset 0x14, pin 7. Read from the pin the firmware drove rather
# than from telemetry: the whole exercise is about an attacker who reached privileged code, and
# privileged code can write telemetry.
GPIOD_ODR = 0x58020C14
FDIR_PIN = 7


def _send_pus8(link: SpaceLink, app_data: bytes, seq: int = 7) -> None:
    """Send over the link the Range already holds.

    Not a fresh connection. Renode's socket terminal serves one client, so a second one is accepted
    by the kernel and then served by nobody: the frames vanish and the OBC console simply never
    mentions them. The first version of this file opened its own link and every PUS 8 test failed
    with "the packet never arrived", which is true and says nothing about the firmware.
    """
    tc = PusTc(service=8, subtype=1, source_id=GROUND_SOURCE_ID, app_data=app_data)
    packet = SpacePacket(apid=OBC_APID, ptype=PacketType.TC, sec_hdr=True,
                         seq_count=seq, data=tc.encode())
    link.send_frame(encode_tc_frame(packet.encode(), seq & 0xFF))


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
        self._ctx = self.sup.launch(argv, OUT / "exf01-renode.log")
        self.run = self._ctx.__enter__()

        self.link = SpaceLink(port=LINK_PORT)
        self.link.connect(retries=60)
        self.station = GroundStation(self.link)
        self.mon = Monitor(port=MONITOR_PORT).connect()

        # Wait for the OBC's own readiness line. A telecommand sent before its CSP interface is up
        # is dropped once, with no retry, and the exercise would then be measuring nothing.
        if not self.wait_console("obc.uart", "OBC listening", seconds=BOOT_TIMEOUT_S):
            console = self.console("obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready within {BOOT_TIMEOUT_S}s:\n{console}")
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

    def alive(self, timeout: float = 8.0) -> bool:
        return self.station.ping(timeout=timeout) is not None

    def read_fdir_inhibit(self) -> bool:
        self.mon.command('mach set "OBC"')
        return bool((self.mon.read_u32(GPIOD_ODR) >> FDIR_PIN) & 1)


def _require(elf: Path):
    if not elf.exists():
        pytest.skip(f"{elf} missing - run 'make firmware-f01' first")


def _payload_for(elf: Path) -> bytes:
    return build_payload(read_symbol(elf, WIN_SYMBOL))


def test_the_target_is_in_the_image_and_unreachable_by_command():
    """The premise, checked before anything is run.

    If the linker removed the maintenance handler there is nothing to return to, and every other
    test in this file would fail for a reason that has nothing to do with the exercise. An earlier
    version of the firmware lost it to --gc-sections while `__attribute__((used))` was present.
    """
    elf = OUT / "build-obc" / "zephyr" / "zephyr.elf"
    _require(elf)
    addr = read_symbol(elf, WIN_SYMBOL)
    assert addr & 0xFF000000 == 0x08000000, (
        f"{WIN_SYMBOL} is at 0x{addr:08x}, which is not in flash; the ret2win target must be in "
        f"executable memory, and SRAM here is execute-never")
    # Matched with a regex rather than an exact string. The first version of this assertion
    # depended on the number of spaces in the table, so reformatting the source would have failed
    # the test for a reason that has nothing to do with the exercise.
    source = (REPO / "firmware" / "apps" / "obc" / "src" / "main.c").read_text()
    entry = re.search(r"\{\s*FUNC_MAINTENANCE\s*,\s*(\w+)\s*,", source)
    assert entry is not None, (
        "the maintenance entry is no longer in the OBC's function table; there is nothing for the "
        "exercise to return to")
    assert entry.group(1) == "false", (
        f"the maintenance entry is enabled ({entry.group(1)}), so the ground can call it "
        f"legitimately and reaching it proves nothing about control flow")


def test_attack_succeeds_against_the_vulnerable_obc():
    elf = OUT / "build-obc" / "zephyr" / "zephyr.elf"
    _require(elf)
    with Range(elf) as r:
        assert r.alive(), "the satellite was not answering before the attack"
        assert r.read_fdir_inhibit() is False, "FDIR should not start inhibited"

        _send_pus8(r.link, _payload_for(elf))

        assert r.wait_console("obc.uart", "FDIR INHIBITED", seconds=15), (
            f"control flow was not redirected. OBC console:\n{r.console('obc.uart')}")
        assert r.read_fdir_inhibit() is True, (
            "the console announced the hijack but the pin the handler drives is still low, so "
            "something other than the handler printed it")

        # The legitimate half must ALSO have run. Without this, a packet that was mishandled into
        # the win function would be indistinguishable from one that was executed and then returned
        # into it, and only the second is a control-flow hijack.
        assert "PUS 8 executed" in r.console("obc.uart"), (
            f"the function itself never ran, so the return was never reached:\n"
            f"{r.console('obc.uart')}")


def test_mitigation_rejects_the_over_long_argument_block():
    elf = OUT / "build-obc-hard" / "zephyr" / "zephyr.elf"
    _require(elf)
    with Range(elf) as r:
        assert r.alive(), "the satellite was not answering before the attack"

        _send_pus8(r.link, _payload_for(elf))

        assert r.wait_console("obc.uart", "REJECTED PUS 8 argument block", seconds=15), (
            f"the OBC did not log a rejection, so the packet may never have arrived - which would "
            f"make this test pass for the wrong reason:\n{r.console('obc.uart')}")
        assert "FDIR INHIBITED" not in r.console("obc.uart")
        assert r.read_fdir_inhibit() is False, "the mitigated build was hijacked anyway"
        assert r.alive(), "the satellite went quiet even though the command was rejected"


def test_the_mitigation_does_not_break_legitimate_pus8():
    """A control that refuses every PUS 8 is not a mitigation, it is a dead service."""
    elf = OUT / "build-obc-hard" / "zephyr" / "zephyr.elf"
    _require(elf)
    with Range(elf) as r:
        assert r.alive(), "the satellite was not answering before the command"

        # Function 1, rail ON, with an argument block that fits.
        legitimate = (1).to_bytes(2, "big") + bytes([1]) + bytes(ARG_BUF_LEN - 1)
        assert len(legitimate) - 2 <= ARG_BUF_LEN
        _send_pus8(r.link, legitimate, seq=8)

        assert r.wait_console("obc.uart", "PUS 8 executed", seconds=15), (
            f"a well-sized PUS 8 was not executed:\n{r.console('obc.uart')}")
        assert r.read_fdir_inhibit() is False
        assert r.alive()


def test_the_measured_offset_still_describes_the_build():
    """The return-address offset is a property of one compilation, not a constant of nature.

    It was read out of the prologue of one image. If the firmware or the toolchain moves it, the
    attack test above fails with "control flow was not redirected", which is a confusing way to
    learn that a number went stale. This says it directly.
    """
    elf = OUT / "build-obc" / "zephyr" / "zephyr.elf"
    _require(elf)
    payload = _payload_for(elf)
    assert len(payload) - 2 == RET_OFFSET + 4
    assert len(payload) - 2 > ARG_BUF_LEN, (
        "the payload no longer overflows the buffer at all")
