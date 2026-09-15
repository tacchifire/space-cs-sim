#!/usr/bin/env python3
"""EX-U01 model solution: deny the uplink, and watch every downlink control report health.

The attacker sits where EX-D02's did - between the operator and the spacecraft - and points the
other way. Nothing about the downlink changes: the beacon arrives, the counter has no gaps, the
pass schedule says ok. The commands simply never happen.

    --deny      take every uplink frame
    --no-deny   let them through, for the row to compare against

Run it against both builds of the pair. With acknowledgements the operator sees four commands sent
and four acknowledged, or four and zero. Without them it is zero either way, and the console that
reports a healthy link is telling the truth about the only direction it can see.
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
from cuberange.gs.passes import Pass, PassLog                     # noqa: E402
from cuberange.gs.station import GroundStation                    # noqa: E402
from cuberange.identity import GROUND_STATIONS                    # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deny", action="store_true", help="take every uplink frame")
    ap.add_argument("--commands", type=int, default=4)
    ap.add_argument("--window", type=float, default=12.0)
    args = ap.parse_args()

    channel = LinkChannel(listen_port=ports.channel(0), sat_port=ports.link(0),
                          uplink_filter=(lambda _f: False) if args.deny else None).start()
    link = SpaceLink(port=ports.channel(0))
    link.connect(retries=60)
    station = GroundStation(link, station_id=GROUND_STATIONS["primary"],
                            require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY, sdls_spi=SDLS_SPI)
    try:
        start = time.time()
        window = Pass("pass", start=start, end=start + args.window, expect_at_least=1)
        log, sent = PassLog(), 0
        gap = args.window / (args.commands + 1)
        while time.time() < window.end:
            if sent < args.commands and (time.time() - start) > sent * gap:
                station.send_connection_test()
                sent += 1
            time.sleep(0.4)
            station.collect()
            while len(log.heard) < len(station.telemetry):
                log.record()

        print(f"  uplink denied:            {args.deny}")
        print(f"  commands sent:            {sent}")
        print(f"  uplink frames taken:      {len(channel.uplink_suppressed)}")
        print(f"  telemetry heard:          {log.in_window(window)}")
        print(f"  counter gaps:             {station.counter_gaps}")
        print(f"  the pass schedule says:   {log.verdict(window)!r}")
        print(f"  commands acknowledged:    {len(station.acknowledged)}")
        if sent and not station.acknowledged:
            print("  Nothing came back for any command. Before calling that an attack: does this")
            print("  spacecraft acknowledge anything? A build that never does is silent by design.")
    finally:
        link.close()
        channel.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
