# Codespaces foundation validation (PR-1)

## Purpose and status

Prepared 2026-11-21 from `ba38253`, on `feat/devcontainer-foundation`.
This is preparation for a real Codespace measurement, not evidence that Codespaces works.
The coordinator owns push, PR creation and the PHASE-CONT-REPORT.md entry. No push or PR is part of this preparation.
Scope follows the coordinator's `PUBLISHING-SURVEY.md` §9, PR-1; the minimal devcontainer JSON is included now so the owner can launch the experiment. Public onboarding remains later work.

## Method and rationale

Use a Dockerfile derived from [the validation image](../containers/validation/Dockerfile), with [setup-toolchain.sh](../tools/setup-toolchain.sh) unchanged. Dependency inputs alone are copied; Codespaces mounts the repository. No CI entrypoint or offline gate is inherited.

| Choice | Reason and limitation |
| --- | --- |
| Build-time installation, not postCreate | Dependency layers can be reused on rebuilds; a source edit does not reinstall the toolchain. A clean first build still downloads and builds everything; no startup speed claim is measured yet. |
| Existing pins | Ubuntu 24.04, Renode 1.16.1, Zephyr v4.1.0, SDK v1.0.1, libcsp v2.1; Python dependencies retain the existing unpinned policy. |
| Non-root developer + venv | HOME-relative setup and Makefile paths agree; avoids modifying system Python. The setup script performs its existing hello_world build. |
| Separate derived Dockerfile | Keeps the validation gate untouched. Review apt/packages and version changes in both definitions together; duplicated dependency declarations are a maintenance cost. |
| Minimal forwarding | Only GUI 3987 (`ports.gui()`) is declared. Automatic port discovery is disabled; no editor extensions, privileged mode, or isolation bypass. Keep port visibility private. |
| x86_64 only | The selected Renode portable archive and SDK host-tools PATH target x86_64. Other architectures fail the build explicitly. |

## Verification checklist

`tools/codespace-verify.sh` writes `codespace-verify-result.log` at the repository root, replacing the previous run. Each item has BEGIN/output/RESULT records, PASS/FAIL/SKIP, exit status on failure, and elapsed seconds. A failure returns nonzero; dependent skipped work is not a pass. `--env-only` is a partial check, even if its summary says PASS. Timeouts receive a further 10-second kill grace period.

| Item | Acceptance / bound | Real Codespace result |
| --- | --- | --- |
| Tools | python3, west, bwrap versions (20s each), Renode version (30s) | Pending |
| Isolation | Repository bubblewrap probe: only lo exists and a loopback connection succeeds (70s) | Pending |
| Renode start | Isolated headless machine creation and clean exit (60s) | Pending |
| Firmware | `make firmware-p1`: COMM/OBC and vulnerable/hardened EPS (1200s) | Pending |
| Representative exercise | EX-B01 via `make exercise` and solver; zero exit and `injected` output (300s), matching test_documented_workflow.py | Pending |
| GUI | Temporary server on 127.0.0.1:3987; HTTP 200 for / and correct /api/config, then cleanup (40s) | Pending |

EX-B01 is the existing documented-workflow test's inexpensive three-machine example. This verifies launch and injection, not the complete vulnerable/hardened behavior suite. `make check` is not run. Failed isolation blocks Renode and the exercise; it never enables an isolation override. The GUI check remains independent and starts no Renode process.

## Owner operation

After the coordinator pushes `feat/devcontainer-foundation`, create a new Codespace from that branch using the repository's **Code → Codespaces → New with options** flow. Choose an x86_64 machine and the repository devcontainer. Note the UTC creation start and terminal-ready times; preserve the creation/build log if the image fails. Keep forwarded ports private.

In the ready terminal, run this once from the repository root:

```bash
bash tools/codespace-verify.sh
```

Paste the full `codespace-verify-result.log` back to the coordinator, together with creation start/ready times, machine CPU/RAM, and any build failure log. A failed build means the script was not run; report that rather than treating it as a script result. The script stops its temporary GUI server. Stop/delete the Codespace after collecting evidence.

## Result record

| Field | Value |
| --- | --- |
| Commit / UTC run date | Pending |
| Machine CPU / RAM | Pending |
| Creation start / terminal ready / elapsed | Pending |
| Image build outcome / log | Pending |
| Script log / exit status | Pending |
| Private forwarded GUI browser observation | Not tested by script |
| Decision / remaining blockers | Pending real-machine evidence |

## Local evidence and unresolved scope

Local syntax, negative environment branches, GUI response, host pytest and document parity are recorded in the completion report. No container runtime build or real Codespace run has been performed in this preparation session. Host results do not establish Codespaces namespace permissions, image build success, startup duration, or exercise success.

The current GUI checks Host/Origin against its local address, while /api/config advertises localhost Renode URLs. Forwarded browser access and browser-to-Renode connectivity remain unverified and may require separate work. This PR does not alter those security or connection semantics. GUI HTTP success inside the container is not proof that the browser experience works. The validation Dockerfile itself remains unbuilt; testing this derived image would not validate its CI entrypoint or offline gate.

Local run on 2026-11-21: syntax/JSON/port mapping passed; document parity: **280 passed**. The requested host suite returned **1231 passed, 135 failed, 17 errors** in 71.69s. This sandbox denies socket creation (`Operation not permitted`), lacks bwrap/west/Renode, and restricts namespace/process inspection. The expected one-failure host baseline was not met. The verifier correctly returned 1 for missing tools and skipped the isolated stages; GUI startup also failed under the socket restriction. No successful local GUI response is claimed.

Local commit is blocked: `git add` cannot create `index.lock` in the main repository's read-only `.git/worktrees/space-cs-sim-wt-devcontainer` directory. Files remain in the worktree for the coordinator to commit in a writable session.

A separate copy of baseline `ba38253` returned **1227 passed, 135 failed, 17 errors** in 67.96s under the same sandbox. Failed/error test IDs match exactly; the four additional passes come from the new document pair. This comparison shows no additional failing tests, not a green host suite.
