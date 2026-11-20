---
id: EX-F02
title: The other copy of the key
layer: firmware
difficulty: intermediate
duration: 45-60 min
prerequisite: EX-F01
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-F02 — The other copy of the key

*日本語版: [README.ja.md](README.ja.md)*

## The situation

You are on the space link again, where EX-F01 left you: a receiver pointed at the right patch of
sky, and a transmitter. Nothing is going to overflow this time — this OBC is built with EX-F01's
length check on. You are going to send a telecommand the OBC implements on purpose: PUS 6,5, a
memory dump. It reads memory and sends it down, because that is what it is for.

The OBC signs its reports. That is EX-D01's fix, and to sign it needs the key: `cr_sdls_key`, the
32 octets that have authenticated the space link since EX-S01. The key is a `static const` in
`firmware/common/cuberange_keys.h`, and a `static const` lives in `.rodata` — on this part, flash,
at an address the image's own symbol table names.

## Your objective

Read the SDLS key out of the OBC over the space link, without having it.

Success is not 32 octets that look like the string in the header: the range's key is deliberately
recognisable, so a match would prove nothing. Success is 32 octets that verify the trailer on a
report the spacecraft signed.

## What is running

```
ground station --TCP--> COMM.usart2 --CSP/CAN--> OBC --> EPS
       you are here                            the target
```

EX-F01's layout and EX-F01's position: no attacker machine and no CAN injector. The OBC is a
different build — it signs its reports, which is what puts the key in it, and it verifies no
telecommand, so a dump request needs nothing an attacker does not have.

## Run it

```bash
make firmware-f02
make exercise EX=EX-F02-key-extraction                       # runs the scenario and leaves it up
python3 exercises/EX-F02-key-extraction/solve.py --show      # the request, without sending it
python3 exercises/EX-F02-key-extraction/solve.py             # the model answer
make verify EX=EX-F02-key-extraction
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError` — the space link
does not exist outside. `make channel`, `make gs` and `make verify` work from that shell too.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-F02-key-extraction RUN='python3 exercises/EX-F02-key-extraction/solve.py'
```

## Before you start: why the key is in this computer

It is not a mistake peculiar to this OBC. A `static const` is compiled into every image that uses
it, so every image that signs or verifies with the key holds a copy: COMM verifies the link with it
(EX-S01), an OBC that verifies telecommands holds it (EX-S02), and an OBC that signs its reports
holds it (EX-D01). The radio's copy sits behind the radio. The OBC's copy sits behind every service
the OBC implements, and one of them reads memory.

Do not reach for EX-F01's overflow: it is closed in both builds here. Code that is already running
would read the key just as well — `mitigation.md` says so plainly — but this exercise is about a
spacecraft that hands the key over when it is asked.

## Hints

<details><summary>Hint 1 — where the key is</summary>

```bash
arm-zephyr-eabi-nm -S "$(make -s out)"/build-obc-f02-vuln/zephyr/zephyr.elf | grep cr_sdls_key
```

`-S` prints the size as well as the address. Look at the address: on this part, 0x08000000
upwards is flash. Nothing about the build hides it and nothing randomises it.
</details>

<details><summary>Hint 2 — how to ask</summary>

Read `handle_memory_dump` in `firmware/apps/obc/src/main.c`. The application data is seven octets,
big-endian: a memory id, a start address and a length. Memory id 0 is the whole address space and
the only one implemented. One report carries at most 128 octets, which is four keys.
`GroundStation.send_pus` will send it for you.
</details>

<details><summary>Hint 3 — the twenty octets you did not ask for</summary>

The TM 6,6 repeats the seven octets, then carries the dump, then twenty more: the PUS-auth trailer,
a sequence number and a MAC, because this OBC signs. The Space Packet's length field covers them,
so a parser that is not checking signatures sees them as the end of the application data — the
dump's own length tells you where the dump stops. `cuberange.proto.pus_auth.verify(packet, key=...)`
checks a trailer. Try the octets you just read as the key.
</details>

## What you should conclude

**The service worked.** Nothing was malformed, nothing overflowed and nothing was bypassed. PUS 6
read memory because reading memory is its job, and the spacecraft authenticated what it sent you —
with the key it was sending you.

**One key, several images.** The key that guards the space link is not in the radio alone. Every
image that signs or verifies with it holds a copy, in flash, for the life of the image, at an
address the symbol table names. EX-S01 put it on one door; it is behind every door of every node
that holds it. EX-B01's power token is held the same way — `POWER_TOKEN` is a `static const` in
the OBC's source too, and EX-B01's write-up points here.

**Possession is the whole proof.** SDLS (EX-S01), the telecommand trailer (EX-S02) and the report
trailer (EX-D01) all prove possession of these 32 octets and of nothing else. Once they have been
read, every one of those controls will take the reader for the operator. EX-U02 started from
"somebody has your key"; this is one way somebody gets it.

**Hardware would not have hidden it.** The design (section 6.5) records that the STM32H7's CRYP
block is an accelerator, not a vault: the key still comes from memory the firmware manages, and
Renode's model of the part reads its key registers straight back.

Willbold et al. (*Space Odyssey*, IEEE S&P 2023) report dangerous maintenance primitives — direct
memory operations among them — in the real satellite firmware they analysed.

## Then fix it

See [mitigation.md](mitigation.md). The verification asserts five things — the usual three, plus
two that keep the exercise from proving itself:

| Test | Asserts |
| --- | --- |
| `test_the_key_is_in_both_images_in_flash` | the premise, from the ELFs alone: both halves hold the key, in flash, and it is the range's key |
| `test_the_dump_reads_the_key_and_the_key_verifies_the_spacecrafts_reports` | the vulnerable OBC returns the key, and those octets verify signed reports from the same run while a key one bit away does not |
| `test_the_mitigated_obc_refuses_to_dump_the_key` | the OBC logs a rejection, no dump report arrives, and the spacecraft still answers |
| `test_the_mitigation_does_not_break_the_dump_service` | a dump of the public block is served, starts with its magic, and is signed |
| `test_a_dump_that_starts_in_the_block_and_runs_past_it_is_refused` | the request a check on the start address alone would serve |

The second one checks the key by **using** it. The fifth exists because a check on a dump's start
address alone serves the fourth test's request exactly as a correct check does.

**Not yet run.** This exercise was written on a machine with no Zephyr toolchain and no Renode.
Until `make verify EX=EX-F02-key-extraction` has passed somewhere, the table above says what the
tests check, not that it holds.
