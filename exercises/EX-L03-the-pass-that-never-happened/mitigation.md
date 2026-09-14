# EX-L03 — Mitigation

## The fix, in two places again

**On the spacecraft:** `CUBERANGE_OBC_BEACON=1`. An unsolicited housekeeping report on a timer —
a PUS 3,25 subset carrying an uptime, named as a subset in the firmware rather than described as
the service. It goes through the same senders as every other report, so it is signed when the
build signs reports and stored when the build stores them. **The beacon is not a special channel
with its own rules**; it is telemetry that happens to be unsolicited, and an attacker who can take
a report can take a beacon.

**On the ground:** `cuberange.gs.passes` — a window, an expectation, and a verdict of `ok`,
`quiet` or `silent`. The three are different findings and the write-up says which is worth waking
somebody for.

Neither half works alone. The beacon without a schedule is telemetry nobody is counting; the
schedule without a beacon fires on every pass.

## The clock problem, which is the part worth carrying

A rate-based expectation — "a beacon every two seconds" — is a statement in the **spacecraft's**
clock, checked against the **operator's**. This range runs them at different speeds: the execution
profiles let emulated time advance as fast as the host allows (design 4.3, rule G1), so a 2000 ms
beacon period was measured arriving every ~400 ms of wall clock — 52 transmissions in a window
whose rate predicted 10.

So the window here asks `expect_at_least`, which is the question that survives the disagreement.
A real mission reconciles the two clocks deliberately; that is what time correlation is for, and
this range does not implement it. **The failure mode is quiet and worth naming: a rate-based alarm
calibrated on one clock and evaluated on another is wrong by the ratio between them, and the ratio
is not constant.**

## What it costs

- **Airtime and power, continuously.** A beacon transmits whether anyone is listening or not.
  That is the trade every real mission makes and it is not free.
- **A standing signal for an adversary.** A spacecraft that beacons is a spacecraft that announces
  itself. This range has nothing to say about RF geolocation and does not model it.
- **A stack.** The beacon thread signs, so it carries a 424-octet `mbedtls_gcm_context`. The
  1024-octet default sent exactly one beacon and stopped — W47's shape, for the third time, which
  is why the number is now a named constant rather than a third literal.

## What it does not solve

- **Why it was silent.** Denial, a dead spacecraft, a wrong prediction and an antenna pointing
  elsewhere all look identical from here.
- **A partial pass.** An attacker who denies most of a window leaves enough for `ok`, and the
  `quiet` verdict that would catch it needs the rate this range's clocks cannot agree on.
- **A patient attacker.** One denied pass in twenty looks like weather, exactly as one taken
  report did in EX-L02.
- **Anything about the uplink.** The schedule watches what arrives. A spacecraft that hears
  nothing has no way to say so.

## The thing worth carrying out of this

Eight exercises, and the last three are one argument.

EX-D02: check what your detection is standing on. (Its input was forgeable — it failed open.)
EX-L02: check that it can tell your adversary from your weather.
EX-L03: **and check that the thing it is watching for actually happens.** (Its input did not
exist — it failed closed.)

A detector that fires on everything and a detector that fires on nothing are the same defect
wearing different clothes, and both get switched off by the same operator on the same afternoon.
