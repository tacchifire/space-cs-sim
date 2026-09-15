# EX-U03 — the radio's own account, and what one number cannot say

## What the mitigated build does

`CUBERANGE_COMM_LINK_STATS=1` adds two sixteen-bit counters to COMM:

```c
static uint16_t link_frames_rx;        /* frames that arrived at all */
static uint16_t link_frames_refused;   /* frames this radio threw away */
```

`rx` is incremented at the top of `on_tc_frame`, before any check. `refused` is incremented at
every site that refuses a frame and returns: the SDLS MAC failure, the SDLS anti-replay refusal,
the plain anti-replay refusal on both its per-VC and per-link paths, the unreadable virtual
channel, and the FECF/length failure. Six sites, and a seventh added later that forgets to count
is a counter that quietly understates — which is why the increments sit *next to the `return`*
rather than in one place at the top.

A thread sends the pair to the OBC every two seconds over CSP port 12, and the OBC carries them in
the housekeeping beacon. **The radio does not grow a telemetry path of its own.** A real mission
would give the transponder an APID; a real mission would also have the computer aggregate
subsystem housekeeping, and the second one needed no new plumbing here. The cost of that choice is
in the limits below.

The OBC accepts link statistics **only from COMM's CSP address**. A node that took them from
anywhere would let anything on the internal bus write the spacecraft's own account of what the
radio saw, which is EX-B01's premise pointed at telemetry instead of at power.

The beacon is four octets longer *once the radio has actually reported*, and never padded with
zeros before that. **The length is the feature test on the ground**: four octets is a spacecraft
that counts nothing, eight is one that counts telecommands, twelve is one whose radio counts what
it refused. Padding to twelve would tell a station "no frames were refused" about a spacecraft
that has no idea, which is the one answer worse than saying nothing.

## What it costs

Four octets per beacon, two `uint16_t`, one thread with a 2 KB stack, and a four-octet CSP packet
every two seconds on a bus that is otherwise nearly idle.

The thread is the part to be careful with, and this range has a scar about it: `K_THREAD_DEFINE`
with `K_TICKS_FOREVER` creates a thread that **does not run until something starts it**. The first
version of this build counted all twenty refusals correctly and reported none of them — a silent
half-feature that looked exactly like the vulnerable half. Found by measuring; nothing about
reading the code would have shown it.

## What it still cannot do

- **Tell an adversary from bad weather.** This is the big one. A corrupted frame and a forged one
  are **one number** here. In the SDLS build the FECF check lives inside `sdls_verify`, so the
  refusal *reason* is gone by the time anything counts it — and the reason is the whole
  distinction: a bad FECF is noise, a valid FECF with a bad MAC is somebody. A mission would carry
  a small histogram of refusal reasons, four or five counters instead of one, for eight more
  octets. This range carries one, and an operator reading it on a genuinely noisy link would
  investigate the ionosphere.
- **Say who, or from where.** It is a count. No frequency, no arrival time, no Doppler, no
  direction. A real mission correlates refusal bursts with a pointing schedule and gets a location
  out of it; here there is nothing to correlate with.
- **Report while it is happening.** The counts ride the beacon, so they arrive when the beacon
  does — and the beacon reaches the operator only when the operator is in view. An attack during a
  blackout is visible on the next pass and not before. That is the same property EX-U02's counter
  has and for once it is a feature: a counter is a witness that keeps.
- **Survive the radio being the thing that is compromised.** Everything above is the radio's own
  account of the radio's own behaviour. A COMM that has been taken reports whatever it likes, and
  nothing on board cross-checks it. The OBC's telecommand counters are independent of COMM's and
  that is worth something — `unexplained_commands` reading nonzero while COMM claims to have
  refused everything is a contradiction an operator could act on — but nothing in this range makes
  that comparison for them.
- **Count what never got as far as a frame.** Carrier jamming, a swamped receiver, bits that never
  synchronised: the deframer is upstream of all of this, so an attack that stops frames forming
  registers as a *quiet link*. `rx` going to zero is the only symptom, and EX-U01 is the exercise
  about how a quiet link reads from the ground.

## The thing worth carrying out of this

EX-U01: check the direction nobody built anything for.
EX-U02: and check whether anything you built can tell your traffic from someone else's.
EX-U03: **and check whether the controls that already work can say that they did.**

The MAC on this spacecraft refused ten forged frames. The anti-replay counter refused ten
recordings. Both were correct, both were fast, and both wrote their result to a console that has
never left the spacecraft in the entire history of this range. **A defence that cannot report is
not half a defence — it is a whole defence and no detection at all**, and the difference only
shows up in what you knew the week before the attack that worked.
