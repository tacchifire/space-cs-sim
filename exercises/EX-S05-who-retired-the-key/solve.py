#!/usr/bin/env python3
"""EX-S05 model solution: an SA was retired - can you tell from the ground WHO retired it?

EX-S04 stopped an unauthorised retirement. This is the other half: when a retirement DOES happen -
a partner's theft, or the operator's own rotation - the ground sees SPI 9 stop answering and, until
now, nothing about who caused it. Authority is off in this exercise on purpose, so the partner's
retirement lands; the question is whether the spacecraft REPORTS who did it.

    phase 1   the operator commands on SPI 9        -> accepted
    phase 2   STOP_SA(9) is sent (partner, or --legitimate operator-from-SPI-10)
    phase 3   read the beacon: does it name who retired SPI 9?

On the vulnerable build the beacon is silent and a theft is indistinguishable from a rotation. On
the hardened build the beacon says "SPI 9 retired by SPI 11" (a partner) or "by SPI 10" (yourself).

    --legitimate   retire SPI 9 as the OPERATOR would, authenticated under its own SPI 10, instead
                   of as the partner. On the hardened build this is attributed to SPI 10 - a
                   rotation that reads as self rather than as an attack.
"""
import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports                                       # noqa: E402
from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.gs.station import GroundStation                    # noqa: E402
from cuberange.identity import GROUND_STATIONS, GROUND_VCIDS      # noqa: E402
from cuberange.keys import (SDLS_KEY, SDLS_KEY_PARTNER, SDLS_KEY_ROTATED,  # noqa: E402
                            SDLS_SPI, SDLS_SPI_PARTNER, SDLS_SPI_ROTATED)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--legitimate", action="store_true",
                    help="retire SPI 9 as the operator (from SPI 10), not as the partner")
    ap.add_argument("--commands", type=int, default=4)
    args = ap.parse_args()

    link = SpaceLink(port=ports.link(0))
    link.connect(retries=60)
    station = GroundStation(link, station_id=GROUND_STATIONS["primary"],
                            vcid=GROUND_VCIDS["primary"],
                            require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY,
                            sdls_key=SDLS_KEY, sdls_spi=SDLS_SPI)

    def listen(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            station.collect()
            time.sleep(0.25)

    try:
        listen(6.0)
        for _ in range(args.commands):
            station.send_connection_test()
            listen(1.0)
        before = len(station.acknowledged)

        if args.legitimate:
            who, key, spi = "the operator (from SPI 10)", SDLS_KEY_ROTATED, SDLS_SPI_ROTATED
        else:
            who, key, spi = "a PARTNER (SPI 11)", SDLS_KEY_PARTNER, SDLS_SPI_PARTNER
        station.sdls_key, station.sdls_spi = key, spi
        station.send_sa_stop(SDLS_SPI)
        listen(2.0)

        station.sdls_key, station.sdls_spi = SDLS_KEY, SDLS_SPI
        for _ in range(args.commands):
            station.send_connection_test()
            listen(1.0)
        after = len(station.acknowledged) - before
        listen(7.0)   # let the beacon carry whatever the spacecraft now knows

        print(f"  STOP_SA(9) was sent by:            {who}")
        print(f"  operator commands before/after:   {before}/{after} of {args.commands}")
        print(f"  beacon says the retired SA was:    "
              f"{'SPI %d' % station.sa_retired_target if station.sa_retired_target is not None else 'nothing'}")
        print(f"  beacon says it was retired BY:     "
              f"{'SPI %d' % station.sa_retired_by if station.sa_retired_by is not None else 'nothing'}")
        if station.sa_retired_by is None:
            print(f"  SPI {SDLS_SPI} went dark and the ground was told NOTHING about who did it. A")
            print("  theft and the operator's own rotation are the same observation - which is")
            print("  exactly the blind spot EX-S04 ended on. This is the vulnerable build.")
        elif station.sa_retired_by == SDLS_SPI_PARTNER:
            print(f"  The beacon names SPI {SDLS_SPI_PARTNER} - a PARTNER, not the operator's own SPI")
            print(f"  {SDLS_SPI_ROTATED}. The operator can see the retirement was not theirs. That is the")
            print("  whole of the fix: attribution, on the downlink, authenticated by the signed beacon.")
        else:
            print(f"  The beacon names SPI {station.sa_retired_by} - the operator's own key. A rotation")
            print("  reads as self, not as an attack: the report names the actual retirer rather")
            print("  than crying wolf at every retirement.")
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
