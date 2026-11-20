# Attack segment (tap board)

The one span of the CAN trunk that is meant to be attacked. It plugs into the
backplane's connector break and gives physical-layer exercises a defined,
inspectable, and removable place to happen: when the tap board is out and a
plain through-board is in, the bench is a clean bench again.

## Skeleton specification

| Item | Direction |
| --- | --- |
| Form | Pluggable segment in one trunk span, pin-compatible with a plain through-board |
| Passive tap | High-impedance listen point for an external analyser or CAN adapter |
| Interposition | A break point where a device can be inserted between the two halves of the span |
| Fault injection | Switches for the named faults: termination removed, termination doubled, CANH/CANL short, line swap |
| Visibility | Every fault is selected by a labelled switch; no fault is produced by loose wiring |

## Design requirements

- Removing the tap board and fitting the through-board restores the trunk
  to its specified state, including termination.
- The passive tap must not load the bus enough to change the behaviour
  being observed; its input impedance is part of the specification freeze.
- Fault-injection switches are the only sanctioned way to damage the bus.
  The bench is never faulted by cutting, bending, or unplugging mid-run.

## Open points

- Whether interposition is a passive break only, or carries an active
  device footprint, is undecided.
- The analyser connector is undecided.

## Status

- Not designed. KiCad sources land here in phase 4.
