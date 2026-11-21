# CubeRange validation image.
#
# The same gate CI runs (.github/workflows/check.yml -> tools/ci.sh -> make check),
# as a container definition, so a local run and a CI run stop being two
# environments that happen to agree. Versions are pinned to the ones the
# toolchain setup script and the workflow already use:
#
#   Zephyr v4.1.0, Zephyr SDK v1.0.1 (via tools/setup-toolchain.sh)
#   Renode 1.16.1 portable (URL embeds size and SHA1; Renode refuses a
#     download that does not match, so the URL is the integrity pin)
#   libcsp v2.1
#
# STATUS: NOT BUILT. The machine this was written on has no container runtime
# (docker, podman and buildah are all absent), so this file is an unverified
# definition, not evidence. See README.md for the runtime requirements and
# for the CI procedure that currently provides the equivalent verification.
#
# The workflow's split is kept: a fetch/build stage that may reach the
# network, and the gate itself, which must not. In container terms the gate
# run is `--network none`; everything it needs must already be in the image
# or in the artifacts the first stage produced.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    HOME=/root \
    OUT=/tmp/cuberange-ci

# The workflow's apt list, plus the Zephyr host tools a bare image lacks and
# the GitHub runner image happens to preinstall (dtc, gperf). bc is not
# optional: probe.sh's arithmetic is six calls to it. bubblewrap is the
# network-isolation backend every Renode launch requires.
RUN apt-get update && apt-get install -y --no-install-recommends \
      bc build-essential python3 python3-pip python3-dev \
      libicu-dev libssl3 bubblewrap \
      git curl ca-certificates xz-utils file \
      device-tree-compiler gperf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /cuberange
COPY . /cuberange

# west and the build drivers live in the interpreter. The workflow learned
# three times that a cache of artifacts cannot restore them, which is why CI
# installs them on every run; an image has no such cache, so installing them
# at build time is the equivalent step.
RUN pip install -r requirements.txt cmake ninja west

# Renode 1.16.1, to the path tools/ci.sh probes by default.
RUN curl -fsSL -o /tmp/renode.tar.gz \
      https://github.com/renode/renode/releases/download/v1.16.1/renode-1.16.1.linux-portable-dotnet.tar.gz \
    && mkdir -p /root/tools && tar xzf /tmp/renode.tar.gz -C /root/tools \
    && rm /tmp/renode.tar.gz

# The toolchain itself: Zephyr workspace, SDK, and the environment file
# tools/ci.sh checks for, all at the script's default locations.
RUN ./tools/setup-toolchain.sh \
    && git clone --depth 1 --branch v2.1 https://github.com/libcsp/libcsp /root/libcsp \
    && pip install -r /root/zephyrproject/zephyr/scripts/requirements.txt

ENV PATH="/root/zephyr-sdk/sysroots/x86_64-pokysdk-linux/usr/bin:$PATH"

# The gate. Run the fetch/build stage with the network up, then this with
# `--network none`; see README.md.
ENTRYPOINT ["./tools/ci.sh"]
