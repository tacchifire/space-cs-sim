---
id: EX-U03
title: Twenty refusals nobody counted
layer: uplink
difficulty: advanced
duration: 30-45 min
prerequisite: EX-U02
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-U03 — Twenty refusals nobody counted

## The situation

EX-U02 ended by naming the blind spot on the other side of its own detector:

> **Count what COMM refused.** The counters are on the OBC, which is *behind* the link-layer
> authentication. A forged frame, a replayed frame, a frame with a bad MAC — none of them reach
> this counter, so an attacker who does **not** have the key is invisible to it while hammering
> the uplink. That is the opposite blind spot from the one this exercise is about and it is the
> more common attacker.

This attacker has no key. Ten replays and ten forgeries go up.

**Every control refuses them, correctly and completely.** The SDLS MAC does not verify on a
forged frame. The anti-replay counter refuses a recording. Not one payload reaches the on-board
computer, nothing changes on board, nothing is at risk for a moment. This is what a defence
working perfectly looks like.

Forging needs no key, and that is worth being clear about: the frame's FECF is a **CRC**, not a
MAC. Anybody can change an octet and recompute it. That is what a checksum is for and exactly what
it is not for, and it is why there is a MAC underneath.

## Your objective

The spacecraft is attacked for twenty frames while you watch. Say so.

```bash
make exercise EX=EX-U03-twenty-refusals-nobody-counted          # a shell inside the range
python3 exercises/EX-U03-twenty-refusals-nobody-counted/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-U03-twenty-refusals-nobody-counted RUN='python3 exercises/EX-U03-twenty-refusals-nobody-counted/solve.py --no-attacker'
```

## What happened

```text
radio reports   attacker   thrown   acked   gaps   unexplained   received   refused
yes             yes          20      4/4     none       0           23        20
yes             no            0      4/4     none       0            3         0
no              yes          20      4/4     none       0            -    cannot tell
no              no            0      4/4     none       0            -    cannot tell
```

**Rows 3 and 4 differ in nothing an operator can see.** That is what "a successful defence is
indistinguishable from no attack" means once it is measured instead of asserted. Twenty times the
console on board printed a refusal, and no part of it left the spacecraft.

**Look at the `unexplained` column.** Zero in every row, including the rows with an attacker in
them — and that is EX-U02's detector, built one exercise ago *specifically to notice attackers*.
It counts what the **computer** heard, and the radio is in front of the computer. **The control
that stopped this attack is the same thing that hid it from the detector built to see attacks.**

**Twenty of twenty-three, not twenty.** No station on the ground knows what this link's frame rate
should be, so a refusal count alone is not a sentence. A ratio is.

## What to conclude

**A control that succeeds silently teaches the attacker more than it teaches you.** They learn the
MAC is real and the replay window is tight, and they learn it for free, because nothing on your
side recorded that they tried. You learn nothing: not that you are a target, not when, not from
where, not that it is worth correlating with anything else. Next time they come back with
something that works and it will be the first thing you ever hear about them.

**Detection sits at a layer, and the layer that stops an attack is usually not the layer that can
see it.** EX-U02's counter is on the OBC because that is where telecommands are counted. This
attack dies one node earlier. Neither counter is wrong; **each is blind to exactly the attacker the
other one sees**, and a spacecraft needs both because an operator cannot know in advance which kind
they have.

**And this is EX-G04 again, one layer down.** *A control that cannot report is a control the ground
cannot use.* EX-G04 established that about a refused telecommand and the range fixed it with PUS
1,2. Nobody asked the same question about the refusals happening below PUS, in the radio, where
most of them are.

`mitigation.md` is the counters, what a ratio can and cannot say, and why "attacked" and "noisy"
are still one number here.
