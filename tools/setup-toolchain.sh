#!/usr/bin/env bash
#
# CubeRange firmware toolchain setup.
#
# This is the exact path that was measured to work, including the two dead ends that cost time.
# It needs no root and no Docker.
#
#   Zephyr        v4.1.0        (NOT main - see PYTHON below)
#   Zephyr SDK    v1.0.1        arm-zephyr-eabi only, fetched as split release assets
#   Renode        1.16.1        portable, installed separately (see tools/renode-probe/probe.sh)
#
# PYTHON: Zephyr main requires Python >= 3.12 and fails at cmake time with
#   "Could NOT find Python3 (missing: Python3_EXECUTABLE Interpreter) (Required is at least
#    version 3.12)"
# on a host with 3.10. Checking cmake/modules/python.cmake per tag gives the real floor:
#   main -> 3.12, v4.1.0 -> 3.10, v3.7.0 -> 3.8.
# v4.1.0 is therefore the newest release usable on Ubuntu 22.04's stock Python 3.10.
# Do not "upgrade to main" without also pinning a newer Python.
#
# SDK: `west sdk install` runs Zephyr's own cmake and so hits the same Python floor. Fetching the
# release assets directly avoids that entirely, and the split assets are small - 164 MB for host
# tools plus the ARM toolchain, versus a ~10 GB official Docker image.
#
# Disk cost, re-measured 2026-08-30: zephyrproject 5.8 GB, zephyr-sdk 2.0 GB. The earlier note
# here said 6.8 GB and 829 MB; the SDK figure was wrong by 2.4x. The shallow-manifest change
# below takes another ~856 MB off the workspace, so expect roughly 5.0 GB + 2.0 GB now.
#
# Usage:  ./tools/setup-toolchain.sh [workspace_dir] [sdk_dir]
set -euo pipefail

WS="${1:-$HOME/zephyrproject}"
SDK="${2:-$HOME/zephyr-sdk}"
ZEPHYR_TAG="v4.1.0"
SDK_TAG="v1.0.1"
SDK_BASE="https://github.com/zephyrproject-rtos/sdk-ng/releases/download/${SDK_TAG}"

# Host architecture. Only the HOST half of the SDK asset names varies; the target stays
# arm-zephyr-eabi, and both host builds unpack to the identical arm-zephyr-eabi/bin layout, so
# CROSS_COMPILE below is arch-independent. Fail loudly rather than guessing on anything else.
case "$(uname -m)" in
  x86_64)  SDK_HOST=x86_64 ;;
  aarch64) SDK_HOST=aarch64 ;;
  *)       echo "unsupported host architecture: $(uname -m)" >&2; exit 1 ;;
esac

say() { printf '\033[1m==> %s\033[0m\n' "$1"; }

PIP_ERR="$(mktemp)"
trap 'rm -f "$PIP_ERR"' EXIT

say "python: $(python3 --version)"
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    sys.exit("Zephyr v4.1.0 needs Python >= 3.10")
print(f"    ok ({sys.version_info.major}.{sys.version_info.minor})")
PY

say "west"
# Debian 12+ (so current Raspberry Pi OS) marks the system Python externally-managed per PEP 668
# and pip refuses to install into it at all. Under `set -e` that aborts here, on the first install,
# with a wall of pip text. Say what to do instead - and do not reach for --break-system-packages,
# which does what it says to the OS's own Python.
if ! python3 -m pip install --quiet --upgrade west 2>"$PIP_ERR"; then
  if grep -q 'externally-managed-environment' "$PIP_ERR"; then
    cat >&2 <<'MSG'

This Python is externally managed (PEP 668), so pip will not install into it.
Use a virtual environment, then run this script again from inside it:

    python3 -m venv ~/cuberange-venv
    . ~/cuberange-venv/bin/activate
    ./tools/setup-toolchain.sh

MSG
    exit 1
  fi
  cat "$PIP_ERR" >&2
  exit 1
fi
export PATH="$HOME/.local/bin:$PATH"
west --version

if [ ! -d "$WS/.west" ]; then
  say "west init -> $WS ($ZEPHYR_TAG)"
  mkdir -p "$WS"
  # Clone the manifest repo shallowly ourselves, then `west init -l` to adopt it in place.
  #
  # `west init -m <url> --mr <tag>` clones the manifest repository in FULL, and --narrow
  # -o=--depth=1 on the update below only shallows the *projects*, not the manifest repo. Measured
  # on this tree: zephyr/.git is 952 MB after `west init -m`, and 96 MB by this route - the same
  # v4.1.0 working tree either way, 856 MB cheaper. Verified that `west list` resolves the full
  # manifest from the shallow clone afterwards.
  git clone --depth 1 --branch "$ZEPHYR_TAG" \
    https://github.com/zephyrproject-rtos/zephyr "$WS/zephyr"
  ( cd "$WS" && west init -l zephyr )
else
  say "workspace already initialised at $WS"
fi

say "west update (narrow, shallow)"
( cd "$WS" && west update --narrow -o=--depth=1 )

say "zephyr python requirements"
python3 -m pip install --quiet -r "$WS/zephyr/scripts/requirements.txt" || {
  echo "    (pip reported dependency conflicts; these were benign in the measured run)"
}

if [ ! -x "$SDK/arm-zephyr-eabi/bin/arm-zephyr-eabi-gcc" ]; then
  say "Zephyr SDK $SDK_TAG for $SDK_HOST (arm-zephyr-eabi + host tools, ~160 MB download)"
  mkdir -p "$SDK"
  ( cd "$SDK"
    for a in "hosttools_linux-${SDK_HOST}.tar.xz" \
             "toolchain_gnu_linux-${SDK_HOST}_arm-zephyr-eabi.tar.xz"; do
      echo "    fetching $a"
      curl -fsSL -o "$a" "$SDK_BASE/$a"
      tar xf "$a"
      rm -f "$a"
    done
    # Self-extracting host tools installer; -y accepts the prompt, -d sets the destination.
    # This MUST be a hard failure. When the name was hardcoded to x86_64, an aarch64 run
    # downloaded the host tools, unpacked them, skipped this branch because the test was simply
    # false, and exited 0 with the SDK half-installed and nothing printed. The gcc existence guard
    # above then made a re-run a no-op, so the damage was also unrepairable by retrying.
    installer="zephyr-sdk-${SDK_HOST}-hosttools-standalone-0.10.sh"
    [ -f "$installer" ] || { echo "host tools installer not found: $installer" >&2; exit 1; }
    sh "./$installer" -y -d "$SDK" >/dev/null )
else
  say "SDK already present at $SDK"
fi
"$SDK/arm-zephyr-eabi/bin/arm-zephyr-eabi-gcc" --version | head -1

cat > "$WS/../cuberange-env.sh" <<EOF
# source this before building CubeRange firmware
export PATH="\$HOME/.local/bin:\$PATH"
export ZEPHYR_BASE="$WS/zephyr"
# The SDK is used through the cross-compile variant rather than Zephyr's SDK discovery, because
# the split release assets do not ship the sdk_version / cmake files FindZephyr-sdk.cmake expects.
export ZEPHYR_TOOLCHAIN_VARIANT=cross-compile
export CROSS_COMPILE="$SDK/arm-zephyr-eabi/bin/arm-zephyr-eabi-"
EOF

say "smoke test: hello_world for nucleo_h753zi"
# shellcheck disable=SC1090
. "$WS/../cuberange-env.sh"
rm -rf /tmp/cuberange-smoke
west build -p always -b nucleo_h753zi -d /tmp/cuberange-smoke "$WS/zephyr/samples/hello_world" >/dev/null
ls -l /tmp/cuberange-smoke/zephyr/zephyr.elf

say "done. environment written to $(cd "$WS/.." && pwd)/cuberange-env.sh"
echo
echo "Verify it end to end with:"
echo "  cd \$RENODE_DIR && ./renode --disable-xwt --console --plain --hide-log \\"
echo "    -e 'mach create \"SAT\"' \\"
echo "    -e 'machine LoadPlatformDescription @platforms/boards/nucleo_h753zi.repl' \\"
echo "    -e 'sysbus LoadELF @/tmp/cuberange-smoke/zephyr/zephyr.elf' \\"
echo "    -e 'sysbus.usart3 CreateFileBackend @/tmp/out.txt true' \\"
echo "    -e 'emulation SetGlobalQuantum \"0.002\"' \\"
echo "    -e 'emulation SetGlobalAdvanceImmediately true' \\"
echo "    -e 'emulation RunFor \"3\"' -e 'quit'"
echo "  # expect: *** Booting Zephyr OS build v4.1.0 ***  /  Hello World! nucleo_h753zi/stm32h753xx"
