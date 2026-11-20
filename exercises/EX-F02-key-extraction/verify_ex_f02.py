"""EX-F02 verification, in both directions.

Five tests. The usual three, and two that keep the exercise from proving itself:

  1. the premise, from the two ELFs and nothing else: the key is in BOTH halves, in flash, and the
     octets the image stores there are the range's key. Without it, a half built without the key
     would make test 3 pass for a reason that has nothing to do with the allowlist;
  2. against the vulnerable OBC, a dump of the key's address returns the key - AND those octets
     verify signed reports from the same run, while a key one bit away does not. Pattern-matching
     the range's deliberately recognisable key would prove nothing; verifying the spacecraft's own
     MAC with it is the key working as a key;
  3. against the mitigated OBC, the same request is refused: the rejection line is on the console,
     no dump report arrives, and the spacecraft still answers;
  4. against the mitigated OBC, a dump of the public block is served, starts with its magic and is
     signed. A build that refused every dump would pass 3 and be a dead service;
  5. against the mitigated OBC, a dump that STARTS inside the public block and runs past its end is
     refused. That is the request a start-address check alone serves, and test 4 cannot tell such a
     check from a correct one.

None of this has been run yet: the file was written on a machine with no Zephyr toolchain and no
Renode. Until it passes under `make verify EX=EX-F02-key-extraction` it is a description of what
these tests will check, not evidence that it holds.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                   # noqa: E402
from cuberange.gs.link import SpaceLink                       # noqa: E402
from cuberange.gs.station import GroundStation                # noqa: E402
from cuberange.identity import spacecraft                     # noqa: E402
from cuberange.keys import SDLS_KEY                           # noqa: E402
from cuberange.paths import out_dir                           # noqa: E402
from cuberange.renode.profile import profile_args             # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor      # noqa: E402


def _sibling_solve():
    """Load THIS exercise's solve.py by path, under a name nothing else will claim.

    Every exercise has a module called `solve`, and `sys.path.insert` makes whichever was imported
    first win for all of them - see EX-F01's verifier. Registered in sys.modules as well, as
    EX-U02's loader does: solve.py defines a @dataclass, and dataclasses looks a class's module up
    there whenever an annotation is a string.
    """
    import importlib.util
    path = Path(__file__).resolve().parent / "solve.py"
    name = f"cuberange_solve_{path.parent.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_solve = _sibling_solve()

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-obc-f02-vuln" / "zephyr" / "zephyr.elf"
HARD = OUT / "build-obc-f02-hard" / "zephyr" / "zephyr.elf"
OBC_CONSOLE = "f02-obc.uart"

LINK_PORT, MONITOR_PORT = ports.link(0), ports.monitor()
SCID = spacecraft(0).scid
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "45"))
#: A deadline, not a sleep: every wait below returns the moment its event arrives.
SETTLE_TIMEOUT_S = float(os.environ.get("CUBERANGE_SETTLE_TIMEOUT_S", "25"))

#: Flash on this part. The key being here is the premise: .rodata under XIP, readable by any code
#: and by any service that reads memory on request.
FLASH_BASE, FLASH_MASK = 0x08000000, 0xFF000000


class Range:
    def __init__(self, obc_elf: Path):
        self.obc_elf = obc_elf
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)

    def __enter__(self):
        for name in ("f02-comm.uart", OBC_CONSOLE, "f02-eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(MONITOR_PORT),
                "-e", f"$obc=@{self.obc_elf}",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exf02-renode.log")
        self.run = self._ctx.__enter__()

        #: The Range's ONE link. Renode's socket terminal serves one client; a second connection
        #: is accepted by the kernel and served by nobody (EX-F01's verifier learned that).
        self.link = SpaceLink(port=LINK_PORT)
        self.link.connect(retries=60)
        self.station = GroundStation(self.link)

        #: The OBC's own readiness line. A telecommand sent before its CSP interface is up is
        #: dropped once, with no retry, and the test would then be measuring nothing.
        if not self.wait_console(OBC_CONSOLE, "OBC listening", seconds=BOOT_TIMEOUT_S):
            console = self.console(OBC_CONSOLE)
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready within {BOOT_TIMEOUT_S}s:\n{console}")
        return self

    def __exit__(self, *exc):
        link = getattr(self, "link", None)
        if link is not None:
            link.close()
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

    def alive(self, timeout: float = 20.0) -> bool:
        """Retries to a deadline - see GroundStation.alive for why one ping is not liveness."""
        return self.station.alive(timeout=timeout)

    def dump(self, address: int, length: int) -> None:
        """TC 6,5, plain, from the operator's station id - this OBC verifies no telecommand."""
        self.station.send_pus(_solve.SERVICE_MEMORY, _solve.SUBTYPE_DUMP,
                              _solve.dump_request(address, length))

    def await_dump(self, timeout: float = SETTLE_TIMEOUT_S):
        packet, _ = _solve.await_report(self.link, _solve.is_dump_report, timeout, scid=SCID)
        return None if packet is None else _solve.parse_dump_report(packet)

    def signed_test_report(self, timeout: float = SETTLE_TIMEOUT_S):
        """PUS 17,1, and the 17,2 that answers it AS SENT - trailer included - plus everything
        that arrived before it.

        The answer doubles as a fence for the negative tests. The OBC handles telecommands one at a
        time on one thread, and every report it sends goes down through COMM in the order sent, so
        a dump report for a request made BEFORE this ping would arrive before its answer. "The
        17,2 came and no 6,6 preceded it" is an observation; "nothing arrived for a while" would
        only be a wait.
        """
        self.station.send_connection_test()
        return _solve.await_report(self.link, lambda tm: (tm.service, tm.subtype) == (17, 2),
                                   timeout, scid=SCID)


def _require(*elves: Path):
    for elf in elves:
        if not elf.exists():
            pytest.skip(f"{elf} missing - run 'make firmware-f02' first")


def test_the_key_is_in_both_images_in_flash():
    """The premise, checked before anything is run.

    The key is in this OBC because the OBC signs its reports, and the pair holds signing ON in both
    halves. If a build lost the key - signing switched off, or the constant optimised into
    something with another name - the hardened half would refuse a dump of an address holding
    nothing, and test 3 would pass without the allowlist having protected anything.
    """
    _require(VULN, HARD)
    for elf in (VULN, HARD):
        copies = _solve.object_symbols(elf, _solve.KEY_SYMBOL)
        assert len(copies) == 1, (
            f"{elf} holds {len(copies)} copies of {_solve.KEY_SYMBOL}; this exercise reads one, "
            f"and a second is a second place to read the key from that nothing here asserts on")
        address, size = copies[0]
        assert size == len(SDLS_KEY), f"{_solve.KEY_SYMBOL} is {size} octets in {elf}"
        assert address & FLASH_MASK == FLASH_BASE, (
            f"{_solve.KEY_SYMBOL} is at 0x{address:08x} in {elf}, which is not flash; the "
            f"exercise's premise is a static const in .rodata")
        assert _solve.image_bytes(elf, address, size) == SDLS_KEY, (
            f"the octets {elf} stores at {_solve.KEY_SYMBOL} are not cuberange.keys.SDLS_KEY; "
            f"firmware/common/cuberange_keys.h and src/cuberange/keys.py have drifted")

        #: And the block the hardened half serves instead - present in both, in SRAM, starting
        #: with the magic test 4 looks for.
        public, plen = _solve.object_symbol(elf, _solve.PUBLIC_SYMBOL)
        assert plen == _solve.PUBLIC_LEN, f"{_solve.PUBLIC_SYMBOL} is {plen} octets in {elf}"
        assert public & FLASH_MASK != FLASH_BASE, (
            f"{_solve.PUBLIC_SYMBOL} is in flash in {elf}; it is meant to be live SRAM")
        assert _solve.image_bytes(elf, public, len(_solve.PUBLIC_MAGIC)) == _solve.PUBLIC_MAGIC


def test_the_dump_reads_the_key_and_the_key_verifies_the_spacecrafts_reports():
    _require(VULN)
    key_at, key_len = _solve.object_symbol(VULN, _solve.KEY_SYMBOL)
    with Range(VULN) as r:
        assert r.alive(), "the satellite was not answering before the attack"
        #: A report the spacecraft signed for somebody else's question, captured before the dump.
        report, _ = r.signed_test_report()
        assert report is not None, (
            f"no PUS 17,2 to check the key against:\n{r.console(OBC_CONSOLE)}")

        r.dump(key_at, key_len)
        dump = r.await_dump()
        assert dump is not None, (
            f"no TM 6,6 arrived for a dump of 0x{key_at:08x}:\n{r.console(OBC_CONSOLE)}")
        assert (dump.mem_id, dump.address) == (_solve.MEM_UNIFIED, key_at)
        assert dump.data == SDLS_KEY, (
            f"the dump returned {dump.data.hex()}, not the key")
        assert r.wait_console(OBC_CONSOLE, f"PUS 6 dump of {key_len} octets at 0x{key_at:08x} sent",
                              seconds=10), r.console(OBC_CONSOLE)

        #: Proved by use. The extracted octets verify a report this run produced for an unrelated
        #: request, and the report that carried them; one bit away verifies neither.
        wrong = bytes([dump.data[0] ^ 0x01]) + dump.data[1:]
        assert _solve.signed_by(report, dump.data), (
            "the extracted octets do not verify the spacecraft's own PUS 17,2 - whatever was "
            "dumped, it is not the key this spacecraft signs with")
        assert _solve.signed_by(dump.packet, dump.data), (
            "the extracted octets do not verify the report that carried them")
        assert not _solve.signed_by(report, wrong), (
            "a key one bit away also verifies, so this check accepts anything")
        assert not _solve.signed_by(dump.packet, wrong)


def test_the_mitigated_obc_refuses_to_dump_the_key():
    _require(HARD)
    key_at, key_len = _solve.object_symbol(HARD, _solve.KEY_SYMBOL)
    with Range(HARD) as r:
        assert r.alive(), "the satellite was not answering before the attack"

        r.dump(key_at, key_len)
        assert r.wait_console(OBC_CONSOLE, "REJECTED PUS 6 dump", seconds=15), (
            f"the OBC did not log a rejection, so the request may never have arrived - which "
            f"would make this test pass for the wrong reason:\n{r.console(OBC_CONSOLE)}")

        answer, before = r.signed_test_report()
        assert answer is not None, (
            f"the spacecraft went quiet even though the dump was refused:\n"
            f"{r.console(OBC_CONSOLE)}")
        leaked = [tm for _, tm in before if _solve.is_dump_report(tm)]
        assert not leaked, f"a dump report arrived anyway: {leaked}"
        assert not any(SDLS_KEY in packet for packet, _ in before), (
            "the key's octets went down the link in some other report")
        obc = r.console(OBC_CONSOLE)
        assert f"PUS 6 dump of {key_len} octets at 0x{key_at:08x} sent" not in obc, obc


def test_the_mitigation_does_not_break_the_dump_service():
    """A control that refuses every dump is not a mitigation, it is a dead service."""
    _require(HARD)
    public, plen = _solve.object_symbol(HARD, _solve.PUBLIC_SYMBOL)
    with Range(HARD) as r:
        assert r.alive(), "the satellite was not answering before the dump"

        r.dump(public, plen)
        dump = r.await_dump()
        assert dump is not None, (
            f"a dump of the public block was not served:\n{r.console(OBC_CONSOLE)}")
        assert (dump.address, len(dump.data)) == (public, plen)
        assert dump.data.startswith(_solve.PUBLIC_MAGIC), dump.data.hex()
        #: Live, not the boot image: the block names the report that carries it.
        assert int.from_bytes(dump.data[12:14], "big") == dump.tm.msg_counter, dump.data.hex()
        #: And still signed with the operator's key: the hardened half changed which memory a dump
        #: may read, not what the spacecraft does with a report.
        assert _solve.signed_by(dump.packet, SDLS_KEY), "the served dump does not verify"
        assert "REJECTED" not in r.console(OBC_CONSOLE)


def test_a_dump_that_starts_in_the_block_and_runs_past_it_is_refused():
    """The boundary. Half inside the block and half past its end.

    Test 4 asks for exactly the block, and a check on the START address alone serves that just as a
    correct one does. It serves this request too, because this one also starts inside the block -
    so this is the request that tells the two apart, and the reason dump_allowed() checks both
    edges.
    """
    _require(HARD)
    public, plen = _solve.object_symbol(HARD, _solve.PUBLIC_SYMBOL)
    start, length = public + plen // 2, plen
    with Range(HARD) as r:
        assert r.alive(), "the satellite was not answering before the dump"

        r.dump(start, length)
        assert r.wait_console(OBC_CONSOLE, "REJECTED PUS 6 dump", seconds=15), (
            f"a dump of 0x{start:08x}+{length} straddling the block's end was not refused:\n"
            f"{r.console(OBC_CONSOLE)}")

        answer, before = r.signed_test_report()
        assert answer is not None, "the spacecraft went quiet after a refused dump"
        assert not [tm for _, tm in before if _solve.is_dump_report(tm)], (
            "a dump report arrived for a refused request")
