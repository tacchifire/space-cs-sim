# EX-S01 — Mitigation

## The fix that exists

`CUBERANGE_OBC_CROSSLINK_ORIGIN=1` — EX-X01's, unchanged, and it still works:

```
OBC: REJECTED a packet claiming source 66 that arrived from node 13
OBC: PUS 1,2 acceptance failure reported to source 66 (code 4)
```

A topology check, not authentication. It asks where the packet entered, which the attacker does
not choose, instead of who sent it, which the attacker writes. EX-X01's write-up says at length
what that does and does not buy, and deploying SDLS on the uplink changed none of it.

That is the point worth sitting with. The weak control on the uncovered path is **exactly as
necessary** after the strong control went in on the covered one. What changes is how likely
somebody is to remember it.

## The fix that would be right, and what it costs

Extend SDLS to the crosslink. A second Security Association for peer traffic, its own key, its own
anti-replay counter, and a MAC over something — and the "over something" is where the work is,
because the crosslink carries CSP packets rather than transfer frames. CCSDS 355.0-B-2 has nothing
to say about a CSP packet; there is no security header defined for one.

So this would be one of:

- **SDLS on a transfer frame over the crosslink.** Wrap peer traffic in TC frames and run the same
  code. Honest, and it makes the crosslink a second space link, which is roughly what a
  cross-support radio is.
- **Authentication at the CSP layer.** libcsp has an HMAC option (`CSP_USE_HMAC`); this range
  compiles it out, and CryptoLib's own TC path drops HMAC-flagged frames when it is not built in.
  A different protocol's answer to the same question.
- **Authentication at the application layer.** The PUS packet carries its own MAC. Then it does
  not matter which link it arrived on - which is the property the other two do not have.

The third is the one that actually answers this exercise, and it is the most work. Not implemented
here, named rather than hinted at.

## What the fix does not solve

- **A peer with the key.** Any of the three above gives the constellation a shared secret; a
  compromised spacecraft holds it. `keys.py` says at length why one written-down key is the honest
  shape for this range, and the sentence that matters is the same one: a fixed shared secret is a
  thing you HAVE rather than a thing you prove.
- **Our own COMM.** The origin check believes COMM because something has to be believed, and a MAC
  over the crosslink would be verified by COMM too.
- **What an authenticated station is allowed to ask for.** EX-G02 is untouched by all of this.
  Authentication answers who; authorisation answers what; SDLS is entirely on the first question.
  A range that deployed crypto and stopped would still have EX-G02.
- **The anti-replay window.** One Security Association means one counter, and EX-G03's problem
  reappears: two ground stations on one SA lock each other out exactly as they did on one virtual
  channel. SDLS has somewhere to put that fix - anti-replay state belongs to the SA, so two
  stations get two SAs - and this range has not put it there. Written down rather than discovered.

## The thing worth carrying out of this

Three exercises now end at the same sentence from three directions.

EX-X01: a defence is attached to a path, not to an asset.
EX-G04: a control that cannot report is a control the ground cannot use.
EX-S01: **the strongest control in the system is still attached to a path**, and its strength is
what makes that easy to forget.

The uplink MAC is not a weaker answer than it looked. It is exactly as strong as advertised, over
exactly the frames it covers, and the sentence that gets written down afterwards is shorter than
that.
