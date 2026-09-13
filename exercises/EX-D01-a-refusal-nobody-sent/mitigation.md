# EX-D01 — Mitigation

## The fix is in two places, and only one of them is the spacecraft

`CUBERANGE_OBC_SIGN_REPORTS=1` puts the same trailer on a report that EX-S02 put on a telecommand:

```
primary(6) | PUS TM secondary(7) | time(4) | application data | SEQ(4) | MAC(16)
```

**And the ground station has to require it.** `GroundStation(..., require_signed_tm=key)`. A
station that verified a trailer when one was present and accepted the packet when it was absent
would be defeated by an attacker who simply does not attach one — which is not a subtle attack,
and is the shape most optional security ends up having. Measured with the flag on and the station
not requiring: the forgery still lands.

That split matters more than it looks. **COMM is not the fix and cannot be.** Watch what it does
on the mitigated build:

```
COMM console:      downlink 22 octets from node 13
operator console:  (nothing believed; 1 rejected as unauthenticated)
```

The forged frame still goes down the link. COMM frames whatever arrives on its PUS port because
that is what a radio does, and the thing that can tell a real report from a forged one is the
thing that holds the key and cares about the answer. The control belongs at the receiver.

## The cheaper partial, and what it leaves

COMM could accept downlink only from its own OBC — a topology check, EX-X01's shape:

```c
if (csp_conn_src(conn) != OBC_ADDR) { /* not ours to transmit */ }
```

That stops this attack, costs nothing per frame, and is worth doing. It does **not** stop anybody
who can transmit on the downlink itself, which on a real link is anyone with a dish and the
spacecraft's SCID. The trailer stops both. Both are cheap; only one is sufficient; the write-up
says which is which rather than letting the cheap one look finished.

## What it does not solve

- **A peer with the key.** Signing reports uses the same one key. A compromised spacecraft can
  still forge one from any other — and `keys.py` says at length why one written-down key is the
  honest shape for a teaching range, not a defensible one for a mission.
- **Suppression.** Authentication makes a forged report detectable. It does nothing about a real
  report that never arrives, which is EX-G04's problem and is still there: the ground cannot tell
  a suppressed refusal from a command that was accepted. A signed report proves what the
  spacecraft said; it cannot prove the spacecraft said nothing.
- **Traffic analysis and timing.** An attacker who cannot forge a report can still watch when one
  appears.
- **The counter.** Report sequence numbers are per-spacecraft and monotonic, and the ground does
  not currently check them for gaps. A gap is the signal that a report was suppressed - the one
  thing above that authentication could have helped with - and this range does not read it. Named
  rather than hinted at.

## The thing worth carrying out of this

Five exercises, one sentence, and this is the direction nobody looks.

EX-X01: a defence is attached to a path, not to an asset.
EX-G04: a control that cannot report is a control the ground cannot use.
EX-S01: the strongest control in the system is still attached to a path.
EX-S02: so bind it to the thing you actually care about.
EX-D01: **and then check that you did it in both directions.**

Every one of those was found by asking what the previous fix did not cover. The reporting channel
EX-G04 built was a fix; it became an attack surface the moment the ground learned to trust it, and
it stayed one through two rounds of cryptography aimed at the other direction.
