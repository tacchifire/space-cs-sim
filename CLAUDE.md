# CubeRange — working notes

Read this before doing anything in this repository.

## What this is

A hands-on range for space cybersecurity. Satellite firmware runs at instruction level in Renode:
emulated STM32H753 nodes on a CAN bus, a ground station on the host, and security exercises that
are verified in both directions — the attack lands, and the mitigation stops it.

**There is no CI.** `make check` is the gate, and a human has to type it. Nine places in this
repository used to describe gates as CI-enforced; there has never been a `.github` directory. The
verification is real and the automation around it is not, and those are different claims — see
section 16 of the design for why this project is careful about the difference.

Three exercises work today: EX-B01 (internal bus), EX-L01 (space link), EX-A01 (ADCS command
envelope). `docs/superpowers/specs/2026-08-28-cuberange-design.md` is the design and its section 16
is the record of every claim that turned out to be wrong.

## The rule

**Do not write a claim you have not run.**

This is not a slogan, it is the reason the project has a shape. The design's first revision
asserted a whole section of "verified" Renode capabilities that had never been executed — Renode
was not even installed. Executing it refuted six of them, including the choice of MCU. The second
revision's central performance premise was wrong by a factor of eight. Both are documented in
section 16 because the record is more useful than the embarrassment is costly.

Concretely:

- Every Renode capability the design relies on is reproduced by `tools/renode-probe/probe.sh`.
  If you add a claim, add a probe. If a probe cannot reproduce it, the claim comes out.
- **The harness must be able to fail.** `probe.sh` section Z feeds itself known-bad input and
  requires those cases to be detected. An earlier version reported PASS for all 34 probes when its
  output directory was missing, because `grep` on a nonexistent file finds no error.
- `make check` ends with `CHECK PASSED`. If you do not see that line, it did not pass — a failing
  sub-target once hid inside 8500 lines of build output while a summary line said "passed".
- Prefer measuring to reading. Most of what is written here was learned by running Renode and
  being wrong first.

## Commands

```bash
make probe        # 40 Renode capability checks incl. 5 deliberate-failure self-tests   ~6 min
make firmware-a01 # COMM, OBC, both EPS profiles, both ADCS profiles
make demo-p0      # PUS 17 round trip, ground station to OBC and back
make determinism  # rules G1/G2: three CI-profile runs, byte-identical UART capture     ~30 s
make pair-gate    # every vulnerable/mitigated pair differs by exactly one build flag
make verify-all   # every exercise, three assertions each
make check        # all of it
make soak-p0      # 30 consecutive round trips under a watchdog                        ~3.5 min
```

`make probe` is very sensitive to host contention: a clean run is about 6 minutes, and with two
other Renode workloads on the box it took 35. Do not read a slow probe as a regression without
checking the load first.

Toolchain install is `./tools/setup-toolchain.sh` — read its header comment, it records two dead
ends that cost real time.

## Pinned, and why

Changing any of these breaks something specific.

| Pin | Why |
| --- | --- |
| Renode **1.16.1**, build `d66b0c2a-202602160923` | `probe.sh` asserts both. Features on `master` (the RAMN board, FDCAN on STM32L5) are not available |
| Zephyr **v4.1.0** | `main` requires Python ≥ 3.12 and fails at cmake time on 3.10. `cmake/modules/python.cmake` per tag: main 3.12, v4.1.0 3.10, v3.7.0 3.8 |
| `csp_conf.version = 1`, set before `csp_init()` | libcsp defaults to **2** and picks header and CFP layouts at runtime. Nodes speaking v2 to each other look perfectly healthy while every v1 tool is silently ignored |
| `SetGlobalQuantum "0.002"` | Largest quantum with byte-identical firmware output. Above it, timing drifts silently and duration-dependently — 5 ms looks correct at 5 s and 10 s and breaks at 20 s |
| `SetGlobalAdvanceImmediately true` (interactive only) | Removes Renode's real-time throttle. Without it four nodes run at 0.301x instead of 2.34x. CI leaves it off and adds `SetGlobalSerialExecution` and `SetSeed` |
| CSP port **10**, not 17 | libcsp's `CSP_PORT_MAX_BIND` defaults to 16 and everything above is reserved for ephemeral source ports |

## Failure modes that produce no error message

This domain is full of them. These have all been hit here:

- **`CANMessageFrame(id, data)` builds a standard 11-bit frame.** Receivers filtering on extended
  29-bit IDs drop it. The frame is structurally perfect and invisible. Pass `extendedFormat: true`.
- **libcsp version mismatch** — see the pin table. No error, anywhere.
- **`csp_sendto` is connectionless** and never reaches a socket using `csp_bind`/`listen`/`accept`.
  Use `csp_connect`/`csp_send`.
- **A Monitor batch aborts at the first failing command, silently**, and every later reply shifts.
  Count the fields you get back.
- **The Monitor corrupts scientific notation**: `1e3` becomes 1, `0b101` becomes 0. Python's repr
  emits `1e-05` for small floats. Format fixed-point.
- **Monitor reads go through the peripheral model.** Polling a read-to-clear register corrupts
  firmware state. GPIO ODR, `LED.State` and `GetGPIOs` are safe; assume nothing else is.
- **`Sensors.OB1203` aborts the whole process** on construction. `Sensors.VEML7700` does not exist
  in 1.16.1. Both are pinned as negative assertions so a future release that fixes them fails CI.
- **`gpioPortB` pin 0 is in `invertedAFPins`.** A rail there reads inverted from ODR. Port D is
  clean, which is why the COMM rail lives on `gpioPortD` pin 5. Measure polarity before asserting.
- **Zephyr's STM32 GPIO driver toggles via ODR, not BSRR.** A watchpoint on BSRR misses every
  toggle.
- **`--hide-log` suppresses the injector's own diagnostics.** Exercises run without it; the Renode
  log is evidence.
- **Renode's Monitor emits the prompt and the command echo as separate chunks.** A client that
  waits for "a prompt" returns before the result arrives and finds it at the head of the next
  command's output. `src/cuberange/renode/monitor.py` anchors on the echo.
- **`cpu TranslateAddress` caches by address and not by access type.** Ask about `Read` on an
  address and the very next `InstructionFetch` on the SAME address returns a false success. A
  different page, or flash, is unaffected. This matters because the natural way to check "SRAM is
  readable but not executable" is to ask in exactly that order, and that order reports that
  shellcode would work. Query `InstructionFetch` first, or in a fresh process. Pinned as a negative
  assertion in probe.sh section G (D22).
- **A failed command aborts the rest of the `-e` chain, including the trailing `quit`.** Renode
  then never exits; it hangs until the supervisor's watchdog kills it, and the log is EMPTY because
  the output is lost with the process group. A harness looking for an error string in the log finds
  nothing and concludes nothing went wrong. This is why scenarios include their execution profile
  as their FIRST command: a bad path takes the machines down with it instead of running every node
  with no quantum set.
- **Renode wedges on ~13% of launches in some conditions** and leaks RSS to 17 GB in 10 minutes.
  Always launch through `src/cuberange/renode/supervisor.py`. It did not reproduce once in 30
  supervised runs on a quiet host, so contention is the likely cause, but the cause is not known.

The full register is section 3.2 of the design.

## Exercises

Five files each, and `verify_ex_*.py` must assert **three** things: the attack works, the
mitigation blocks it, and **the mitigated build still does its job**. Without the third, a
mitigation that simply broke the feature would pass.

Every vulnerable/mitigated firmware pair differs by exactly one build flag. That used to be
proved by a `diff` a contributor was asked to remember to type; `tools/config_diff_gate.py` now
proves it for every pair declared in `firmware-matrix.yml`, and it is part of `make check`. It
checks five things, and the last three exist because the first two can pass while the flag does
nothing at all:

1. the two Kconfig outputs are identical (836 symbols, measured);
2. no other `CUBERANGE_*` cache variable differs;
3. the two ELFs are not byte-identical — a misspelled flag compiles the same image twice and
   would otherwise "prove" a mitigation that was never built in;
4. the flag is actually referenced by the application source;
5. every translation unit is compiled with an identical command line apart from the declared `-D`,
   which is what stops a vulnerability being manufactured through compiler options. Kconfig says
   nothing about those.

The gate carries `--self-test`, which feeds it six known-bad pairs and requires each to be
rejected. A gate that cannot fail is not evidence.

Every `mitigation.md` states what the fix does *not* solve, and the next exercise generally attacks
one of those limits — EX-B01's token is defeated by EX-L01's replay, exactly as EX-B01 predicted.
The chain only works because the write-ups are honest.

## Before publishing anything

`SAFE_USE.md`, `SECURITY.md`, `ASSURANCE.md` and `docs/legal/export-control.md` exist because this
repository ships working attacks. Do not write "EAR99" or "not ITAR" anywhere; nothing has been
classified. Do not add real identifiers, frequencies, keys or endpoints to anything.

## Where things are

```
tools/renode-probe/probe.sh     the evidence gate
tools/setup-toolchain.sh        Zephyr + SDK, no root, no Docker
firmware/common/                the C wire codec, byte-checked against the Python one
firmware/apps/{comm,obc,eps}/   node roles; one source tree, one build per profile
src/cuberange/proto/            CCSDS, PUS, CSP codecs and the deframer
src/cuberange/renode/           supervisor, Monitor client, External Control client, power domain
src/cuberange/gs/               ground station
src/cuberange/channel/          the link proxy an attacker taps and replays through
attacker/TcpCanInjector.cs      90 lines of C# Renode compiles at runtime; raw CAN from a socket
scripts/profiles/               interactive and ci execution profiles; scenarios include one
tools/config_diff_gate.py       the anti-strawman gate, with self-tests
firmware-matrix.yml             which vulnerable/mitigated pairs the gate checks
tests/e2e/test_determinism.py   rules G1/G2, reproduced rather than asserted
exercises/EX-*/                 five files each
tests/golden/                   vectors from independent oracles, not from our own codecs
docs/superpowers/specs/         the design; section 3.2 defects, section 16 corrections
```
