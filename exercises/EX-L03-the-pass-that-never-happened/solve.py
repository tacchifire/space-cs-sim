#!/usr/bin/env python3
"""EX-L03 model solution: the attacker denies the pass, and the schedule is what notices.

Four runs, because the finding is in the comparison rather than in any one of them:

    --beacon/--no-beacon   which spacecraft is flying
    --deny/--no-deny       whether anything is allowed through

A counter gap needs two reports for it to sit between, so total denial produces an EMPTY gap list
- the same list a perfect pass produces. The schedule is the only thing here that knows the
difference, and it only knows it when the spacecraft says something unprompted.

Run it four ways and read the table. The row that matters is the one with no beacon and no
attacker, where a procedure built on a schedule alone reports a silent pass that nobody caused.
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
from cuberange.gs.passes import Pass, PassLog                   # noqa: E402
from cuberange.gs.station import GroundStation                    # noqa: E402
from cuberange.identity import GROUND_STATIONS                    # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deny", action="store_true", help="deny every downlink frame")
    ap.add_argument("--window", type=float, default=15.0, help="pass length in seconds")
    args = ap.parse_args()

    channel = LinkChannel(listen_port=ports.channel(0), sat_port=ports.link(0),
                          downlink_filter=(lambda _f: False) if args.deny else None).start()
    link = SpaceLink(port=ports.channel(0))
    link.connect(retries=60)
    station = GroundStation(link, station_id=GROUND_STATIONS["primary"],
                            require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY, sdls_spi=SDLS_SPI)
    try:
        start = time.time()
        #: expect_at_least, not a rate. The two clocks here run at different speeds and the
        #: schedule module says why at length - a rate calibrated on one and evaluated on the
        #: other is wrong by a ratio that is not constant.
        window = Pass("pass", start=start, end=start + args.window, expect_at_least=1)
        log = PassLog()
        print(f"  a {args.window:.0f}-second window; the attacker is "
              f"{'denying everything' if args.deny else 'not there'}")
        while time.time() < window.end:
            time.sleep(0.4)
            station.collect()
            while len(log.heard) < len(station.telemetry):
                log.record()

        print(f"  heard in the window:       {log.in_window(window)}")
        print(f"  counter gaps:              {station.counter_gaps}")
        print(f"  the schedule's verdict:    {log.verdict(window)!r}")
        if log.verdict(window) == "silent":
            print("  Before calling this an attack: does this spacecraft say anything when")
            print("  nobody asks? A schedule with nothing to expect is silent every pass.")
    finally:
        link.close()
        channel.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
