# Contributing

*日本語版: [CONTRIBUTING.ja.md](CONTRIBUTING.ja.md)*

## The rule the whole project runs on

**Do not claim what you have not run.** Every capability this project depends on is reproduced by a
command in the repository, and the harness that runs those commands carries deliberate-failure
self-tests. The design document's first two revisions asserted a section of "verified" Renode
behaviour that had never been executed; running it refuted six claims at once, including the choice
of MCU. Section 16 of the design is the record. That is why the rule exists.

In practice: if you add a claim, add the command. If you add a test, prove it can fail.

## Contributing an exercise

An exercise is seven files and all seven are required.

```
exercises/EX-XXX-short-name/
  README.md          scenario, objective, three-stage hints, what to conclude
  README.ja.md       the same, in Japanese - the title translates, the front matter does not
  scenario.resc      the Renode setup
  solve.py           the model answer
  mitigation.md      the fix, and what it does not solve
  mitigation.ja.md   the same, in Japanese
  verify_ex_xxx.py   the verification - note the unique basename
```

This said five for a long time, and listed five, and the test that enforces it required seven -
the two Japanese files were missing from the count in both languages, which is a pleasing way for
a bilingual repository to be wrong. The number is now checked against the list the test uses.

`verify_ex_xxx.py` must assert **three** things, not one:

1. the attack succeeds against the vulnerable build
2. the mitigation blocks it
3. **the mitigated build still performs the legitimate operation**

The third is not optional. A "mitigation" that also blocks the operator is an outage, and without
that assertion it passes.

The test file basename must be unique across exercises. Two files called `verify_test.py` cannot be
collected together, and the failure hides well in build output.

**Do not import a sibling `solve.py` by name.** Every exercise has one, and `sys.path.insert` makes
whichever was imported first answer for all of them — EX-G01's verification silently received
EX-F01's module and died at collection with "cannot import name PLUGIN_FILE from solve". Load it by
path; `exercises/EX-G01-schedule-poisoning/verify_ex_g01.py` shows the three lines that do it.

### The vulnerable and mitigated builds must differ by one thing

Guard the flaw behind a single build flag and change nothing else — no board, no `prj.conf`, no
compiler options. Declare the pair in `firmware-matrix.yml` and prove it:

```bash
make pair-gate
```

`tools/config_diff_gate.py` checks six things per pair, and it is part of `make check`. The
snippet that used to live here was a `diff` of the two Kconfig outputs that a contributor had to
remember to type, and it caught only the first of the six: it said nothing about compiler options,
about whether the flag reached the compiler at all, about whether any source reads it, or about
whether the two builds even came from this checkout.

**For a host-side exercise** there is no pair of builds to diff, so the rule needs an analogue.
EX-G01's is: one implementation, two policy objects, and the implementation may not know which it
holds — `test_schedule_policy.py` reads `schedule.py` and fails if it names either policy class. Any
host-side exercise needs something of that shape, because "we only changed one thing" is otherwise
unfalsifiable in a single process. An exercise that only works with a defence disabled must say so in its
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
- The C and Python codecs are separate implementations of the same wire format, compared in
  `tests/pytest/test_c_matches_python.py` — on what they emit AND on what they refuse. The second
  half was missing until 2026-09-12: only the encoders were compared, while the decoders, which
  are the functions that parse attacker-controlled bytes, were not. A C decoder that accepts what
  the Python one rejects means the spacecraft acting on something the ground station discarded,
  silently. That file now fails if a new `cr_*` appears in the header without a comparison.
  Two self-written codecs that share a mistake validate each other perfectly, which is why the
  independent oracles in `tests/golden/` exist as well. Do not weaken either.
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
