# Hardware: the CubeRange testbed

This directory holds the design assets for the physical testbed: a modular,
STM32H753-based counterpart to the Renode range, in the spirit of a RAMN-style
bench that a third party can re-order and rebuild from what is committed here.

Nothing in this directory has been ordered or built yet. The documents are the
skeleton agreed in the testbed roadmap (phase 1); the KiCad sources land in
phase 4, after the specification freeze in `docs/testbed/`.

## Layout

| Path | What it will hold |
| --- | --- |
| `lib/` | Pinned symbols and footprints, vendor-supplied preferred |
| `boards/node/` | The common node board (one design, four instances) |
| `boards/backplane/` | The backplane: power distribution and the CAN trunk |
| `boards/tap/` | The removable attack segment (tap / interposition harness) |
| `releases/` | Manufacturing files of versions actually ordered, and only those |

## System shape

- One **common node board** design, used four times. The role (COMM, OBC, EPS,
  ADCS) is selected by firmware, not by a different board.
- A **backplane** carries power and the CAN trunk between the nodes.
- One span of the trunk is a **removable attack segment**, so physical-layer
  exercises keep a defined, inspectable place to happen.
- The ground station stays the existing Python ground station, connected
  through a bridge from a PC or a Raspberry Pi.

## Design requirements

- **Return to a known state.** After an exercise, every node must be
  returnable to its initial state with certainty: per-node power switching,
  external control of BOOT and NRST, and a reflash path that does not depend
  on the node's own firmware cooperating.
- **One power entry.** The EPS position takes a single USB-C 5 V input and
  distributes it over the backplane, with current limiting, protection, and
  per-node current measurement. No battery in v1.
- **CAN classic, wired.** The trunk is real CAN classic, matching the
  libcsp-over-CAN assumption of the firmware. Termination lives at the two
  ends of the trunk only, and can be switched.
- **Faults are features, named as such.** Broken termination, shorts, and
  similar damage are provided by explicit fault-injection switches, never by
  loose wiring or by damaging a board.
- **Measurement is part of the bench.** Per-node current measurement and
  accessible test points for power, CAN, SWD, and the console UART.
- **Reproducible by a third party.** KiCad sources, a BOM, and manufacturing
  outputs live in this repository, and the manufacturing outputs are
  regenerable in CI. Only the version actually ordered is frozen, under
  `releases/`.

## Status

- Phase 1 (specification freeze) is in preparation: see `docs/testbed/`.
- No board has been designed, ordered, or built.
- A human circuit-design review is mandatory before any order; ERC/DRC
  passing is not evidence that a board works.
