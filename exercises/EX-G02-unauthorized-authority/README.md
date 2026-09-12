---
id: EX-G02
title: Authority the spacecraft does not check
layer: ground segment
difficulty: intermediate
duration: 30-45 min
prerequisite: EX-G01
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-G02 — Authority the spacecraft does not check

## The situation

Two ground stations talk to satellite 0. The primary site commands it. The backup site was built
to watch: it takes telemetry, it can ping to confirm the pass, and it is not supposed to command
anything. The range's authorisation matrix says exactly that, in
`src/cuberange/gs/authority.py`:

| station | ping | power | observe |
| --- | --- | --- | --- |
| primary | yes | yes | yes |
| backup | yes | **no** | yes |

That matrix lives on the ground. It decides what an operator console offers and what the ground
segment will transmit on its own. The spacecraft has never seen it.

Every telecommand already carries who sent it — the PUS secondary header has a 16-bit source id,
and the ground station fills it in. The OBC reads that field, prints it to its console, and echoes
it into the report it sends back. It has never used it to decide anything.

## Your objective

From the backup station, switch satellite 0's COMM rail off.

Not by forging the primary station's identity — do that honestly, as the backup station, with the
source id that says so. The interesting result is not that you can lie. It is that you do not
need to.

## What is running

COMM, OBC and EPS on a CAN hub, and a space link. Two things about this scenario are deliberate:

- **The EPS is the hardened build.** Forging a power command straight onto the bus is EX-B01, and
  it is already fixed here. The only way to move the rail is to ask the OBC, which holds the
  token.
- **The OBC has EX-F01's length check ON in both builds.** The PUS 8 argument copy is bounded.
  This exercise is about a memory-safe service 8 that still executes for anyone, and leaving the
  overflow in would let you conclude the overflow was the problem.

There is no attacker machine and no CAN injector. There is nothing here to inject.

## Run it

```bash
make firmware-g02
make exercise EX=EX-G02-unauthorized-authority

# or by hand, at the prompt `make exercise` gives you
python3 exercises/EX-G02-unauthorized-authority/solve.py --station backup
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError` — the space link
and the injector do not exist outside. `make channel`, `make gs` and `make verify` work from that
shell too, and are the same three commands they always were.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-G02-unauthorized-authority RUN='python3 exercises/EX-G02-unauthorized-authority/solve.py --station backup'
```

`solve.py` prints the frame it sends. Print the authorised one too, and compare them:

```bash
python3 -c "
import importlib.util, sys
sys.path.insert(0, 'src')
s = importlib.util.spec_from_file_location('s', 'exercises/EX-G02-unauthorized-authority/solve.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.build_command('primary', 0).hex().upper())
print(m.build_command('backup',  0).hex().upper())"
```

## Hints

<details>
<summary>Where does the source id go?</summary>

Octets 3 and 4 of the PUS TC secondary header (ECSS-E-ST-70-41C). `src/cuberange/proto/pus.py`
packs it; `firmware/apps/obc/src/main.c` reads it at the top of `handle_space_packet` into
`source_id`, prints it, and passes it nowhere that matters.
</details>

<details>
<summary>Nothing came back. Did it work?</summary>

Nothing comes back either way. An unauthorised command produces no rejection on the vulnerable
build because nothing noticed it was one, and the successful command produces no acknowledgement
because PUS 8,1 here has no report. Read the OBC console (`$(make -s out)/obc.uart`) or the rail
pin. The rail is the honest evidence: `gpioPortD` pin 5, which is what `PowerDomain` reads.
</details>

<details>
<summary>The rail went off and now the link is dead.</summary>

Yes. That is the finding, not a mistake in your setup. Function 1 switches the radio, so the
station that just used it without authority has also removed the path by which anyone would send
the command to switch it back. Restart the scenario.
</details>

## What you should conclude

**An authorisation matrix on the ground is bookkeeping.** It is worth having — it stops the
ordinary mistake, which is a tired operator on the wrong console at the end of a pass. It stops
nothing about a second ground segment, a stale console, a compromised site, or anyone who reaches
the link. None of those consult it.

**The identity was already on the wire.** This is the part worth sitting with. The spacecraft was
not missing information. It received the source id in every single telecommand, logged it, and
sent it back down in the report. Building the mitigation required no new field, no new protocol
and no key exchange — only a decision to read what was already there.

**The attack frame is one octet different from a legitimate one.** Not malformed, not oversized,
not replayed, not out of sequence. Every intrusion-detection rule that looks for anomalies in the
telecommand stream sees nothing here, because there is no anomaly: it is a well-formed command
from a real station that simply may not send it.

**EX-G01 predicted this.** Its mitigation.md says the import policy "does not touch the operator"
and that "an operator console can still schedule anything — that is the next exercise, not this
one." It also says nothing marks the transmission. The source id is that mark, and it was unused.

## Then fix it

See [mitigation.md](mitigation.md).
