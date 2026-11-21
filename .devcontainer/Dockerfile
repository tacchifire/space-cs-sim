# PR-1 draft: derived from containers/validation/Dockerfile; not yet built.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      bc build-essential python3 python3-pip python3-dev python3-venv \
      libicu-dev libssl3 bubblewrap git curl ca-certificates xz-utils file \
      device-tree-compiler gperf sudo \
    && rm -rf /var/lib/apt/lists/*

# Keep the setup script's HOME-relative paths usable by the interactive user.
RUN useradd -m -s /bin/bash developer \
    && echo 'developer ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/developer \
    && chmod 0440 /etc/sudoers.d/developer
USER developer
WORKDIR /home/developer
RUN python3 -m venv /home/developer/venv
ENV PATH="/home/developer/venv/bin:/home/developer/zephyr-sdk/sysroots/x86_64-pokysdk-linux/usr/bin:$PATH" \
    RENODE_DIR=/home/developer/tools/renode_1.16.1-dotnet_portable

# Only dependency inputs enter the image; Codespaces mounts the checkout.
COPY --chown=developer:developer requirements.txt requirements-flatsat.txt /tmp/deps/
RUN pip install -r /tmp/deps/requirements.txt cmake ninja west
RUN test "$(uname -m)" = x86_64 \
    && curl -fsSL -o /tmp/renode.tar.gz \
      https://github.com/renode/renode/releases/download/v1.16.1/renode-1.16.1.linux-portable-dotnet.tar.gz \
    && mkdir -p /home/developer/tools \
    && tar xzf /tmp/renode.tar.gz -C /home/developer/tools \
    && rm /tmp/renode.tar.gz
COPY --chown=developer:developer tools/setup-toolchain.sh /tmp/setup-toolchain.sh
RUN bash /tmp/setup-toolchain.sh \
    && git clone --depth 1 --branch v2.1 https://github.com/libcsp/libcsp /home/developer/libcsp \
    && pip install -r /home/developer/zephyrproject/zephyr/scripts/requirements.txt \
    && rm -rf /tmp/cuberange-smoke
CMD ["sleep", "infinity"]
