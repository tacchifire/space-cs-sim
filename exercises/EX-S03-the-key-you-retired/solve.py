#!/usr/bin/env python3
"""EX-S03 model solution: you rotated the key. Prove the old one no longer opens the door.

The operator has activated a second Security Association - SPI 10, a new key - and is commanding
on it. Everything works. That is not evidence of anything: a rotation that ACTIVATES the new SA
and leaves the old one operational works exactly as well, and has retired nothing.

So the test is to transmit on the retired association on purpose and look at what comes back:

    phase 1   four commands on the new SA (SPI 10)      -> expected to be accepted
    phase 2   four commands on the RETIRED SA (SPI 9)   -> expected to be REFUSED

One station does both, switching its own `sdls_key` and `sdls_spi` between phases, because that
is what an operator has: one antenna, one frame counter, two associations. The mission-layer key
is unchanged throughout and deliberately so - a rotation moved ONE of this range's two keys, and
`mitigation.md` is about the half that did not move.

    --no-retired-test   skip phase 2, for the row to compare against
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
from cuberange.keys import (SDLS_KEY, SDLS_KEY_ROTATED, SDLS_SPI,  # noqa: E402
                            SDLS_SPI_ROTATED)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-retired-test", action="store_true")
    ap.add_argument("--commands", type=int, default=4)
    args = ap.parse_args()

    link = SpaceLink(port=ports.link(0))
    link.connect(retries=60)
    #: On the NEW association. `uplink_key` is the mission-layer key and does not change;
    #: `sdls_key` is the Security Association's and does.
    station = GroundStation(link, station_id=GROUND_STATIONS["primary"],
                            vcid=GROUND_VCIDS["primary"],
                            require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY,
                            sdls_key=SDLS_KEY_ROTATED, sdls_spi=SDLS_SPI_ROTATED)

    def listen(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            station.collect()
            time.sleep(0.25)

    try:
        listen(6.0)                     # acquisition of signal, and the counters' baseline
        for _ in range(args.commands):
            station.send_connection_test()
            listen(1.0)
        on_new_sa = len(station.acknowledged)

        retired = 0
        if not args.no_retired_test:
            #: THE TEST. Same station, same virtual channel, same frame counter - only the
            #: association changes. A spacecraft that still answers here has not retired anything.
            station.sdls_key, station.sdls_spi = SDLS_KEY, SDLS_SPI
            for _ in range(args.commands):
                station.send_connection_test()
                retired += 1
                listen(1.0)
        listen(6.0)

        print(f"  commands on the new SA (SPI {SDLS_SPI_ROTATED}):      {on_new_sa} acknowledged")
        print(f"  commands on the retired SA (SPI {SDLS_SPI}):  {retired} sent, "
              f"{len(station.acknowledged) - on_new_sa} acknowledged")
        print(f"  frames the radio received:            {station.link_frames_received}")
        print(f"  frames the radio refused:             {station.link_frames_refused}")
        print(f"  the association it last refused:      {station.link_refused_spi}")
        if retired and len(station.acknowledged) == on_new_sa:
            print(f"  The retired key was refused. SPI {station.link_refused_spi} is the one you")
            print("  retired, and the radio naming it is what makes this proof rather than hope.")
        elif retired:
            print("  THE RETIRED KEY STILL WORKS. The rotation activated a new association and")
            print("  left the old one operational, so nothing was retired - and notice that")
            print("  phase 1 looked exactly the same either way. Working is not evidence.")
        else:
            print("  Phase 2 was skipped, so this run says nothing about the old key. That is")
            print("  the ordinary state of a rotation nobody tested: it looks like success.")
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
