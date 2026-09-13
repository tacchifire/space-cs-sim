# CubeRange — working notes

Read this before doing anything in this repository.

## What this is

A hands-on range for space cybersecurity. Satellite firmware runs at instruction level in Renode:
emulated STM32H753 nodes on a CAN bus, a ground station on the host, and security exercises that
are verified in both directions — the attack lands, and the mitigation stops it.

Two spacecraft, four nodes each, and they can run in one emulation. Identity is a build parameter:
`firmware/common/identity.cmake` derives the CSP addresses and the SCID from `CUBERANGE_SAT_INDEX`,
so one source tree still produces every node of every spacecraft. Satellite 0 keeps the numbers
every existing scenario and write-up names — do not renumber it.

**CI has been observed passing: run 8, commit `eaf07ce`, on main, both jobs green, the gate to
`CHECK PASSED` in 838 seconds on ubuntu-latest.** That sentence replaced "nobody has watched it
run" on 2026-09-10 and not a day earlier, because until then it was not true.

It was truer than intended, too. The API reported zero workflows, not zero runs: GitHub registers
a workflow the first time it runs, `push` matched only main, main had no `.github` directory, and
`workflow_dispatch` only appears for workflows on the default branch. The file was pushed,
described in three documents, and inert. Not a button nobody pressed — no button.

Getting from there to green cost three defects, none of which this machine can produce, because
here the environment is always already installed:

1. the working branch was not in the push trigger, so nothing ever ran;
2. the cache restored `~/zephyrproject` and `~/zephyr-sdk` and not the pip packages that drive
   them, and `setup-toolchain.sh` only runs on a cache MISS — cold runs worked, warm runs died at
   `west build`. A cache that restores the inputs and not the tool fails on the second run, and
   the second run is every run after the first;
3. fixing `west` alone left Zephyr's own `requirements.txt` behind and the same failure came back.
   Cache the downloads; install the environment every time.

Four consecutive green runs now (8, 10, 11, 12), including warm-cache ones, which is what the second and third defects below needed to be ruled out. It is still a short record on one runner image. And the hypothesis that took longest was
wrong: probe.sh's 1.5x four-node speed floor, which this eight-core machine clears at 4.3–5.1x,
looked like the obvious thing to blame on a two-vCPU runner. The probe passes there in 110
seconds. Blaming the host was the comfortable guess, and it was the wrong one.

Twelve exercises work today. Seven cover an attack origin each: EX-B01 (internal bus), EX-L01
(space link), EX-A01 (ADCS command envelope), EX-F01 (the OBC's own PUS 8 parser), EX-G01 (the
ground segment that decides what to send), EX-X01 (the crosslink, where the attacker is a
spacecraft in your own constellation) and EX-D01 (the downlink, where the target is the operator
rather than the spacecraft). Three add no new origin on purpose, and they are the
chain the others point at:

- EX-G02, where the origin is legitimate - a real station sending a real command it has no
  authority for, differing from an authorised frame by one octet;
- EX-G03, where the origin need not be an attacker at all - EX-L01's own mitigation keeps one
  replay counter for the whole link, so a second legitimate ground station locks the first one
  out and the console calls it a replay;
- EX-G04, where nothing is broken and the control works, and the refusal never leaves the
  spacecraft - so from the ground it is indistinguishable from a frame that was never received.

EX-X01 is the one that answers all three of them at once, and not kindly. Every defence those
exercises built is ON in its vulnerable build, and a peer spacecraft switches the victim's radio
off anyway, by writing the ground station's source id into a field it chooses. Two lessons:
a control keyed on what the sender ASSERTS does not survive a second way in, while one keyed on
what the sender must POSSESS does - the EPS refuses the same attacker on both builds. And every
link-layer defence here lives in on_tc_frame, which is the space link; a defence is attached to a
path, not to an asset.

EX-S01 is the tenth, and it adds no origin either: it is EX-X01's attack, byte for byte, against
a spacecraft whose uplink now verifies a CCSDS 355.0-B-2 AES-256-GCM MAC and an anti-replay
counter inside the signed portion. EX-L01's replay is dead and EX-G02's forgery half is dead. The
crosslink attack is untouched, because SDLS is a transfer-frame protocol and the crosslink carries
CSP - the packet did not fail a check, it never met one. The strongest control in the range is
still attached to a path, and its strength is what makes that easy to forget.

EX-S02 is the eleventh and it inverts the sentence the other three arrive at. The MAC moves off
the link and onto the telecommand - inside the Space Packet, covering the source id and a
per-source counter, so the same octets verify off the space link, off the crosslink or off the
internal bus. EX-X01's attack, the same file, is refused. The cost is visible: 11 octets of
payload become 31, on every telecommand, forever. And what it still does not answer is what it
never answered - authentication says who, EX-G02 says what, and EX-G02 is untouched.

EX-D01 is the twelfth and the only one that attacks the OPERATOR. EX-G04 built a reporting
channel so the ground could hear a refusal instead of guessing at silence; EX-S02 put a MAC on
every telecommand. Nobody authenticated a report. A peer on the crosslink hands the victim's own
COMM a PUS 1,2 and the victim transmits it: right APID, right sequence, right station, plausible
reason, and an OBC console with nothing on it. The fix is in two places and only one is the
spacecraft - the ground station has to REQUIRE the trailer, because optional authentication is
defeated by not attaching any, and that is measured too.

50 assertions, all measured. The last three each end by naming a limit the next one attacks, and
in each the fix turned out to be reading something already on the wire: a source id that was
arriving and ignored, a virtual channel that was being stepped over, a refused request sitting in
a local variable.

EX-G01 is the host-side one, so the "differ by one flag" proof works differently: one `Scheduler`,
two `ImportPolicy` objects, and `test_schedule_policy.py` fails if `schedule.py` so much as names
either policy class. A scheduler that could inspect its policy could differ in anything.

`docs/superpowers/specs/2026-08-28-cuberange-design.md` is the design and its section 16 is the
record of every claim that turned out to be wrong.

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
make out          # this checkout's build directory; every path below lives under it
python3 -m cuberange.safety.isolate --probe   # which network-namespace backend works here
make probe        # 40 Renode capability checks incl. 5 deliberate-failure self-tests   ~6 min
make firmware-f01 # every satellite-0 image: COMM, OBC (both), EPS (both), ADCS (both)
make firmware-sat1 # the same four roles as spacecraft 1
make firmware-g01 # the hardened OBC and EPS that EX-G01 runs against
make constellation # eight nodes, two spacecraft, and the bus isolation between them
make fleet SATS=4 # every spacecraft the addressing allows: 16 nodes, measured 1.66-2.52x
make golden       # rebuild the independent oracles and regenerate tests/golden (needs network)
tools/ci.sh       # what CI runs: environment check, then make check
tools/ci.sh --self-test   # prove the environment check can fail
make demo-p0      # PUS 17 round trip, ground station to OBC and back
make determinism  # rules G1/G2: three CI-profile runs, byte-identical UART capture     ~30 s
make pair-gate    # every vulnerable/mitigated pair differs by exactly one build flag
make verify-all   # every exercise, three assertions each
make check        # all of it                                                          ~11 min
make soak-p0      # 30 consecutive round trips under a watchdog                        ~4 min
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
| CSP addresses are **five bits** | `csp.py` masks with 0x1F and the CFP CAN identifier uses the same width. Eight addresses per spacecraft gives four spacecraft, and `identity.cmake` refuses an index past that rather than wrapping onto another satellite's nodes |
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
- **Every exercise has a `solve.py`, and `sys.path.insert` makes the first one win.** EX-G01's
  verification silently received EX-F01's module and died at collection with "cannot import name
  PLUGIN_FILE from solve". CONTRIBUTING.md warned about `verify_*.py` basenames; this is the same
  hazard in a file every exercise has. Load a sibling `solve.py` by path.
- **CAN hub names are emulation-scope.** Two scenarios that both say `canHub` join every machine
  to one bus. Nothing reports it, and the symptom is an attack on one spacecraft landing on the
  other. `tests/e2e/test_constellation.py` asserts the isolation with a positive control — the same
  command on the hub it belongs to must work — because asserting only that nothing happened would
  pass just as well against a typo.
- **A global `pause` is unavoidable for a node reload, and costs the other machines nothing they
  can tell.** Measured: `sysbus LoadELF` fails on a running emulation even with the target CPU
  already halted, while `machine RequestReset` and `cpu IsHalted false` both succeed without one.
  `pause` stops the whole TIME DOMAIN, so during the window the other spacecraft's instruction
  counter and elapsed virtual time are both frozen and both resume. The cost is wall clock, which
  matters only to host code holding a real-time budget across it.
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
- **`$OUT` used to default to `/tmp/cuberange` in every checkout, so two working copies on one
  machine built into the same directory.** `west build -p always` in each, and whichever finished
  last owned `build-obc/`. Nothing reports it. The pair gate then compared one checkout's
  vulnerable image against the other's mitigated image and said `expected exactly
  CUBERANGE_OBC_PUS8_LENGTH_CHECK to differ, but the differing cache variables are
  ['CUBERANGE_APID', ...]` — naming flags that exist in no file of this repository. The default is
  now per-checkout (`src/cuberange/paths.py`, mirrored in the Makefile and compared by
  `test_paths.py`), and the gate reads `CMAKE_HOME_DIRECTORY` first so the message says whose
  artifacts these are. `make out` prints the directory.
- **The scenarios had the same path hardcoded, and the exercises pinned only the image under
  test.** So EX-F01 loaded ITS OBC from this checkout and the COMM and EPS around it from
  whatever was in the shared directory. Every `.resc` now derives every firmware and capture
  path from `$out`, and `$out` has **no default**: unset, Renode stops at `No such variable:
  $out` rather than booting somebody else's build. Renode 1.16.1 does concatenate
  (`$comm?=$out/build-comm/zephyr/zephyr.elf` resolves) — measured, not assumed. Launchers
  pass `-e "$out=@<OUT>"`; `test_paths.py` fails if a scenario hardcodes a path or a launcher
  forgets to pass it.
- **Renode binds 0.0.0.0 and 1.16.1 gives you no say in it**, so every launch goes through
  `cuberange.safety.isolate` into a namespace holding only loopback, and `RenodeSupervisor`
  refuses to start outside one. `make` does it; a direct `python3 exercises/.../verify_*.py` will
  refuse and tell you the command. The backend is chosen by RUNNING each candidate, not by
  `shutil.which`: on this host `unshare` is installed and refused by AppArmor
  (`kernel.apparmor_restrict_unprivileged_userns=1`), and `bwrap` is what works. Measured under
  isolation: probe 40/0/3, demo-p0, EX-B01 with the raw CAN injector.
- **A watchdog that undercounts never fires, and looks identical to one that never trips.** The
  supervisor's RSS accounting walked one level of the process tree, on the strength of a comment
  saying Renode's work happens in a child of the launcher. Measured: `./renode` in 1.16.1
  portable-dotnet is a single native process with no children, so one level and the whole tree
  both came to 292 MB and the limit never bit. It would bite the moment Renode is packaged as a
  wrapper. It walks the whole tree now, and `test_supervisor.py` drives it with 80 MB held by a
  grandchild.
- **State that nothing reads is decoration, however carefully it was set.**
  `PowerDomain.latched_unpowered_at_start` recorded the one thing `state is False` cannot
  express (a rail already dark at the first look, with no machine halted and nothing to restore)
  and no caller consulted it. `outage_applied()` is its consumer now, and `test_powerdomain.py` is the
  first unit test this class has ever had; it halts CPUs and reloads ELFs, and every path through
  it used to run only inside an exercise, where a mistake here reads as a firmware fault.
- **A mutation test can silently check the OLD code.** Python validates `__pycache__` on
  (mtime, size). A one-character mutation that keeps the size — `vcid_bits=3` to `vcid_bits=6`,
  `0x7` to `0x3F` — followed by a restore within the same second leaves the stale `.pyc` looking
  valid, so the run measures the code you just put back. It reported a mutation as "not caught"
  here, and the conclusion was written into a docstring before the cache was cleared and the real
  answer turned out to be different. Clear `__pycache__` between the mutation and the run, and
  between the restore and the next one.
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
checks six things. The first establishes that the artifacts are ours at all, and the last three
exist because checks 2 and 3 can pass while the flag does nothing:

0. both halves were built from THIS source tree, read from `CMAKE_HOME_DIRECTORY` in
   `CMakeCache.txt`. Checked first because every later message is misleading otherwise — see the
   shared-`$OUT` entry in the failure modes above;
1. the two Kconfig outputs are identical (836 symbols, measured);
2. no other `CUBERANGE_*` cache variable differs;
3. the two ELFs are not byte-identical — a misspelled flag compiles the same image twice and
   would otherwise "prove" a mitigation that was never built in;
4. the flag is actually referenced by the application source;
5. every translation unit is compiled with an identical command line apart from the declared `-D`,
   which is what stops a vulnerability being manufactured through compiler options. Kconfig says
   nothing about those.

The gate carries `--self-test`, which feeds it seven known-bad pairs and requires each to be
rejected **for its own stated reason**. Rejection alone is not enough: adding check 0 made every
existing case rejectable by provenance, and a self-test that asked only "was it refused?" would
have reported seven passes while exercising one check. A gate that cannot fail is not evidence, and
neither is one that fails for the wrong reason.

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
tests/native/fuzz_proto.c       the codec's contract, under ASan and UBSan; 5 mutations caught
tools/setup-toolchain.sh        Zephyr + SDK, no root, no Docker
firmware/common/                the C wire codec, byte-checked against the Python one
firmware/apps/{comm,obc,eps,adcs}/  node roles; one source tree, one build per profile
src/cuberange/proto/            CCSDS, PUS, CSP codecs and the deframer
src/cuberange/renode/           supervisor, Monitor client, External Control client, power domain
src/cuberange/gs/               ground station
src/cuberange/gs/node.py         a ground station you can run and sit at; two at once
src/cuberange/channel/          the link proxy an attacker taps; several stations attach
attacker/TcpCanInjector.cs      90 lines of C# Renode compiles at runtime; raw CAN from a socket
src/cuberange/ports.py          the port map; one place, with a collision test
src/cuberange/paths.py          the build directory, unique per checkout; make out prints it
src/cuberange/safety/           the loopback-only namespace every Renode launch runs in
src/cuberange/identity.py       who is who, mirroring identity.cmake; a test asserts they agree
src/cuberange/gs/schedule.py    the TC plan and the importer EX-G01 attacks
src/cuberange/gs/import_policy.py  the two policies that are EX-G01's whole difference
firmware/common/identity.cmake  CSP addresses and SCID derived from CUBERANGE_SAT_INDEX
scripts/multi-node/constellation.resc  two spacecraft, two hubs, two links, one Monitor
tools/gen_constellation.py      that scenario, derived; SATS=4 is 16 nodes
scripts/profiles/               interactive and ci execution profiles; scenarios include one
tools/config_diff_gate.py       the anti-strawman gate, with self-tests
firmware-matrix.yml             which vulnerable/mitigated pairs the gate checks
tests/e2e/test_determinism.py   rules G1/G2, reproduced rather than asserted
exercises/EX-*/                 five files each
tests/golden/                   vectors from independent oracles, not from our own codecs
tools/oracles/                  the oracles themselves - csp_oracle.c, tc_oracle.c, the build script
docs/superpowers/specs/         the design; section 3.2 defects, section 16 corrections
```
