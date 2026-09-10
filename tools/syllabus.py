#!/usr/bin/env python3
"""The teaching order, derived from the exercises rather than written beside them.

Every exercise's README front matter declares `prerequisite:`, and until now nothing read it.
The order existed, correctly, in six files that a student has to open one at a time to discover -
which is the same shape as a fact that is true and unreachable.

Printing it is half. The other half is tests/pytest/test_syllabus.py, which fails on a
prerequisite naming an exercise that does not exist, on a cycle, and on an exercise no path
reaches. A curriculum with a dangling edge sends a student to look for a lesson that was never
written; a cycle sends them in one.

    python3 tools/syllabus.py            # the order, with durations
    python3 tools/syllabus.py --dot      # the graph, for a slide
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXERCISES = REPO / "exercises"

FIELDS = ("id", "title", "layer", "difficulty", "duration", "prerequisite")


def front_matter(readme: Path) -> dict:
    """The YAML-ish header, parsed with a regex on purpose.

    A real YAML parse would also swallow the `ttp:` block, whose whole point is that it is
    deliberately empty and must stay visible; and this file must run with no dependencies so an
    instructor can read the order on a machine that has not installed anything.
    """
    text = readme.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError(f"{readme} has no front matter")
    out = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if km and km.group(1) in FIELDS:
            out[km.group(1)] = km.group(2).strip()
    return out


def exercises() -> dict:
    found = {}
    for d in sorted(EXERCISES.iterdir()):
        readme = d / "README.md"
        if not d.is_dir() or not readme.is_file():
            continue
        fm = front_matter(readme)
        fm["dir"] = d.name
        found[fm["id"]] = fm
    return found


def order(found: dict) -> list:
    """Breadth-first from the exercises with no prerequisite.

    Breadth-first rather than depth-first because the graph forks: EX-B01 unlocks two, and a
    student finishing it should be told about both rather than led down one branch.
    """
    ready = [i for i, fm in found.items() if not fm.get("prerequisite")]
    seen, out = set(), []
    frontier = sorted(ready)
    while frontier:
        nxt = []
        for i in frontier:
            if i in seen:
                continue
            seen.add(i)
            out.append(i)
            nxt += sorted(j for j, fm in found.items() if fm.get("prerequisite") == i)
        frontier = nxt
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dot", action="store_true", help="emit graphviz instead")
    args = ap.parse_args()

    found = exercises()
    seq = order(found)

    if args.dot:
        print("digraph syllabus {")
        print('  rankdir=LR; node [shape=box, fontname="sans"];')
        for i, fm in sorted(found.items()):
            print(f'  "{i}" [label="{i}\\n{fm.get("title","")}"];')
        for i, fm in sorted(found.items()):
            if fm.get("prerequisite"):
                print(f'  "{fm["prerequisite"]}" -> "{i}";')
        print("}")
        return 0

    unreached = sorted(set(found) - set(seq))
    print("CubeRange — the order the exercises were written to be taken in")
    print()
    for n, i in enumerate(seq, 1):
        fm = found[i]
        pre = fm.get("prerequisite") or "—"
        print(f"  {n}. {i}  {fm.get('title','')}")
        print(f"       {fm.get('layer','?'):16s} {fm.get('difficulty','?'):14s} "
              f"{fm.get('duration','?'):12s} after: {pre}")
        print(f"       make exercise EX={fm['dir']}")
    print()
    print("Each mitigation.md ends with what the fix does NOT solve, and the next exercise "
          "generally attacks one of those limits. Read them in order and the chain is the point.")
    if unreached:
        print(f"\nUNREACHABLE, which is a defect: {unreached}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
