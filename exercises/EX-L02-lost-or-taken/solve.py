#!/usr/bin/env python3
"""EX-L02 model solution: ask for every missing report, and see which one never comes.

This solver does not attack anything. It is the operator's procedure:

    1. run a pass and note the gaps in the report counter;
    2. ask the spacecraft to send each missing report again;
    3. whatever comes back was lost by the link. Whatever never comes back is being taken.

Step 3 is the whole exercise, and step 2 is why the vulnerable half of this pair fails CONFIDENTLY
rather than safely: with no report store the spacecraft answers nothing, every gap looks like an
attack, and a procedure that manufactures findings is worse than no procedure.

Usage: run it from inside the range's shell. It brings its own lossy channel, because the losses
are the point - `--loss 0` gives the clean link EX-D02 ran on.
"""
import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                       # noqa: E402
from cuberange.channel.link_channel import LinkChannel            # noqa: E402
from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.gs.station import GroundStation                    # noqa: E402
from cuberange.identity import GROUND_STATIONS                    # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402

OBC_CONSOLE = "l02-sat0-obc.uart"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--loss", type=float, default=0.10, help="downlink frame loss (default 0.10)")
    ap.add_argument("--take", type=int, default=5,
                    help="which report the attacker takes, every time it is sent (0 for none)")
    ap.add_argument("--commands", type=int, default=20)
    ap.add_argument("--out", type=Path, default=None, help="where the consoles are")
    args = ap.parse_args()

    from cuberange.paths import out_dir
    out = args.out or out_dir()
    seen = {"n": 0}

    def decide(frame: bytes) -> bool:
        seen["n"] += 1
        return not (args.take and seen["n"] == args.take)

    channel = LinkChannel(listen_port=ports.channel(0), sat_port=ports.link(0),
                          frame_loss=args.loss,
                          downlink_filter=decide if args.take else None).start()
    link = SpaceLink(port=ports.channel(0))
    link.connect(retries=60)
    station = GroundStation(link, station_id=GROUND_STATIONS["primary"],
                            require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY, sdls_spi=SDLS_SPI)
    try:
        print(f"  a pass of {args.commands} commands over a link losing {args.loss:.0%}")
        for _ in range(args.commands):
            station.send_connection_test()
            time.sleep(1.8)
            station.collect()

        print(f"  the link lost {len(channel.lost)}; the attacker took {len(channel.suppressed)}")
        print(f"  the operator's console shows: {station.counter_gaps}")
        if not station.counter_gaps:
            print("  no gaps. Nothing to ask about, which is also an answer.")
            return 0

        missing = [c for last, cur, _ in station.counter_gaps for c in range(last + 1, cur)]
        print(f"  asking for {missing}")
        returned = []
        for counter in missing:
            station.request_resend(counter)
            time.sleep(4)
            station.collect()
            console = (out / OBC_CONSOLE).read_text(errors="replace")
            if f"resent report {counter} " in console:
                returned.append(counter)

        never = [c for c in missing if c not in returned]
        print(f"  came back (the link lost these):  {returned}")
        print(f"  never came back (somebody has these): {never}")
        if not returned and never:
            print("  NOTE: nothing at all came back. Before concluding that all of these were")
            print("  taken, check whether this spacecraft can resend anything - see mitigation.md.")
    finally:
        link.close()
        channel.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
