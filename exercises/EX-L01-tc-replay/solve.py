#!/usr/bin/env python3
"""EX-L01 model solution: replay a telecommand without understanding it.

Assumes a scenario is already running (see the exercise README). Captures the frame the operator's
rail-off command puts on the link, waits for the satellite to recover, and sends the same bytes
again.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cuberange import ports  # noqa: E402
from cuberange.channel.link_channel import LinkChannel  # noqa: E402
from cuberange.gs.link import SpaceLink                 # noqa: E402
from cuberange.gs.station import GroundStation          # noqa: E402

SAT_LINK_PORT = ports.link(0)
GS_LINK_PORT = ports.channel(0)


def main() -> int:
    channel = LinkChannel(listen_port=GS_LINK_PORT, sat_port=SAT_LINK_PORT).start()
    link = SpaceLink(port=GS_LINK_PORT)
    link.connect()
    station = GroundStation(link)
    try:
        print("1. operator sends a legitimate rail-off command")
        before = len(channel.uplink_frames)
        station.set_comm_rail(False)
        if not channel.wait_for_uplink(before + 1, timeout=10):
            print("   nothing crossed the link", file=sys.stderr)
            return 1
        frame = channel.uplink_frames[-1]
        print(f"   captured {len(frame)} octets: {frame.hex()}")

        print("2. waiting for the satellite's FDIR to restore the radio (up to 45 s)")
        time.sleep(45)

        print("3. replaying the same bytes - no key, no parsing")
        channel.replay(frame)
        print("   watch for a second 'EPS: COMM rail OFF (commanded)'")
        return 0
    finally:
        link.close()
        channel.stop()


if __name__ == "__main__":
    sys.exit(main())
