#!/usr/bin/env python3
"""EX-U03 model solution: the attack fails completely and nobody ever finds out.

This attacker does not have the key. That is the ordinary case - EX-U02's intruder is the rarer
one - and every control this range built for the uplink refuses them: the SDLS MAC does not
verify on a forged frame, the anti-replay counter refuses a recording, and neither frame's payload
ever reaches the on-board computer.

The defence works. The attack achieves nothing. And on the vulnerable build every indicator the
operator has reports an ordinary pass, including `unexplained_commands` - EX-U02's brand-new
detector - which reads zero throughout, because it counts what the COMPUTER heard and the radio
is in front of it.

    --no-attacker       nobody transmits at the spacecraft, for the row to compare against
    --replays N         captured frames sent again
    --forgeries N       captured frames with a payload octet flipped and the FECF recomputed

A forgery needs no key: the FECF is a CRC, not a MAC, and anybody can recompute it. That is the
whole point of having a MAC underneath it.
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
from cuberange.proto.crc import crc16_ccsds                       # noqa: E402

#: The frame's last two octets are the FECF, a CRC-16 over everything before it. An attacker with
#: no key can still produce a frame whose FECF is correct - that is what a checksum is for, and
#: what it is not for. Flipping an octet of the authenticated payload and fixing the FECF produces
#: a frame that passes every shape and integrity check this range has and fails the MAC.
FECF_LEN = 2


def forge(frame: bytes) -> bytes:
    """A captured frame with one payload octet changed and the FECF made correct again."""
    body = bytearray(frame[:-FECF_LEN])
    body[-1] ^= 0x01
    return bytes(body) + crc16_ccsds(bytes(body)).to_bytes(FECF_LEN, "big")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-attacker", action="store_true")
    ap.add_argument("--replays", type=int, default=10)
    ap.add_argument("--forgeries", type=int, default=10)
    ap.add_argument("--commands", type=int, default=4)
    args = ap.parse_args()

    channel = LinkChannel(listen_port=ports.channel(0), sat_port=ports.link(0)).start()
    link = SpaceLink(port=ports.channel(0))
    link.connect(retries=60)
    station = GroundStation(link, station_id=GROUND_STATIONS["primary"],
                            vcid=GROUND_VCIDS["primary"],
                            require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY, sdls_spi=SDLS_SPI)

    def listen(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            station.collect()
            time.sleep(0.25)

    try:
        #: Receive before transmitting, which is how a pass starts and what fixes the baseline.
        deadline = time.time() + 6.0
        while time.time() < deadline and station.link_frames_refused is None \
                and station.unexplained_commands is None:
            station.collect()
            time.sleep(0.2)

        for _ in range(args.commands):
            station.send_connection_test()
            listen(1.2)

        thrown = 0
        if not args.no_attacker:
            captured = channel.uplink_frames[-1]
            for _ in range(args.replays):
                channel.replay(captured)
                thrown += 1
                time.sleep(0.12)
            for _ in range(args.forgeries):
                channel.replay(forge(captured))
                thrown += 1
                time.sleep(0.12)
        listen(6.0)

        print(f"  frames thrown at the spacecraft: {thrown}")
        print(f"  our commands:                    {station.commands_sent}")
        print(f"  ours acknowledged:               {len(station.acknowledged)}")
        print(f"  reports we never heard:          {station.reports_missing}")
        print(f"  telecommands we did not send:    {station.unexplained_commands}")
        if station.link_frames_refused is None:
            print("  frames the radio refused:        THIS RADIO DOES NOT REPORT")
            print("  Every control worked. Every frame was thrown away. The console on board says")
            print("  so twenty times and no part of it leaves the spacecraft.")
        else:
            print(f"  frames the radio received:       {station.link_frames_received}")
            print(f"  frames the radio refused:        {station.link_frames_refused}")
        if station.unexplained_commands == 0 and thrown:
            print("  Note the zero above it. That detector counts what the COMPUTER heard, and")
            print("  the radio is in front of the computer - so the control that stopped this")
            print("  attack is also what hid it from the detector built to notice attacks.")
    finally:
        link.close()
        channel.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
