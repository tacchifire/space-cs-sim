# Testbed documents

The specification side of the physical testbed whose design assets live
under `hardware/`. This is where phase 1 of the testbed roadmap — the
specification freeze — is written down, before any board is designed.

## Documents

| Document | What it holds |
| --- | --- |
| `hw-compat-table.md` | Every exercise, classified for the physical bench: runs as-is, needs a different injection method, or stays on Renode — and the hardware features each one needs |
| `pinmap.md` | What the current firmware already fixes about the STM32H753 pin map, and what is still undecided |

## What the freeze must settle

- The pin map, proven against the Nucleo board the firmware already runs on.
- Power, clock, and CAN settings, fixed as numbers rather than intentions.
- The exercise classification in `hw-compat-table.md`, reviewed.
- The acceptance thresholds below, fixed before the hardware exists, so the
  bench is judged against numbers written in advance.

## Acceptance thresholds (draft)

The values are undecided; the criteria are not. Each is fixed in phase 1
with its measurement method.

| Criterion | Value | Measurement |
| --- | --- | --- |
| Round-trip latency of a PUS exchange over the wired trunk | undecided | undecided |
| Frame loss over a continuous run | undecided | undecided |
| Continuous operation without intervention | undecided | undecided |
| Time to return all nodes to the initial state after an exercise | undecided | undecided |

## Status

- Phase 1 is in preparation. Both documents in the table above are drafts.
- Nothing here is a claim that hardware exists or works.
