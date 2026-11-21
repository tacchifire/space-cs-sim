# Releases

Manufacturing files are regenerated in CI as a rule; this directory is the
exception. It freezes the manufacturing output of a version at the moment
that version is actually ordered, so the order can be repeated exactly and
audited later.

## Rules

- One subdirectory per ordered version, named for the board and revision.
- A version is frozen here only when an order is placed, never before.
- The frozen set is what was sent to the manufacturer: Gerbers, drill files,
  BOM, and pick-and-place, plus the order's date and manufacturer.

## Status

- Nothing has been ordered. This directory is empty of releases.
