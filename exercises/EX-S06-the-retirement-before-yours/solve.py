#!/usr/bin/env python3
"""EX-S06 model solution: two retirements of one SA, and what the beacon keeps.

EX-S05 put WHO retired an SA on the beacon - but only the LAST retirement. This is the sequence that
limit hides: a partner retires SPI 9 (a theft), then the operator retires the same SPI 9 (a planned
rotation). The snapshot keeps only the second, so the theft is overwritten AND reattributed to the
operator's own key. The log keeps both, oldest first, so the theft the rotation overwrote is still
readable.

    phase 1   a PARTNER (SPI 11) sends STOP_SA(9)      -> the operator's SA goes dark
    phase 2   the OPERATOR (SPI 10) sends STOP_SA(9)   -> a planned rotation, same SPI, arriving after
    phase 3   read the beacon: one entry (snapshot) or two (log)?

On the vulnerable build the beacon says "SPI 9 retired by SPI 10" and the theft is invisible. On the
hardened build it says "SPI 9 by SPI 11" THEN "SPI 9 by SPI 10", and a partner getting there first
is on the downlink.
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

    def retire(target_spi, key, spi):
        station.sdls_key, station.sdls_spi = key, spi
        station.send_sa_stop(target_spi)
        listen(2.0)

    try:
        listen(6.0)
        retire(SDLS_SPI, SDLS_KEY_PARTNER, SDLS_SPI_PARTNER)   # the theft
        retire(SDLS_SPI, SDLS_KEY_ROTATED, SDLS_SPI_ROTATED)  # the planned rotation, same SPI
        listen(7.0)   # let the beacon carry whatever the spacecraft now knows

        log = station.sa_retire_log
        print(f"  STOP_SA(9) sent twice: by SPI {SDLS_SPI_PARTNER} (partner), "
              f"then by SPI {SDLS_SPI_ROTATED} (operator)")
        print(f"  beacon SA-management log ({len(log)} "
              f"{'entry' if len(log) == 1 else 'entries'}):")
        for i, (target, by) in enumerate(log):
            print(f"      {i}: SPI {target} retired by SPI {by}")

        if len(log) <= 1:
            print(f"  Only the LAST retirement survived - SPI {SDLS_SPI} attributed to the operator's")
            print(f"  own SPI {SDLS_SPI_ROTATED}. The partner's theft (by SPI {SDLS_SPI_PARTNER}) was "
                  f"overwritten and")
            print("  reattributed. From the ground this is a benign self-rotation. Vulnerable build.")
        elif log[0] == (SDLS_SPI, SDLS_SPI_PARTNER):
            print(f"  The log kept BOTH, oldest first: the theft by SPI {SDLS_SPI_PARTNER} is the "
                  f"first entry -")
            print("  the one the snapshot overwrote. A retirement before the operator's own is on the")
            print("  downlink now, authenticated by the signed beacon. Hardened build.")
        else:
            print("  The log carried more than one entry but not the theft-first order expected; "
                  "inspect it above.")
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
