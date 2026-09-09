# EX-G01 mitigation — provenance, then shape, then authority

*日本語版: [mitigation.ja.md](mitigation.ja.md)*

## The change

One object, swapped:

```python
Scheduler(store, AcceptAnything(),   plugin_dir, transmit)   # vulnerable
Scheduler(store, SignedAndBounded(), plugin_dir, transmit)   # mitigated
```

`SignedAndBounded.accept` does three things, in that order, and the order is part of the fix:

```python
# 1. provenance
if not hmac.compare_digest(sig, sign(doc, self._key)):
    raise ImportRejected("signature does not match the operator key")
# 2. shape - only now, on a document the operator is known to have produced
...
# 3. authority - a signature says who wrote it, not what it may ask for
if service not in self._allowed:
    raise ImportRejected(f"service {service} may not be scheduled by import")
```

Checking the schema of a document you have no reason to trust is work done on an attacker's
behalf, and a schema error tells them which field to fix next. Provenance first.

## Signed is not the same as permitted

Step 3 is the one that is easy to leave out, and leaving it out is how a signed-import feature
becomes a signed-anything feature.

An operator's key proves who produced the file. Whether an *import* should be able to schedule a
power command is a different question with a different answer, and the two get conflated
constantly. `IMPORT_ALLOWED_SERVICES` is `{3, 17}` — housekeeping and connection tests, the things
a partner's pass plan legitimately asks for. Function management, which is how the ground switches
a rail, is not on it.

`test_a_signature_alone_does_not_authorise_any_command` is the test for exactly this: a *validly
signed* plan carrying PUS 8 is still refused.

## Why "one thing differs" is checkable here

The firmware exercises prove they are not manufactured by diffing two builds. Host-side Python has
no such artefact, and CONTRIBUTING.md's rule was written entirely in Zephyr terms — so an exercise
whose two halves were "two code paths in one process" could claim it changed one thing with nothing
able to check.

The analogue: **one scheduler, two policies, and the scheduler may not know which it holds.**
`test_the_scheduler_does_not_know_which_policy_it_holds` reads `schedule.py` and fails if it so
much as names either policy class. A scheduler that could inspect its policy could differ in
anything at all, and then a "mitigation" might be quietly changing how commands are transmitted
rather than which ones are accepted.

## What this does NOT solve

Say it plainly, because a mitigation oversold is worse than none:

- **The key is a fixed string in the repository.** Like `POWER_TOKEN` in EX-B01, and with the same
  consequence: anyone who can read the ground segment's files can sign a plan. It raises the cost
  from *write a file* to *read a file and then write one*, which is real and small.
- **It does not touch the operator.** The policy governs imports. An operator console can still
  schedule anything, and `test_an_operator_entry_is_not_bounded_by_the_import_policy` asserts that
  it can — so a compromised operator account walks straight past all of this. That is the next
  exercise, not this one.
- **The two paths still share a directory.** The signature makes the shared directory survivable;
  it does not make it a good idea. Separating the plugin sink from the import source removes a
  whole class of mistake rather than authenticating past it, and it costs nothing.
- **The allowlist is a list.** It bounds services, not arguments. A plan may schedule housekeeping,
  and a housekeeping request with a pathological parameter is still a request this policy waves
  through. Bounding *what a command may say* is EX-A01's subject and it is not solved here either.
- **Nothing marks the transmission.** `ScheduledCommand.origin` records that an entry came from
  `passpredict.plan.json`, and that never reaches the wire or the log the spacecraft produces.
  After the fact, a poisoned plan and a keystroke are indistinguishable in the downlink.

## What a flight design would do instead

Separate the directories, so a plugin's output path is not an input to anything. Sign plans with a
key held in an HSM or at least outside the file tree the plugins can read. Put the authority check
in a place that sees the whole plan rather than each entry — a plan that switches the radio off and
never back on is suspicious in a way no single entry is. Record the origin of every transmitted
command in the ground segment's own log AND in a downlinkable event, so the question "who asked for
this" has an answer six months later. And require two operators for a command that can sever the
link, which is the control that would have stopped this one regardless of how the entry got in.
