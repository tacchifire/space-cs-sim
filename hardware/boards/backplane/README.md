# Backplane

The board the four nodes plug into. It carries power from the single EPS
entry point to each node, and the CAN trunk between them.

## Skeleton specification

| Item | Direction |
| --- | --- |
| Power entry | USB-C 5 V at the EPS position, single point |
| Distribution | Per-node feed, each with its own switch and current measurement |
| Protection | Current limiting and reverse-polarity protection at the entry |
| CAN trunk | One differential pair routed past all four node positions |
| Termination | At the two ends of the trunk only, switchable |
| Attack span | One trunk span is a connector break, where the tap board plugs in |

## Design requirements

- A node can be powered down, measured, and removed without disturbing the
  power or the bus of the others.
- Termination exists only at the trunk ends. A node must not be able to add
  a third termination by configuration accident; broken termination is a
  fault-injection feature of the tap board, not of the backplane.
- The power entry is the only place external power enters the bench in v1.
  There is no battery.
- Test points for the entry voltage, each node's feed, and CANH/CANL.

## Open points

- Node connector and slot geometry are undecided.
- The current-measurement method (shunt and amplifier, or integrated
  monitor) is undecided; phase 1 fixes the accuracy the exercises need.

## Status

- Not designed. KiCad sources land here in phase 4.
