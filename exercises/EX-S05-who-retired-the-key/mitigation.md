# EX-S05 — the name on the retirement, and the history it does not keep

## What the mitigated build does

Both halves of this pair retire SAs by telecommand with **authority held off** — a partner's
`STOP_SA` lands, on purpose, because a detection exercise needs the thing it detects to happen. The
one flag, `CUBERANGE_COMM_SA_MGMT_REPORT`, decides whether COMM tells the ground who did it.

```c
/* in handle_sa_directive, right after the retirement */
sa_table[target].operational = false;
printk("COMM: SA STOP - SPI %u DEACTIVATED by SPI %u (owner %u)\n", ...);
#if CUBERANGE_COMM_SA_MGMT_REPORT
        sa_retired_target = target_spi;
        sa_retired_by = sa_table[requester].spi;   /* the SPI whose MAC verified - proven, not asserted */
        have_sa_retire = true;
#endif
```

COMM already ships link statistics to the OBC (frames received, refused, the SPI of the last
refusal). This adds two octets to that report — the retired association and the SPI that ordered it
— and the OBC puts them on the housekeeping beacon. The vulnerable half compiles none of it: the
retirement is a console line and nothing more.

**The length is the feature test, all the way down.** COMM's link-stats packet is six octets
without the attribution and ten with it; the beacon is fourteen octets without and eighteen with;
the ground reads the length rather than trusting a fixed layout. Padding to the full width with
zeros would tell a station `SPI 0 retired SPI 0` about a spacecraft that reported nothing — the one
answer worse than silence — so, exactly as with the link counters (EX-U03) and the refused SPI
(EX-S03), the field appears only once there is something true to put in it.

**The attribution is authenticated, but not by this flag.** The beacon is signed by EX-D01's
control (`CUBERANGE_OBC_SIGN_REPORTS`) and required by the ground station; this rides that. That is
deliberate: report authentication is a control this range already has, and re-implementing it here
would be two ideas in one flag. What EX-S05 adds is the *content* — who retired what — and it leans
on EX-D01 to make that content trustworthy. The requester SPI is not a header field the attacker
filled in; it is the SA whose key verified the directive's MAC, so "retired by SPI 11" is a
statement about possession.

## What this does not solve

**A history.** Only the LAST retirement is carried — two octets of target, two of requester,
overwritten each time. A burst of SA-management events collapses to one, the same single-value limit
`link_refused_spi` has carried since EX-S03, and named there for the same reason. Telling "the
partner retired SPI 9, then the operator re-activated it, then it was retired again" from one pair
of octets is impossible; a real mission carries an SA-management audit log, and this range carries a
snapshot. That is the next exercise's to take.

**Attribution is not prevention.** This build reports the theft; it does not stop it — authority is
off here on purpose. In a real deployment EX-S04's authorisation and EX-S05's attribution run
together: the first refuses the partner, the second is what tells the operator the refusal (or, where
authority is off or bypassed, the retirement) happened and who caused it. Split across two exercises
because each is one idea and the pair gate proves it.

**Whose key, not whose hands.** The requester SPI names the *association* that authorised the
directive, which is possession of a key — not the person or station holding it. An intruder who has
stolen the operator's own key (EX-U02's premise) retires SPI 9 and is attributed to the operator's
own SPI, because cryptographically they are the operator. Attribution by SA is exactly as strong as
the key management under it, which this range does not do, and says so.

## The thing worth carrying out of this

EX-S04: refuse the retirement the sender was not authorised to order.
EX-S05: **and put who retired an SA where the ground can read it, so a refusal — or a theft — is a
fact the operator has rather than a silence they interpret.**

Every control in this range that came before its own detector had the same shape: the spacecraft
knew, and the ground did not. EX-G04 gave a refusal a reason; EX-U01 gave an acceptance a report;
EX-U03 gave the radio's refusals a count. This gives an SA retirement a name. **A control's job is
not finished when it acts — it is finished when the operator can see that it acted, and by whom.**
The console always knew who retired the key. The work was getting that one sentence onto the wire,
signed, where someone who is not on the spacecraft can read it.
