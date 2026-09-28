#!/usr/bin/env python3
"""EX-S07 model solution: a retirement in your name, and the one record that can say it was not you.

EX-S06 kept the whole SA-management log, every entry attributed to the SA whose MAC verified the
directive. Attribution names the KEY, never the hand holding it. This is the case that hides: a
second station holding a COPY of the operator's SPI 10 key retires SPI 9. The log is correct, and it
says the operator did it.

    phase 1   an INTRUDER's station, framing under SPI 10 with a copied key, sends STOP_SA(9)
    phase 2   the operator's station reads the beacon: "SPI 9 retired by SPI 10" - one of its own
    phase 3   the same log, read twice: trusting the attribution, then against the operator's own
              ledger of the directives it sent

    --rotation   the operator retires SPI 9 itself, from its own SPI 10, instead of the intruder.
                 The spacecraft logs the same entry; only the ledger differs, and the reconciling
                 reading finds nothing to explain.
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
from cuberange.keys import (SA_OWNER_OPERATOR, SA_OWNERS, SDLS_KEY,  # noqa: E402
                            SDLS_KEY_ROTATED, SDLS_SPI, SDLS_SPI_ROTATED)

#: The operator's SAs, read from the owner table: SPI 9 and SPI 10.
OPERATOR_SPIS = frozenset(spi for spi, owner in SA_OWNERS.items() if owner == SA_OWNER_OPERATOR)


def entries(log) -> str:
    return "; ".join(f"SPI {t} retired by SPI {b}" for t, b in log) or "nothing"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rotation", action="store_true",
                    help="retire SPI 9 as the operator, from its own SPI 10, not as the intruder")
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
        listen(7.0)   # acquisition: every baseline comes from a beacon, before anything happens
        if args.rotation:
            who = "the OPERATOR's own station, from its SPI 10"
            station.sdls_key, station.sdls_spi = SDLS_KEY_ROTATED, SDLS_SPI_ROTATED
            station.send_sa_stop(SDLS_SPI)
        else:
            #: A separate station. What it transmits is not the operator's, so nothing it sends
            #: enters the operator's ledger - which is the whole premise.
            who = "an INTRUDER's station, holding a copy of SPI 10's key"
            intruder = GroundStation(link, sdls_key=SDLS_KEY_ROTATED, sdls_spi=SDLS_SPI_ROTATED)
            intruder.send_sa_stop(SDLS_SPI)
        listen(9.0)   # the directive, then a beacon carrying whatever the spacecraft now knows

        log = station.sa_retire_log
        print(f"  {'STOP_SA(%d) was sent by:' % SDLS_SPI:<35}{who}")
        print(f"  the beacon's SA-management log:    {entries(log)}")
        print(f"  your ledger (what you sent):       {entries(station.sa_directives_sent)}")
        print("  every instrument this range built to notice an intruder:")
        print(f"      telecommands you did not send  {station.unexplained_commands}"
              "   (a directive is not a telecommand)")
        print(f"      frames the radio refused       {station.link_frames_refused}"
              "   (the key was real)")
        sent = station.commands_sent + len(station.sa_directives_sent)
        rx = station.link_frames_received
        other = rx is not None and rx > sent
        print(f"      frames the radio received      {rx}   (you sent {sent}"
              f"{': someone else transmitted, but not what' if other else ''})")
        if not log:
            print("  Nothing was retired. This exercise runs on EX-S06's hardened COMM - is that")
            print("  the build the range started with?")
            return 0

        #: The same log, read twice. The only thing that changes is whether the station knows
        #: which SAs are its own - which is the whole difference between the halves.
        station.owned_spis = None
        trusting = station.unexplained_sa_retirements
        station.owned_spis = OPERATOR_SPIS
        reconciled = station.unexplained_sa_retirements

        mine = [e for e in log if e[1] in OPERATOR_SPIS]
        print(f"  trusting the attribution:          "
              f"{'SPI %d is yours, so this was you' % mine[-1][1] if mine else 'not yours'}")
        print(f"      unexplained_sa_retirements     {trusting}"
              "   (it cannot say it was not you)")
        print("  against your own ledger:")
        print(f"      unexplained_sa_retirements     {entries(reconciled)}")

        if reconciled:
            print(f"  The log is correct: SPI {SDLS_SPI_ROTATED}'s key did retire SPI {SDLS_SPI}. "
                  "Every instrument agrees it was")
            print("  you, because every one of them names a key. The one record that knows it was")
            print("  not is the operator's own account of what it sent.")
        else:
            print("  The spacecraft logged the same entry an intruder produces. The ledger holds")
            print("  the directive, so it reconciles to nothing: a detector that fired on the")
            print("  operator's own rotation would be switched off by the end of the week.")
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
