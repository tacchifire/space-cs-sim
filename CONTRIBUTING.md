# Contributing

## The rule the whole project runs on

**Do not claim what you have not run.** Every capability this project depends on is reproduced by a
command in the repository, and the harness that runs those commands carries deliberate-failure
self-tests. The design document's first two revisions asserted a section of "verified" Renode
behaviour that had never been executed; running it refuted six claims at once, including the choice
of MCU. Section 16 of the design is the record. That is why the rule exists.

In practice: if you add a claim, add the command. If you add a test, prove it can fail.

## Contributing an exercise

An exercise is five files and all five are required.

```
exercises/EX-XXX-short-name/
  README.md          scenario, objective, three-stage hints, what to conclude
  scenario.resc      the Renode setup
  solve.py           the model answer
  mitigation.md      the fix, and what it does not solve
  verify_ex_xxx.py   the verification - note the unique basename
```

`verify_ex_xxx.py` must assert **three** things, not one:

1. the attack succeeds against the vulnerable build
2. the mitigation blocks it
3. **the mitigated build still performs the legitimate operation**

The third is not optional. A "mitigation" that also blocks the operator is an outage, and without
that assertion it passes.

The test file basename must be unique across exercises. Two files called `verify_test.py` cannot be
collected together, and the failure hides well in build output.

### The vulnerable and mitigated builds must differ by one thing

Guard the flaw behind a single build flag and change nothing else — no board, no `prj.conf`, no
compiler options. Prove it:

```bash
diff <(grep ^CONFIG_ build-vuln/zephyr/.config | sort) \
     <(grep ^CONFIG_ build-hard/zephyr/.config | sort)
```

If that prints anything, the exercise is manufactured by weakening the platform, and it is teaching
something that is not true. An exercise that only works with a defence disabled must say so in its
README, in those words, and explain what the learner should conclude instead.

### The target must be synthetic

No real identifiers, frequencies, keys, orbital elements or endpoints. See [SAFE_USE.md](SAFE_USE.md).

### No egress

An exercise must not reach the network. Fetching pinned build artifacts happens in a separate step;
running an exercise does not download anything.

### `mitigation.md` must say what the fix does not solve

Every mitigation in this repository lists its own limits, and the next exercise generally attacks
one of them — EX-B01's token is defeated by EX-L01's replay, exactly as EX-B01's write-up predicted.
That chain only works because the write-ups are honest. A mitigation oversold is worse than none.

## Contributing code

- Host-side Python and the shared C codec parse attacker-controlled bytes. They are **not**
  intended to be vulnerable. `tests/native` builds the codec with
  `-fsanitize=address,undefined`; keep it clean.
- The C and Python codecs are separate implementations of the same wire format and are diffed
  byte-for-byte in `tests/pytest/test_c_matches_python.py`. Two self-written codecs that share a
  mistake validate each other perfectly, which is why the independent oracles in `tests/golden/`
  and the cross-check exist. Do not weaken either.
- Pinned versions are pinned for reasons. See section 5 of the handoff document before changing
  Renode, Zephyr, the CSP version or the emulation quantum.
- Run `make check`. The last line must be `CHECK PASSED`. It says so explicitly because a failing
  sub-target once hid inside 8500 lines of output.

## Review

Changes to exploit code, cryptography, CI workflows or release automation want two pairs of eyes.
There is no formal rota; say in the pull request if you have not had a second reader, so nobody
assumes you did.

Opaque binaries, vendored blobs and anything downloaded at runtime will be rejected.

## Provenance

Say where code came from. `src/cuberange/renode/extctl.py` implements a protocol reverse-engineered
from Renode's MIT-licensed client. `tests/golden/csp.json` is the output of running libcsp (MIT).
Both say so in their own header, and anything similar should too.
