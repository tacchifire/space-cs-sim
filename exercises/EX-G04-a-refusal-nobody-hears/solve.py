"""EX-G04 model solution: make an operator debug the wrong thing.

There is nothing to break here. EX-G02's authority check is on and it works: the spacecraft
refuses a command from a station that may not send it. This script simply sends that command and
then asks the question the operator will be asking five minutes later.

    python3 exercises/EX-G04-a-refusal-nobody-hears/solve.py

The output is the exercise. A refused command and a command that never arrived produce the same
observation on the ground: silence. Not a different silence - the same one. An operator with a
pass window open and a spacecraft that will not respond checks the antenna, the cable, the
pointing, the scheduler, and the link budget, because those are the things that produce silence.
The one thing that actually happened is not on the list, because nothing on the spacecraft ever
said it.

WHAT THIS IS NOT. It is not an exploit, and it is not availability lost to an attacker - the
attacker here is a configuration. It is the cost of a control that cannot report, which is a
security property and gets treated as an operations detail until the pass is over.
"""
import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.node import GroundStationNode                # noqa: E402
from cuberange.ports import channel as channel_port            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--station", default="backup",
                    help="a station that is not granted 'power' on this spacecraft")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--wait", type=float, default=8.0,
                    help="how long to listen for the spacecraft to say anything about it")
    args = ap.parse_args()

    port = args.port if args.port is not None else channel_port(0)
    node = GroundStationNode(args.station, override=True, link_port=port).connect(retries=60)
    try:
        print(f"\n{args.station}: sending PUS 8 function 1 (COMM rail off)")
        node.do("rail-off")

        print(f"listening {args.wait:.0f}s for anything about it...")
        refusal = node._station.await_refusal(timeout=args.wait)
        if refusal is None:
            print("\n  nothing.")
            print("  The spacecraft refused it. The console on board says so. The ground cannot")
            print("  tell that from a frame that was never received, and will not find out")
            print("  during this pass.")
            return 1
        print(f"\n  {refusal}")
        print("  Which request, and why. The operator stops debugging the radio.")
        return 0
    finally:
        node.close()


if __name__ == "__main__":
    sys.exit(main())
