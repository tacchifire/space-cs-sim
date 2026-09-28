# EX-S07 — a ledger on the ground, and the question a log cannot ask

## What the mitigated build does

There is no mitigated *build*. Both runs load EX-S06's hardened COMM (`CUBERANGE_COMM_SA_MGMT_HISTORY`
on), and the spacecraft emits the identical SA-management log either way. This is a **host-side**
pair, like EX-G01: the difference is one field on the ground station.

```python
# GroundStation, EX-S07
self.sa_directives_sent = []          # every STOP_SA this station framed, (retired SPI, SPI framed under)
self.owned_spis = frozenset(...)      # the SAs this station's owner holds — or None, "I cannot tell"

@property
def unexplained_sa_retirements(self):
    if self.owned_spis is None:
        return None                   # not [] — "I cannot judge" is not "nothing is wrong"
    unmatched = Counter(self.sa_directives_sent)
    out = []
    for entry in self.sa_retire_log:          # (retired SPI, retiring SPI), from the beacon
        if entry[1] not in self.owned_spis:
            continue                  # a partner's SA — EX-S05/S06 attribute it; not this question
        if unmatched[entry] > 0:
            unmatched[entry] -= 1     # one directive I sent explains one log entry
        else:
            out.append(entry)         # in my name, and I have no record of sending it
    return out
```

The reconciling station is the whole mitigation. It keeps its own ledger of the STOP_SA directives it
sent, and for every log entry whose **retiring** SPI is one of its own, it cancels one against a
directive it remembers sending. What is left is a retirement *in the operator's name that the
operator did not order* — the signature of a key the operator holds being used by someone who is not
the operator. It is a **multiset in log order**: sending one STOP_SA(9) explains exactly one
`(9, 10)` entry, so a copy of the key that produces a second one is not absorbed.

**Only entries naming an SA the station owns are judged.** A partner retiring SPI 9 under SPI 11 is
attribution EX-S05 and EX-S06 already put on the downlink; charging it to the operator would be a
detector that cried at every retirement. The detector answers exactly one question: *was a key I hold
used by someone who is not me.*

**A directive is not a telecommand, and the counter proves it.** `send_sa_stop` records into
`sa_directives_sent`, never `commands_sent` — because COMM acts on the directive and the OBC never
sees it, so EX-U02's `unexplained_commands` never moves for it. Counting it as a telecommand made the
operator's own rotation read `unexplained_commands = −1` (a lost command, EX-U01's symptom, pointing
at the wrong incident); that is now measured as `0` in the feature test.

## What this does not solve

**It detects; it does not prevent.** Nothing here stops the retirement — EX-S04's owner check, on its
own build, *authorises* it, because a copy of SPI 10's key satisfies the `owner` equality exactly as
the real key does (the exercise measures this: the partner's SPI 11 is refused, the copied SPI 10 is
not, and SPI 9 goes dark). The ledger only lets the operator notice, after the fact, that a
retirement in its name was one it never sent. The prevention this asks for is **key management** — an
SA bound to a holder, key rotation on suspicion of compromise — which this range names and does not
build. EX-U02 first stated that a stolen key is a premise no control here covers; EX-S07 does not lift
that, it reads its consequence off the downlink.

**Detection needs a log to reconcile against, and the log is EX-S06's — bounded, and only if
reported.** A build that does not report the SA-management log (EX-S05's snapshot, or a COMM with
reporting off) leaves the reconciling station with nothing: `unexplained_sa_retirements` reads the
empty list, the same answer a clean run gives, because the input does not exist. Run 5 measures this
on EX-S04's build. And where the log *is* reported it is EX-S06's bounded ring, so a retirement that
scrolled off the oldest end is one the ledger can no longer match — the bounded-log limit EX-S06
named, inherited here.

**Every holder of the key must reconcile, or the gap moves.** The ledger is per station. Two
legitimate operator stations sharing SPI 10 (EX-G03's two-station problem) each know only their own
directives, so a retirement one of them sent reads as unexplained at the other. A complete detector
needs the ledger to cover every station holding the key — which is the same shape as EX-G03's single
replay counter, one layer up in key management.

## What to take from here

EX-S06: keep the sequence of who retired which SA, because the last-state snapshot is overwritten.
EX-S07: **and reconcile it against your own record of what you did, because attribution names the key,
and a copy of your key is you to everyone except the one who remembers what they sent.**

Authentication tells you a key verified. It cannot tell you the key was yours to use — that is not a
property of the frame, and no control on the spacecraft can recover it, because the spacecraft only
ever held the key. The answer is on the ground, in the operator's own account of what it did, exactly
where EX-G01 found the authority a spacecraft-side control could not supply. The witness to a stolen
retirement is not on the satellite; it is the ledger the operator keeps of the retirements it ordered.
