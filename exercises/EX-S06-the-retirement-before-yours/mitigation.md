# EX-S06 — a log where there was a snapshot, and the history it does not keep

## What the mitigated build does

Both halves of this pair hold **authority OFF** and **report ON** — a partner's retirement lands,
and COMM records who retired which SA, exactly as EX-S05 left it. The single flag,
`CUBERANGE_COMM_SA_MGMT_HISTORY`, decides only how many of the recorded events leave the spacecraft.

```c
/* handle_sa_directive, on every retirement */
sa_log_append(target_spi, sa_table[requester].spi);   /* the requester SPI the MAC PROVED */

/* send_link_stats */
int n     = CUBERANGE_COMM_SA_MGMT_HISTORY ? sa_log_count : 1;              /* how many to send */
int first = CUBERANGE_COMM_SA_MGMT_HISTORY ? 0 : (sa_log_count - 1);        /* from where */
```

COMM keeps a ring of the last `SA_LOG_MAX` (= 4) events in arrival order in **both** builds; the
flag is the transmit depth. OFF sends only `sa_log[count-1]` — byte-for-byte the single snapshot
EX-S05 sent. ON sends the whole ring, oldest first. The OBC carries whatever it is told onto the
housekeeping beacon, and the ground reads it.

**The length is the feature test, end to end.** COMM's link-stats packet is 6 octets with no
retirement and `6 + 4·k` with `k` recorded; the beacon is 14 and `14 + 4·k`. A spacecraft that
retired nothing sends the short form, so an empty log means *nothing happened*, not *the report is
broken* — the same reason EX-S05 refused to pad `SPI 0 retired SPI 0` into a beacon, and EX-U03
refused to pad a zero link count.

**The attribution is authenticated, but not by this flag.** The beacon is signed by EX-D01's control
(`CUBERANGE_OBC_SIGN_REPORTS`) and required by the ground; this rides it. That is deliberate — report
authentication is a control this range already has, and re-implementing it here would put two ideas
in one flag. EX-S06 adds only the *content*: the sequence rather than the last of it. The requester
SPI in each entry is the SA whose MAC verified the directive, not a header field, so "retired by
SPI 11" is a statement about possession.

## What this does not solve

**A history longer than the ring.** The buffer holds the last `SA_LOG_MAX` events, drop-oldest. A
sequence of more than four SA-management events still loses its oldest — the single-value limit
EX-S03's `link_refused_spi` and EX-S05's snapshot both had, now one size larger rather than gone. In
this range only three SAs exist, so four is comfortable headroom; a busier SA-management channel
would overflow it, and a real mission's audit log is bounded by storage the same way. The honest
statement is that a bounded log is a better snapshot, not an unbounded record.

**Attribution by SA, not by the party.** Each entry names the SA whose MAC verified the directive,
not the human or station holding its key. An intruder who stole the operator's SPI 10 key
(EX-U02's premise) and retired SPI 9 with it is logged as "retired by SPI 10" — the operator's own
key, cryptographically indistinguishable from the operator. SA-attribution is exactly as strong as
the key management under it, and this range does not do key management. EX-S05 carried this same
limit and EX-S06 does not lift it.

**Prevention.** This build *reports* a theft; it does not stop one — authority is off on purpose. In
a real deployment EX-S04's authorisation and EX-S06's log run together: the first refuses a partner's
retirement, the second records the SA-management events that DID happen — the operator's own
retirements, and any a bypassed or misconfigured authority let through — in an order an operator can
audit. Each is one idea, and the pair gate proves it, so they are separate exercises.

## What to take from here

EX-S05: put WHO retired WHICH SA where the ground can read it.
EX-S06: **and keep the SEQUENCE, because a control that reports only the latest state is defeated by
a second event — and the second event can be your own routine action, so the defeat looks like
nothing at all.**

Every reporting control this range built reported a *state*: the last refusal's SPI, the last
retirement's attribution, the current counters. A state is a snapshot, and a snapshot is overwritten.
The moment two things happen between two beacons, the first is gone — and an attacker who arranges to
be overwritten by a legitimate action is invisible to a detector that keeps only the latest. The log
is the smallest thing that survives that: the events in the order they arrived, so the one that was
overwritten is still there to read.
