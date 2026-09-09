---
id: EX-L01
title: Replay a telecommand you cannot read
layer: space link
difficulty: introductory
duration: 30-45 min
prerequisite: EX-B01
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-L01 — Replay a telecommand you cannot read

*日本語版: [README.ja.md](README.ja.md)*

## The situation

EX-B01 ended with the EPS refusing unauthenticated power commands, and its mitigation notes
admitted the control was replayable. Collect on that.

This time you are not on the internal bus. You are on the **space link** — the position of anyone
with a receiver pointed at the right patch of sky. You can hear telecommands going up and
telemetry coming down, and you can transmit.

You do not have the power token. You do not need it.

## Your objective

Silence the satellite using only bytes you observed. Success is `EPS: COMM rail OFF (commanded)`
appearing a second time, with no operator involved.

## What is running

```
ground station --> [ channel ] --> COMM.usart2 --CSP/CAN--> OBC --> EPS
                       ^
                  you are here: you see every frame, and you can transmit
```

The EPS is the **hardened** build from EX-B01 — forging a bus frame will not work here. The
operator's legitimate path is PUS 8,1 function 1, which the OBC executes by sending the EPS an
authenticated power command.

The satellite recovers by itself: the EPS restores the COMM rail 30 seconds after it goes off.
Real spacecraft carry that interlock so one bad command cannot permanently sever the only channel
capable of sending a better one. It is also what makes this exercise observable.

## Run it

```bash
make firmware-exl01
python3 exercises/EX-L01-tc-replay/solve.py     # the model answer
make verify EX=EX-L01-tc-replay
```

## Hints

<details><summary>Hint 1 — what to capture</summary>

Watch the uplink while the operator does something with a visible effect. `LinkChannel` records
every frame in `uplink_frames`; you want the one that made the rail drop.
</details>

<details><summary>Hint 2 — what to send</summary>

Nothing you have to build. `LinkChannel.replay()` transmits a captured frame byte for byte. Do not
re-encode it — re-encoding would let you accidentally fix a field that the real defence checks,
and then you would not know which of the two things you tested.
</details>

<details><summary>Hint 3 — timing</summary>

The satellite has to be alive for the replay to be worth anything. Wait for
`EPS: COMM rail ON (FDIR restore)` before transmitting.
</details>

## What you should conclude

The interesting part is what the attacker never had. No token. No key. No parser. No idea what any
field in that frame means. Authentication that does not bind a command to a *moment* authenticates
the message and not the request, and a recording is a perfectly valid message forever.

Notice also who defeated the EPS's authentication: the OBC did, on the attacker's behalf. The
mitigation from EX-B01 was checking that the sender knew a secret, and the sender did — the
satellite was faithfully replaying its own legitimate behaviour.

Willbold et al. (*Space Odyssey*, IEEE S&P 2023) report exactly this shape in real satellite
firmware: encryption present without equivalent authorisation guarantees, and telecommand paths
where possession of a static credential is treated as authority.

## Then fix it

See [mitigation.md](mitigation.md). The verification asserts all three directions:

| Test | Asserts |
| --- | --- |
| `test_replay_succeeds_against_the_vulnerable_comm` | captured bytes replayed kill the radio again |
| `test_replay_is_rejected_by_the_hardened_comm` | the same bytes are refused |
| `test_the_hardened_comm_still_accepts_new_commands` | the operator's next command still works |
