# Validation container

A container definition for the gate CI runs, so the toolchain is fixed in
one place and shared between CI and a local machine, instead of existing
only as a workflow's cache keys.

## Contents

| File | What it is |
| --- | --- |
| `Dockerfile` | Ubuntu 24.04 with the workflow's packages, Renode 1.16.1, and the Zephyr toolchain installed by `tools/setup-toolchain.sh` |

## Equivalence with CI

- Same entry point: `tools/ci.sh`, which runs `make check`.
- Same pins: Zephyr v4.1.0, SDK v1.0.1, Renode 1.16.1, libcsp v2.1.
- Same split: fetching and building may use the network; the gate run may
  not. In the workflow these are two jobs; here they are two runs of one
  image, the second with the network off.

## Usage

```sh
docker build -t cuberange-validation -f containers/validation/Dockerfile .
docker run --rm cuberange-validation --env-only
docker run --rm --network none cuberange-validation
```

## Runtime requirements

- Unprivileged user namespaces must work, or no isolation backend works and
  the gate refuses to start: on an Ubuntu 24.04 host that means the
  `kernel.apparmor_restrict_unprivileged_userns` knob, or running the
  container privileged.
- Disk: the toolchain alone is roughly 7 GB; the image is large by design.
- The base image is pinned by tag, not yet by digest. Digest pinning is an
  open point, to be fixed the first time the image is actually built.

## Status

- **Not built, not run.** The machine this definition was written on has no
  container runtime — `docker`, `podman`, and `buildah` are all absent — so
  the build could not even be attempted here. The definition is unverified.
- Until it is built and a gate run inside it is observed, the equivalent
  verification is the existing CI: `.github/workflows/check.yml`, which runs
  the same `tools/ci.sh` on `ubuntu-latest`. A branch becomes verified by
  opening its pull request and reading the run's log against the commit SHA,
  not by this file existing.
