# Security policy

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
| `POWER_TOKEN` in `firmware/apps/eps/src/main.c` | a shared secret committed in the clear, by design |
| the exercise scenarios | listen on loopback ports with no authentication |

**Real, and worth reporting.** Anything that lets code or data escape the intended blast radius:

- A defect in the host-side Python — the ground station, the channel, the codecs, the supervisor,
  the Renode clients — that is exploitable by data arriving over a socket. These parse
  attacker-controlled bytes and are *not* intended to be vulnerable.
- A defect in `firmware/common/cuberange_proto.c`. It is the shared codec, compiled into every
  node including the mitigated builds, and it is fuzzed and sanitised precisely because it is not
  supposed to have any.
- Anything in a mitigated build (`*-hard`) that the corresponding exercise claims is blocked.
- Anything that binds a socket to a non-loopback address, executes downloaded content, or reaches
  the network outside the artifact-fetch step.
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
