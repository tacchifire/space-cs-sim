# Safe use

CubeRange teaches attacks against spacecraft protocols. The techniques are real; the target is not.
This document says what that means in practice.

## What this project is a target for

Everything CubeRange attacks is synthetic and runs on your own machine:

- The spacecraft is four emulated STM32H753 nodes inside Renode.
- The "radio link" is a TCP socket on loopback.
- The "bus" is a Renode CAN hub in the same process.
- Every identifier is invented. The spacecraft ID is `0x0A9` because it was free. There are no real
  APIDs, no real virtual channel IDs, no real frequencies, no real orbital elements, no real keys.
- `POWER_TOKEN` is four bytes chosen at random for an exercise and committed in the clear.

Nothing here is calibrated against, derived from, or tested on a real spacecraft.

## Rules

**Do not point this at anything real.** Not a real ground station, not a real satellite, not a real
radio, not a lab that shares a bus with something that flies. The tooling speaks subsets of real
standards, which is exactly why this matters: the frames it builds are close enough to real ones to
be dangerous in the wrong place.

**Do not add real material.** No real APIDs, spacecraft IDs, frequencies, TLEs, key material, or
endpoint addresses, in code, in test data, or in an exercise. If an exercise needs to look
realistic, invent something and say in its README that it is invented.

**Do not make it reach the network.** Scenarios bind loopback. The only outbound traffic in the
whole project is fetching pinned build artifacts, and that happens in a separate step from running
anything. An exercise that phones home is not an exercise.

**Do not use it to justify a claim about a real system.** See [ASSURANCE.md](ASSURANCE.md). An
exploit working in Renode says something about the code; it says nothing about a spacecraft you
have not tested.

## If you are teaching with it

Say plainly that participants are attacking a simulation. It sounds obvious in a room and stops
being obvious in a write-up six months later, which is where "we compromised a satellite" sentences
come from.

Pair every attack with its mitigation. Each exercise ships one, along with a written account of
what the mitigation does *not* solve, because a mitigation oversold is worse than none.

## If you are contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every new exercise needs a synthetic target, a mitigation,
tests in both directions, and no egress.
