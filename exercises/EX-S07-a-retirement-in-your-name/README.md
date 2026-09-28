---
id: EX-S07
title: A retirement in your name
layer: ground segment
difficulty: advanced
duration: 30-45 min
prerequisite: EX-S06
ttp:
  sparta: []
  space_shield: []
---

# EX-S07 — A retirement in your name

*日本語版: [README.ja.md](README.ja.md)*

## The situation

EX-S06 put the whole SA-management **log** on the beacon, each entry attributed to the SA whose MAC
verified the directive. Its `mitigation.md` ended on the limit this exercise takes, in as many words:
attribution names the **key**, never the hand holding it. An intruder who stole the operator's
SPI 10 key and retires SPI 9 with it is logged as *"SPI 9 retired by SPI 10"* — the operator's own
association, cryptographically indistinguishable from the operator.

This is a **host-side** exercise, like EX-G01. **The firmware is EX-S06's hardened build in both
runs**; the spacecraft's log is the same octets either way. The one difference is on the ground:
whether the operator's station knows which SAs are its own (`owned_spis`) and reconciles the log
against its own ledger of the directives it actually sent.

## Your objective

A second station, holding a copy of the operator's SPI 10 key, retires SPI 9. Show that **only the
station that reconciles the spacecraft's log against its own ledger** can say the retirement in its
name is one it never sent — and that every other instrument in this range reads it as the operator.

```bash
make firmware-s06 firmware-u02 firmware-s04     # both builds are EX-S06's hardened COMM
make exercise EX=EX-S07-a-retirement-in-your-name          # a shell inside the range
python3 exercises/EX-S07-a-retirement-in-your-name/solve.py   # at that shell's prompt
```

`make exercise` drops you into a shell **inside the range's network namespace**, and the solver runs
from there. The namespace is loopback-only (SAFE_USE.md) and created fresh per invocation, so a
solver launched in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-S07-a-retirement-in-your-name RUN='python3 exercises/EX-S07-a-retirement-in-your-name/solve.py'
```

Pass `--rotation` to retire SPI 9 as the operator's own station instead of the intruder, and watch
the same log entry reconcile to nothing.

## What happened

```text
who retired SPI 9         beacon log            trusting the attribution   against your own ledger
an INTRUDER (copy of      SPI 9 by SPI 10       SPI 10 is yours, so it     SPI 9 by SPI 10 — in your
SPI 10's key)                                   was you                    name, and never sent by you
the OPERATOR (own SPI 10) SPI 9 by SPI 10       SPI 10 is yours, so it     nothing — you sent it
                                                was you
```

**The top row is the finding.** Every instrument this range built to notice an intruder reads
*nothing wrong*, because every one of them names a key and the key was real: `unexplained_commands`
is `0` (a directive is not a telecommand and never reaches that counter), `link_frames_refused` is
`0` (the MAC verified), one frame was received that the station did not send (someone transmitted —
but not *what*), and the log names SPI 10, the operator's own association. A theft with your key and
your own routine rotation are the same observation.

**The one record that tells them apart is the operator's own ledger of what it sent.** The station
that reconciles keeps `sa_directives_sent`; the intruder's directive is not in it, so `SPI 9 by
SPI 10` is an entry *in your name that you did not send*. The bottom row is the feature test: the
operator's own rotation logs the identical entry, is in the ledger, and reconciles to nothing — a
detector that fired on its own operator would be switched off by the end of the week.

## What you should conclude

**Authentication answers *who holds a key*, not *who is entitled to hold it*.** Every control in this
range asks whether a frame is legitimate; this is the first that asks whether a key that verified is
*yours to have used*. It cannot answer from anything the spacecraft says, because the spacecraft only
ever knew the key — the answer lives in the operator's own record of what it did, on the ground.

**A detector is not a control.** EX-S04's owner check, on its own build, *authorises* this
retirement: a copy of SPI 10's key is the owner of SPI 10 as far as an equality on `owner` can tell
(run 5 measures exactly that — the partner is refused, the copied key is not). Nothing here stops the
retirement; the ledger only lets the operator *notice* it afterwards, and only once a beacon carries
the log to reconcile against.

**This is EX-G01's lesson, one layer down.** EX-G01 put the trust boundary where the *plan* is
assembled, because a spacecraft-side control cannot ask "was this command one anybody was entitled to
schedule". EX-S07 puts it where the operator's *ledger* is kept, because a spacecraft-side log cannot
ask "was this key one I used". Both defences are on the ground, off the asset, because that is the
only place the question can be asked.

## Then fix it

See [mitigation.md](mitigation.md). The verification asserts all of it, end to end:

| Test | Asserts |
| --- | --- |
| `test_with_your_key_every_instrument_says_it_was_you` | the finding: every spacecraft-side instrument reads clean, and the log names the operator |
| `test_your_ledger_names_the_retirement_you_never_sent` | the mitigation: the reconciling station names the entry in its name it never sent |
| `test_your_own_rotation_reconciles_to_nothing` | the feature: the operator's own rotation is in the ledger and raises nothing (and `unexplained_commands` reads 0, not the −1 a counted directive gave) |
| `test_a_partners_retirement_is_not_charged_to_you` | the scope: a partner's retirement under the partner's own SA is EX-S05/S06's attribution, not this question |
| `test_the_owner_check_authorises_it_and_without_a_log_the_ledger_sees_nothing` | why it is a detector: EX-S04's owner check authorises a copy of the owner's key, and a build reporting no log leaves the ledger nothing to reconcile |

`tests/pytest/test_station_sa_ledger.py` carries nine more, faked-link and fast, including the
anti-strawman proof: two stations with the same link, log and ledger, differing only in whether they
know which SAs are their own, and only the verdict changes.
