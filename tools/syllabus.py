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
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXERCISES = REPO / "exercises"

# The front-matter parser itself lives in exactly one place, src/cuberange/exercises.py -
# this tool and the GUI catalog both delegate to it. That module uses only the standard
# library, so this file still runs with no third-party dependencies on a machine that has
# not installed anything; the sys.path bootstrap below is what lets a bare
# `python3 tools/syllabus.py` find it without an install step.
sys.path.insert(0, str(REPO / "src"))

from cuberange import exercises as _exercises_module  # noqa: E402

FIELDS = _exercises_module.FIELDS


def front_matter(readme: Path) -> dict:
    """The YAML-ish header of one README (delegated to cuberange.exercises).

    Kept under this name because existing callers - the flatsat console catalog and the
    tests - import it from here.
    """
    return _exercises_module.parse_front_matter(readme)


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
