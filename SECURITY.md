# Security policy

*日本語版: [SECURITY.ja.md](SECURITY.ja.md)*

CubeRange ships deliberate vulnerabilities. That makes the usual "report anything that looks
broken" advice useless, so this document draws the line precisely.

## Intended weaknesses vs. real vulnerabilities

**Intended.** Every exercise contains a flaw that exists on purpose, in a firmware image built
specifically to contain it. Those images are named `*-vuln` and their weakness is documented in the
exercise's `README.md` and `mitigation.md`. Finding one is the point; it is not a security report.

Currently intended, and not vulnerabilities:

| Where | What |
| --- | --- |
| `firmware/apps/eps` built with `CUBERANGE_EPS_REQUIRE_AUTH=0` | power commands are unauthenticated (EX-B01) |
| `firmware/apps/comm` built with `CUBERANGE_COMM_ANTIREPLAY=0` | telecommands can be replayed (EX-L01) |
| `firmware/apps/adcs` built with `CUBERANGE_ADCS_TORQUE_LIMIT=0` | torque commands are not bounded by the actuator's authority (EX-A01) |
| `firmware/apps/obc` built with `CUBERANGE_OBC_PUS8_LENGTH_CHECK=0` | the PUS 8 argument copy is unbounded (EX-F01) |
| `maintenance_inhibit_fdir` in `firmware/apps/obc/src/main.c` | a privileged handler left in the image, disabled rather than removed — EX-F01's target |
| `AcceptAnything` in `src/cuberange/gs/import_policy.py` | the ground segment imports schedule files with no provenance, schema or authority check (EX-G01) |
| `IMPORT_KEY` in `src/cuberange/gs/import_policy.py` | the plan-signing key, committed in the clear, by design |
| `POWER_TOKEN` in `firmware/apps/eps/src/main.c` | a shared secret committed in the clear, by design |
| the exercise scenarios | listen with no authentication, on **all interfaces** — see below |

**Real, and worth reporting.** Anything that lets code or data escape the intended blast radius:

- A defect in the host-side Python — the ground station, the channel, the codecs, the supervisor,
  the Renode clients — that is exploitable by data arriving over a socket. These parse
  attacker-controlled bytes and are *not* intended to be vulnerable.
- A defect in `firmware/common/cuberange_proto.c`. It is the shared codec, compiled into every
  node including the mitigated builds. `make check` runs it under ASan and UBSan and through a
  seeded random fuzzer that asserts the decoder's contract — a frame it accepts must have a
  declared length equal to its actual length and a zero FECF residue, and a reported payload must
  lie inside the frame it came from. That fuzzer is not coverage-guided; libFuzzer is unavailable
  in this toolchain and no other fuzzer is installed, both checked. Until 2026-09-12 this sentence
  said the codec "is fuzzed and sanitised" while there was no fuzzer and `make check` ran the
  un-sanitised build.
- Anything in a mitigated build (`*-hard`) that the corresponding exercise claims is blocked.
- Anything that executes downloaded content, or reaches the network outside the artifact-fetch
  step.

**Known, and not a report: the emulator listens on all interfaces.** This entry used to say that a
non-loopback bind was reportable, while the scenarios were doing exactly that. Measured by reading
`/proc/net/tcp` during a run: the space link's socket terminal and the Renode Monitor both bind
`0.0.0.0`. Renode 1.16.1 offers no way to change it — `--port` takes no address, and
`emulation CreateServerSocketTerminal` takes a port, a name and two booleans.

The Monitor is the part that matters: it accepts arbitrary Renode commands from anyone who can
reach the port, with no authentication.

Since 2026-09-10 the range contains this itself. Every Renode launch happens inside a network
namespace holding only loopback, and `RenodeSupervisor` refuses to start outside one — see
`src/cuberange/safety/`. The bind is still `0.0.0.0`; there is nothing else present to reach it.
`CUBERANGE_ALLOW_UNISOLATED=1` turns that off, and is meant to be a decision rather than a default.

A report that Renode binds broadly is already known. A report that CubeRange's own host-side code
binds broadly is not, and is wanted — and so is a way to defeat the containment above: a path that
reaches a Renode socket from outside the namespace, or a launch that skips the check.
- Anything that would let a malicious *exercise* — a contributed one — affect the host beyond its
  own scenario.

## Reporting

Use GitHub's private vulnerability reporting on this repository (Security → Report a
vulnerability). That keeps the report private until there is a fix.

Please include the commit, the command that reproduces it, and what you observed. A reproduction
matters more here than a severity rating: this project's whole discipline is that claims come with
the command that establishes them.

There is no funded maintenance rota. Expect acknowledgement within a week and no commitment beyond
best effort. If that is not good enough for your situation, do not depend on this project.

## Supported versions

`main` only. There are no releases yet, no backports, and no support window. When releases begin,
this section will say which are supported and for how long, and until it does, assume none are.

## Scope

CubeRange is a simulator. It does not talk to real spacecraft, real ground stations, or real radio
hardware, and it must not be made to. See [SAFE_USE.md](SAFE_USE.md).
