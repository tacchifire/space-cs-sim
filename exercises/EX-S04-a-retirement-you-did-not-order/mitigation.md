# EX-S04 — an owner for every Security Association, and the retirement nobody can attribute

## What the mitigated build does

Both halves of this pair carry all three Security Associations, the `owner` field, and the `STOP_SA`
directive handler. **SA management by telecommand is not the difference — it is the feature.** The
difference is one authorisation check.

```c
struct cr_sdls_sa {
        uint16_t spi;
        const uint8_t *key;
        uint8_t owner;            /* the party this SA belongs to */
        bool operational;
};

static struct cr_sdls_sa sa_table[] = {              /* mutable now: EX-S04 retires at runtime */
        { CR_SDLS_SPI,         cr_sdls_key,         CR_SDLS_OWNER_OPERATOR, /* ... */ },
        { CR_SDLS_SPI_ROTATED, cr_sdls_key_rotated, CR_SDLS_OWNER_OPERATOR, true },
        { CR_SDLS_SPI_PARTNER, cr_sdls_key_partner, CR_SDLS_OWNER_PARTNER,  true },
};
```

A `STOP_SA` directive arrives on the reserved control virtual channel, authenticated like any other
frame — so by the time COMM reads it, the sender's *identity* is already proven: they hold the key
of the SA they framed under. The one check that `CUBERANGE_COMM_SA_MGMT_AUTHORITY=1` adds asks the
question authentication cannot:

```c
#if CUBERANGE_COMM_SA_MGMT_AUTHORITY
        if (sa_table[requester].owner != sa_table[target].owner) {
                /* refused, and the radio names the REQUESTER, not the target */
                return;
        }
#endif
        sa_table[target].operational = false;
```

That one `#if` is the pair's whole difference. `SA_DEACTIVATION` is held at `0` in **both** halves,
so every SA boots operational and the exercise retires one on the wire; the partner SA and the
handler are in both halves too, so the pair gate sees exactly one `CUBERANGE_*` flag change and
nothing else — which is what stops the mitigation from being "we deleted the feature".

The refusal names the **requester** (SPI 11), not the target (SPI 9). Naming the target would send
the operator to look at their own association, which is exactly what has *not* changed; the incident
is "somebody overreached", and the party who did is the one worth reporting.

## The directive, and an honest gap

`STOP_SA` is three octets — a directive type and the target SPI — carried on control VC 7. The
**outer** authenticated frame is the one `tools/oracles/sdls_oracle.c` already checks against NASA
CryptoLib, so its layout is oracle-backed. The **inner** directive has no such oracle in this
repository yet: CCSDS 355.1 Extended Procedures have a CryptoLib implementation, but wiring it up as
a golden oracle is a project of its own. So the inner PDU is cross-checked C-against-Python by
`test_c_matches_python.py` — including that both parsers refuse the same short PDUs — and named as a
gap in `ASSURANCE.md` rather than dressed up as more than it is. Two self-written parsers agreeing
is not proof, which is precisely why the gap is written down.

## What this does not solve

**Attribution.** This is the finding, and the next exercise. From the ground, the attack (row 1) and
a legitimate retirement (row 3) are the *same observation*: SPI 9 stops answering. Authentication
answered *who framed the directive* and authorisation answered *whether they were allowed to* — and
**neither is on the downlink**. The only record of who retired an SA is the COMM console, `by SPI
11` versus `by SPI 10`, which never leaves the spacecraft. That is EX-G04 and EX-U03's problem —
a control that works and cannot be heard working — arriving for SA management. An operator who finds
SPI 9 dark cannot tell, from anything they can see, whether they did it or an adversary did.

**Availability.** This exercise deliberately did not guard the *destructive* directions of the same
command. Nothing here stops a `STOP_SA` from retiring the **last** operational SA and leaving the
spacecraft with no way to be commanded at all; nothing orders activation before deactivation, so a
directive that arrives before the SA it depends on has undefined footing. Both are real, both are
authorisation's neighbours rather than authorisation itself, and folding them in would have made the
pair differ by more than one idea. They are named here so the next exercise can attack them honestly.

**Who the owners are.** `owner` is a tag compiled into the table, not an identity a mission manages.
Real SA administration binds authority to a managing entity through key management this range does
not do — the same boundary `keys.py` draws around the keys themselves. The lesson that survives is
the *shape* of the control, not a claim to have built key management.

## The thing worth carrying out of this

EX-S03: retire a key, and check that the door is shut by asking to be let in.
EX-S04: **and check that the frame asking to shut it was one the sender was allowed to send.**

Every control before this one asked whether a frame was *legitimate* — well-formed, fresh,
correctly signed. This one asks whether the sender was *entitled to the effect they requested*, and
that is a different question with a different answer: the partner's frame is entirely legitimate and
entirely unauthorised. **A valid signature is a statement about a key, not about its bearer's
rights** — and the moment an authenticated party can act on something they do not own, the fix is
never a better signature. It is an owner.
