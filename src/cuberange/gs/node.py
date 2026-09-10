"""A ground station you can run, sit at, and watch.

Until now "two ground stations" meant two integers passed to one class inside one process. The
request this range was built for asks for ground station NODES, plural, and an instructor putting
two students at two consoles cannot do it with a keyword argument.

    python3 -m cuberange.gs.node --station primary --satellite 0 --do ping
    python3 -m cuberange.gs.node --station backup  --satellite 0            # a console to sit at

Each node has its own identity on the wire (the PUS source id from cuberange.identity), its own
log under $OUT, and its own view of what it is allowed to ask for.

THE OVERRIDE IS THE POINT. A node consults `cuberange.gs.authority` before transmitting and
refuses what the matrix forbids. `--override` sends it anyway, and says so in the log. That is not
a convenience: it is EX-G02 made concrete. The backup operator has to deliberately step past their
own ground-side check, and a vulnerable spacecraft obeys regardless - because the check was never
on the spacecraft. A station that could not be made to misbehave would teach the opposite lesson.

What a node is NOT: authenticated. Nothing here proves a station is who it says it is; the source
id is a field an attacker on the link writes freely. See EX-G02's mitigation.md.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from .. import ports
from ..identity import spacecraft
from ..paths import out_dir
from . import authority
from .link import SpaceLink
from .station import GroundStation

#: What an operator can ask for, and the authority action each needs.
ACTIONS = {
    "ping":     "ping",
    "rail-on":  "power",
    "rail-off": "power",
}


class Refused(RuntimeError):
    """The station's own matrix forbids this, and no override was given."""


class GroundStationNode:
    """One ground station, with an identity, a log and a conscience."""

    def __init__(self, station: str, satellite: int = 0, override: bool = False,
                 log_path: Optional[Path] = None, link_port: Optional[int] = None):
        self.station = station
        self.satellite = satellite
        self.override = override
        self.source_id = authority.station_id(station)     # raises on an unknown station
        self.sat = spacecraft(satellite)
        self.link_port = link_port if link_port is not None else ports.link(satellite)
        self.log_path = log_path or (out_dir() / f"gs-{station}.log")
        self._link: Optional[SpaceLink] = None
        self._station: Optional[GroundStation] = None

    # ------------------------------------------------------------------ plumbing
    def log(self, text: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {self.station}/0x{self.source_id:04X} {text}"
        print(line, flush=True)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(line + "\n")
        except OSError:
            pass                       # a console with no writable $OUT is still a console

    def connect(self, retries: int = 60) -> "GroundStationNode":
        self._link = SpaceLink(port=self.link_port)
        self._link.connect(retries=retries)
        self._station = GroundStation(self._link, station_id=self.source_id,
                                      target_apid=self.sat.apid, target_scid=self.sat.scid)
        self.log(f"up on port {self.link_port}, talking to satellite {self.satellite} "
                 f"(SCID 0x{self.sat.scid:03X})")
        return self

    def close(self) -> None:
        if self._link is not None:
            self._link.close()
            self._link = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ operating
    def permitted(self, action: str) -> bool:
        return authority.may(self.station, self.satellite, ACTIONS[action])

    def do(self, action: str, timeout: float = 10.0):
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}; known: {sorted(ACTIONS)}")
        if self._station is None:
            raise RuntimeError("connect() first")

        allowed = self.permitted(action)
        if not allowed and not self.override:
            self.log(f"REFUSED {action}: this station is not granted "
                     f"'{ACTIONS[action]}' on satellite {self.satellite}")
            raise Refused(f"{self.station} may not {ACTIONS[action]} satellite {self.satellite}")
        if not allowed:
            self.log(f"OVERRIDE {action}: the matrix forbids '{ACTIONS[action]}' and this "
                     f"console is sending it anyway. The spacecraft is what decides now.")

        if action == "ping":
            report = self._station.ping(timeout=timeout)
            self.log("ping answered" if report is not None else "ping unanswered")
            return report
        seq = self._station.set_comm_rail(action == "rail-on")
        self.log(f"PUS 8 function 1 sent, rail {'ON' if action == 'rail-on' else 'OFF'} (seq {seq})")
        return seq


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--station", default="primary",
                    help=f"which ground station ({', '.join(sorted(authority.GROUND_STATIONS))})")
    ap.add_argument("--satellite", type=int, default=0)
    ap.add_argument("--do", dest="action", choices=sorted(ACTIONS),
                    help="run one action and exit; omit for a console")
    ap.add_argument("--override", action="store_true",
                    help="send even what this station's own matrix forbids")
    ap.add_argument("--port", type=int, default=None, help="space link port")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args(argv)

    node = GroundStationNode(args.station, args.satellite, override=args.override,
                             link_port=args.port)
    node.log(f"may: {', '.join(a for a in sorted(ACTIONS) if node.permitted(a)) or 'nothing'}")
    try:
        node.connect()
    except ConnectionError as exc:
        node.log(f"no link: {exc}")
        return 1

    try:
        if args.action:
            try:
                node.do(args.action, timeout=args.timeout)
            except Refused:
                return 2
            return 0

        node.log("console ready. Commands: " + ", ".join(sorted(ACTIONS)) + ", quit")
        for line in sys.stdin:
            cmd = line.strip()
            if cmd in ("quit", "exit", ""):
                break
            if cmd not in ACTIONS:
                node.log(f"unknown command {cmd!r}")
                continue
            try:
                node.do(cmd, timeout=args.timeout)
            except Refused:
                pass
    finally:
        node.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
