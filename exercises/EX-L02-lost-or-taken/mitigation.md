# EX-L02 — Mitigation

## The fix

`CUBERANGE_OBC_REPORT_STORE=1`. The OBC keeps the last eight reports, verbatim, and sends one
again when the ground asks for it by counter — a subset of ECSS-E-ST-70-41C service 15, named as a
subset in the firmware rather than described as the service.

**Verbatim is the whole point.** The resent report is the same octets, so its trailer still
verifies and its counter is still the one the ground noticed missing. Rebuilding it would produce
a different packet answering a question about a specific one; re-signing it would need a new
sequence number, which is the number the ground is trying to reconcile.

The request goes through the station's ordinary `_send`, so whatever authentication the station is
configured for applies to it. That is not tidiness: **a resend request a spacecraft honours without
checking is a way to make it transmit on demand**, which is a thing an attacker would enjoy having
on a power-limited spacecraft.

## What it costs

- **Eight reports of RAM**, at 96 octets each. Measured: the build is 52960 octets of flash and
  33712 of RAM, against 2 MB and 512 KB. On a real small satellite that is a real conversation and
  the number is small enough to have it.
- **A depth the ground has to know.** Eight is a buffer, not an archive. An operator who asks for
  something older gets nothing — and that is indistinguishable from suppression, which is the
  mitigation's own failure mode reproduced at the edge of its range. A mission would publish the
  depth and the ground would not ask beyond it.
- **Transmissions an attacker can provoke.** Every resend is airtime. Authenticating the request
  bounds who can spend it; it does not make it free.

## What it does not solve

- **Suppression of the resends too.** Which is exactly what makes the attack visible here — the
  attacker who takes report 5 every time is the one you find. An attacker who takes it once and
  then stops looks like the link. This mitigation does not identify attackers; it separates
  *persistent* denial from *random* loss, and a patient attacker is deliberately not caught.
- **Total denial.** If nothing arrives, there are no gaps and nothing to ask about. EX-D02 said
  this and it is still true: a silent pass is not a gap, and noticing it needs a schedule.
- **What was in a report you cannot get back.** Older than eight, and the answer is gone.
- **A compromised spacecraft.** It resends whatever it likes, signed with the key it holds.

## The thing worth carrying out of this

Seven exercises, and this is the one about what a control is standing in for.

EX-X01: a defence is attached to a path, not to an asset.
EX-G04: a control that cannot report is a control the ground cannot use.
EX-S01: the strongest control in the system is still attached to a path.
EX-S02: so bind it to the thing you actually care about.
EX-D01: and then check that you did it in both directions.
EX-D02: and check what your detection is standing on.
EX-L02: **and check that it can tell your adversary from your weather.**

A detector that fires on both is not wrong. It has been moved from a room where one of them
existed into a room where both do, and nobody re-read it on the way.
