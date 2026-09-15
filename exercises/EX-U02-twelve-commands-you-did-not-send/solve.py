#!/usr/bin/env python3
"""EX-U02 model solution: someone else has the key, and the only thing that saw it is a counter.

The intruder here is not in the middle of anything. They hold the uplink key, they transmit the
frames a ground station transmits, and every control on board answers them correctly - the SDLS
MAC verifies, the anti-replay counter is fresh, the telecommand authentication verifies. Nothing
is broken. Cryptography cannot tell a key from the person holding it.

They transmit while the operator is out of view, which is where the exercise is. The downlink
their probes provoke is transmitted into an empty sky, so an operator watching the live link sees
nothing because there was nothing to see AT THE TIME. The spacecraft's own count of what it heard
is still there on the next pass.

    --no-intruder   nobody else transmits, for the row to compare against
    --intruder-commands N

Run it against both builds of the pair. The hardened one reports twelve telecommands the operator
did not send; the vulnerable one reports that it cannot tell, which is the honest answer and not
the same as zero.
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
from cuberange.identity import GROUND_STATIONS, GROUND_VCIDS      # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402

#: The intruder's source id, and the reason it is not one of this range's station ids.
#:
#: An intruder holding the key could write a real station's id into the field and look entirely
#: legitimate. That is a different exercise and a different detector - the acknowledgements would
#: come back for request ids the real station never issued, which is EX-D01's machinery pointed at
#: acceptance instead of refusal. This one is EARLIER than that: an intruder mapping a spacecraft's
#: service tree before touching anything, who has no reason yet to disturb the operator's session.
INTRUDER_SOURCE_ID = 0x0099

#: A virtual channel of their own, for the reason EX-G03 measured: this COMM keeps its anti-replay
#: counter per virtual channel, so two transmitters on one link do not lock each other out. An
#: intruder who did not know that would jam the operator and be found in an afternoon.
INTRUDER_VCID = 2

#: A PUS service this OBC does not implement. The handler prints one line on a console nobody off
#: the spacecraft can read and returns - no report, no refusal, no state change, nothing on the
#: downlink that was not going to be there anyway. Service 3 subtype 1 is a housekeeping parameter
#: request in ECSS-E-ST-70-41C; asking for one is exactly what an intruder learning the service
#: tree would send, and here it is answered with silence.
PROBE_SERVICE, PROBE_SUBTYPE = 3, 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-intruder", action="store_true", help="nobody else transmits")
    ap.add_argument("--intruder-commands", type=int, default=12)
    ap.add_argument("--commands", type=int, default=4, help="what the operator sends per pass")
    ap.add_argument("--deny-second-pass", action="store_true",
                    help="no intruder; take the second pass's uplink frames instead")
    args = ap.parse_args()

    #: The operator's view of the sky, as a flag the channel reads per frame. Out of view means
    #: the frames do not arrive - not that the station stops reading them, which a TCP socket
    #: would simply buffer and hand over later. The distinction is the exercise: a detector that
    #: reads the live downlink can only see what it was present for.
    in_view = {"now": True}
    uplink_open = {"now": True}
    channel = LinkChannel(listen_port=ports.channel(0), sat_port=ports.link(0),
                          downlink_filter=lambda _f: in_view["now"],
                          uplink_filter=lambda _f: uplink_open["now"]).start()
    link = SpaceLink(port=ports.channel(0))
    link.connect(retries=60)
    station = GroundStation(link, station_id=GROUND_STATIONS["primary"],
                            vcid=GROUND_VCIDS["primary"],
                            require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY, sdls_spi=SDLS_SPI)

    intruder_link = SpaceLink(port=ports.channel(0))
    intruder_link.connect(retries=60)
    intruder = GroundStation(intruder_link, station_id=INTRUDER_SOURCE_ID, vcid=INTRUDER_VCID,
                             require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY, sdls_spi=SDLS_SPI)

    def pass_over(seconds: float, send: int = 0) -> None:
        """Collect for `seconds`, transmitting `send` commands spread through it."""
        start, sent = time.time(), 0
        while time.time() - start < seconds:
            if sent < send and (time.time() - start) > sent * (seconds / (send + 1)):
                station.send_connection_test()
                sent += 1
            time.sleep(0.3)
            station.collect()

    def acquire(seconds: float = 6.0) -> None:
        """Listen before transmitting, which is both how a pass starts and what makes the
        arithmetic exact: a baseline taken after the first command was already counted measures
        from an origin one command in."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            station.collect()
            if station.unexplained_commands is not None:
                return
            time.sleep(0.2)

    try:
        acquire()
        # --- pass 1: an ordinary pass. The baseline is taken here, from a beacon, and it is the
        # only thing that makes the second pass mean anything. A station with no record of where
        # the counter was cannot say where it should be.
        pass_over(8.0, send=args.commands)
        acked_pass1 = len(station.acknowledged)
        baseline_unexplained = station.unexplained_commands

        # --- between passes: the spacecraft is not over this station. -------------------------
        in_view["now"] = False
        probes = 0
        if not args.no_intruder:
            #: LISTEN FIRST. This range has one Security Association, so its SDLS anti-replay is
            #: one counter for the whole link - COMM's own source says so. An intruder starting
            #: from sequence 1 is behind the operator and is refused as a replay by a control
            #: aimed at somebody else: measured, twelve probes from sequence 1 got eight through.
            #: Reading the counter off a captured frame is what the key buys them.
            intruder.observe_uplink_sequence(channel.uplink_frames[-1])
            for _ in range(args.intruder_commands):
                intruder.send_pus(PROBE_SERVICE, PROBE_SUBTYPE)
                probes += 1
                time.sleep(0.15)
        time.sleep(2.0)
        heard_while_away = len(station.telemetry)

        # --- pass 2: nothing has changed on the link. --------------------------------------------
        in_view["now"] = True
        if args.deny_second_pass:
            uplink_open["now"] = False
        station.collect()
        telemetry_before_pass2 = len(station.telemetry)
        pass_over(8.0, send=args.commands)

        print(f"  an intruder transmitted:    {not args.no_intruder}")
        print(f"  our second pass denied:     {args.deny_second_pass}")
        print(f"  their telecommands:         {probes}")
        print(f"  our telecommands:           {station.commands_sent}")
        print(f"  ours acknowledged:          {len(station.acknowledged)} "
              f"({acked_pass1} in pass 1)")
        print(f"  telemetry we heard:         {len(station.telemetry)} "
              f"({heard_while_away} of it while out of view)")
        print(f"  reports we never heard:     {station.reports_missing} "
              f"(the beacon ran while we were out of view)")
        print(f"  telemetry for someone else: {len(station.not_for_us)}")
        if station.unexplained_commands is None:
            print("  telecommands we did not send: THIS SPACECRAFT DOES NOT COUNT")
            print("  Which is not the same as zero. Every control on board passed this intruder,")
            print("  every report was correct, and there is no number to be wrong about.")
        else:
            print(f"  telecommands the spacecraft heard: {station.commands_heard}")
            print(f"  telecommands we did not send: {station.unexplained_commands} "
                  f"(a NET: {station.commands_heard} heard against {station.commands_sent} sent)")
            print(f"  telecommands refused:         {station.commands_refused}")
            if station.unexplained_commands > 0 and station.commands_refused == 0:
                print("  Not one of them was refused, which is worth as much as the count. They")
                print("  are not trying to do anything yet - they are finding out what is here.")
            elif station.unexplained_commands < 0:
                print("  Negative: the spacecraft heard FEWER than we sent. Nobody else is using")
                print("  our key - our uplink is being eaten, and no key rotation will fix it.")
        _ = (baseline_unexplained, telemetry_before_pass2)
    finally:
        intruder_link.close()
        link.close()
        channel.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
