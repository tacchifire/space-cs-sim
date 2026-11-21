# Common node board

One board design, instantiated four times. The role — COMM, OBC, EPS, ADCS —
is selected by firmware. This is the first board the project designs for
itself, and the unit of the phase 3 bring-up: one board, then two talking,
then four.

## Skeleton specification

| Item | Direction |
| --- | --- |
| MCU | STM32H753, the same part the Renode range runs |
| Bus interface | CAN classic transceiver onto the backplane trunk |
| Debug | SWD header, externally accessible with the node installed |
| Console | USB-C console, independent of the node's own firmware |
| Power | Fed from the backplane; per-node switch and current measurement |
| Reset and boot | NRST and BOOT brought out for external control |
| Role I/O | Role-specific I/O broken out to a defined connector |

## Design requirements

- The board must be returnable to a known state from outside: power cut,
  NRST, BOOT selection, and reflashing over SWD must all work without the
  running firmware's cooperation.
- The CAN transceiver must support a normal, acknowledging mode; a
  listen-only node cannot take part in the exercises that need ACK.
- Test points for power, CANH/CANL, SWD, and the console UART.
- The schematic is drawn as hierarchical sheets, so a later consolidated
  single-board variant can reuse it instead of redrawing it.

## Open points

- The pin map is not frozen. See `docs/testbed/pinmap.md` for what the
  current firmware already fixes and what is still undecided.
- Connector choice for role I/O is undecided.

## Status

- Not designed. KiCad sources land here in phase 4.
