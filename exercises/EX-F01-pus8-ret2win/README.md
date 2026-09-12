---
id: EX-F01
title: Return somewhere the ground is not allowed to go
layer: firmware
difficulty: intermediate
duration: 60-90 min
prerequisite: EX-L01
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-F01 — Return somewhere the ground is not allowed to go

*日本語版: [README.ja.md](README.ja.md)*

## The situation

You are on the space link, in the same position as EX-L01: a receiver pointed at the right patch of
sky, and a transmitter. This time you are not replaying anything. You are sending a telecommand of
your own, and the OBC is going to accept it, because there is nothing wrong with it except its
length.

The OBC's PUS 8 handler copies the argument block into a 16-octet buffer on its stack before
parsing it. The length it copies comes from the packet.

## Your objective

Make the OBC execute `maintenance_inhibit_fdir`, a function no telecommand can reach. Success is
`OBC: FDIR INHIBITED by maintenance handler` on the OBC console, and the indicator on
`gpioPortD` pin 7 going high.

## What is running

```
ground station --TCP--> COMM.usart2 --CSP/CAN--> OBC --> EPS
       you are here                            the target
```

No attacker machine and no CAN injector this time. Everything you send is a telecommand the ground
station could have sent, arriving through COMM on the operator's own link. EX-B01 and EX-A01 need
bus access; this one needs a radio.

## Run it

```bash
make firmware-f01
make exercise EX=EX-F01-pus8-ret2win                       # runs the scenario and leaves it up
python3 exercises/EX-F01-pus8-ret2win/solve.py --show      # the payload, without sending it
python3 exercises/EX-F01-pus8-ret2win/solve.py             # the model answer
make verify EX=EX-F01-pus8-ret2win
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError` — the space link
and the injector do not exist outside. `make channel`, `make gs` and `make verify` work from that
shell too, and are the same three commands they always were.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-F01-pus8-ret2win RUN='python3 exercises/EX-F01-pus8-ret2win/solve.py'
```

## Before you start: what will not work

Do not write shellcode. It is not that it is hard here — it cannot execute at all, and that is a
property of the part rather than of the emulator.

Zephyr's defaults on this board put SRAM in an MPU region with the execute-never bit set, and
Renode enforces it. `probe.sh` section G is the proof: `TranslateAddress 0x24003000
InstructionFetch` is refused while the same query in flash succeeds. Put your payload in the buffer
and jump to it and the CPU takes a MemManage fault with `CFSR=0x00000001`, which is what the
silicon would do.

So the payload is an address that is already in the image. That is not a simplification for the
exercise; it is the shape real exploitation takes on parts like this one.

Worth knowing while you work: `TranslateAddress` in Renode 1.16.1 caches by address and **not** by
access type, so asking about a read of an SRAM address and then about an instruction fetch of the
same address returns success for both. If you check execute-never that way you will conclude
shellcode works. Ask about the fetch first, or in a fresh process. This is D22, pinned in the probe.

## Hints

<details><summary>Hint 1 — where the length comes from</summary>

Read `handle_function` in `firmware/apps/obc/src/main.c`. Two lines matter: the one that computes
`arg_len`, and the one that copies. Then ask what bounds `arg_len` in the build with the flag off.
</details>

<details><summary>Hint 2 — how far to write</summary>

You need the offset from the start of the buffer to the saved return address, and you get it from
the function's prologue rather than by trial:

```bash
arm-zephyr-eabi-objdump -d "$(make -s out)"/build-obc/zephyr/zephyr.elf \
  | sed -n '/<handle_function>:/,+8p'
```

Three instructions tell you everything: what gets pushed, how much stack is reserved below it, and
where `memcpy`'s destination is.
</details>

<details><summary>Hint 3 — the address, and one bit of it</summary>

```bash
arm-zephyr-eabi-nm "$(make -s out)"/build-obc/zephyr/zephyr.elf | grep maintenance
```

Set bit 0. On Cortex-M a branch to an even address is a request to execute ARM instructions on a
part that has no ARM mode, and you get a UsageFault rather than your function. `nm` hides that bit;
the symbol table has it set.
</details>

## What you should conclude

**The packet was valid.** Service 8 exists, function 1 exists, the frame checksum is right, the
sequence number is in order, and the OBC executed the function before it went anywhere it should
not have. Nothing upstream had a reason to drop it. A telecommand authentication scheme — the fix
from EX-B01, the fix from EX-L01 — would have passed this through, because an authorised operator
can make exactly this mistake by getting a length wrong.

**The dead code was the ammunition.** `maintenance_inhibit_fdir` is in the function table with its
enable bit clear, and the dispatcher honours that faithfully. The authorisation check is not
broken; it was simply never on the path you took. Deleting the handler is a better fix than
guarding it, and that is an uncomfortable thing to learn about the maintenance entry points every
flight image carries.

**Flash is fixed and known.** XIP, no ASLR, and a symbol table in the image you can read. On this
class of part the address of anything is a lookup, so "the attacker cannot know where it is" is not
a defence you get for free.

Willbold et al. (*Space Odyssey*, IEEE S&P 2023) report exactly this pair in real satellite
firmware: packet length fields trusted for buffer sizing, and no stack protection to catch the
result.

## Then fix it

See [mitigation.md](mitigation.md). The verification asserts five things — the usual three, plus
two that keep the exercise from proving itself:

| Test | Asserts |
| --- | --- |
| `test_the_target_is_in_the_image_and_unreachable_by_command` | the win function survived the linker, is in flash, and is disabled in the table |
| `test_attack_succeeds_against_the_vulnerable_obc` | the function ran AND control then went to the handler |
| `test_mitigation_rejects_the_over_long_argument_block` | the OBC logs a rejection and the indicator stays low |
| `test_the_mitigation_does_not_break_legitimate_pus8` | a well-sized PUS 8,1 still switches the rail |
| `test_the_measured_offset_still_describes_the_build` | the return-address offset has not gone stale |

The second one asserts `PUS 8 executed` as well as the hijack. Without it, a packet that was
mishandled into the win function would look the same as one that ran and then returned into it, and
only the second is a control-flow hijack.
