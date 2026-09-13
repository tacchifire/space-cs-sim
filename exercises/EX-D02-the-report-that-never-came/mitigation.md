# EX-D02 — Mitigation

## The fix

The ground station reads the report counter, on telemetry it has already authenticated:

```python
GroundStation(link, require_signed_tm=key)      # both, or neither is worth having
...
station.counter_gaps      # [(last, current, missing), ...]
station.reports_missing   # how many the operator can tell they never received
```

`_advance` is four lines and one of them is about wrapping: a 16-bit counter rolls over, and a
station that reported 65534 missing reports once a day would be switched off by the second day.
A backwards or very large step is treated as a wrap or a reordering rather than a loss - this
station claims to detect gaps, not to reconstruct history.

**The order of the two checks is the whole mitigation.** Authenticate, then count. The other order
counts octets an attacker wrote.

## What it costs

- **Nothing on the spacecraft.** The counter was already there - PUS puts a message counter in
  every TM secondary header, and the OBC has always incremented it. EX-D02's fix is entirely in
  the ground station, which is the unusual shape here and worth noticing: the evidence existed
  and nobody read it.
- **False positives on a real link.** A lossy channel drops frames, and a real operator would see
  gaps that no attacker caused. A mission would treat a gap as a prompt to ask rather than an
  alarm. This range has no bit errors (SAFE_USE.md lists what the channel does not model), so
  every gap here is somebody's decision.

## What it does not solve

- **What was in the missing report.** A gap says the spacecraft spoke and you did not hear it. It
  does not say whether that was a refusal, a success, or a housekeeping frame. An operator who
  sees a gap during a power command knows to ask; they do not know the answer.
- **Suppression of everything.** An attacker who takes the whole downlink produces no gaps at all,
  because there is no later report to compare against. Total denial is indistinguishable from a
  spacecraft that has nothing to say - which is EX-G04's finding, one more time, and the reason
  a mission has a pass schedule and notices a silent one.
- **The first report.** There is nothing before it to count from. A station that has just attached
  cannot tell whether it joined at the start.
- **A compromised spacecraft.** The counter is signed by the key the spacecraft holds. A
  spacecraft that has been taken over signs whatever it likes, including a counter with no gaps.

## The thing worth carrying out of this

Six exercises, and this is the one that is not about an attack.

EX-X01: a defence is attached to a path, not to an asset.
EX-G04: a control that cannot report is a control the ground cannot use.
EX-S01: the strongest control in the system is still attached to a path.
EX-S02: so bind it to the thing you actually care about.
EX-D01: and then check that you did it in both directions.
EX-D02: **and check what your detection is standing on.**

The counter was in every report from the first day of this range. It became useful the day the
reports were authenticated, and not before - and in between it would have looked like a control
while being a place to look at nothing.
