# Building CubeRange on a host with no root

The setup in [README.md](../README.md) starts with `sudo apt install build-essential` and
`pip install`. On a locked-down workstation you have neither. This is the path that was actually
walked on such a machine — Ubuntu 26.04 with no `sudo`, no `gcc`, no `pip`, no `make` and no
Docker — recorded because working it out took longer than following it will.

Everything below installs under `$HOME`. Nothing needs a package manager.

## What the machine did have

Worth checking first, because two of these are easy to assume are missing and are not:

```bash
for c in bc busybox git curl python3 ld as ar; do printf '%-10s' "$c"; command -v $c || echo '--'; done
ldconfig -p | grep -cE 'libicu|libssl'      # Renode's bundled .NET dlopens both at startup
```

`bc` is not optional — `probe.sh` calls it six times. `libicu` and OpenSSL 3 are dlopened by the
.NET runtime Renode ships with. If those three are absent and you cannot install them, stop here;
the rest will not help.

## 1. Python

The system Python may be newer than Zephyr v4.1.0 supports. On the measured host it was 3.14, and
Zephyr v4.1.0 is pinned precisely because of Python floors — see the header of
`tools/setup-toolchain.sh`. `uv` downloads a standalone interpreter, so this needs no compiler:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv python install 3.12
uv venv --seed --python 3.12 ~/.venvs/cuberange
uv pip install --python ~/.venvs/cuberange/bin/python \
    pytest spacepackets crcmod cmake ninja
```

`--seed` is not decoration. Without it the venv has no `pip` module, and `setup-toolchain.sh` dies
on its first install with `No module named pip`.

`cmake` and `ninja` come from PyPI wheels. `setup-toolchain.sh` does not install them and its final
smoke test fails with `CMake is not installed or cannot be found` if they are missing.

## 2. A C compiler

`tests/native` and `make check` need a host compiler. Zig ships one as a single static tarball:

```bash
ZIG=0.16.0
curl -sSL -o /tmp/zig.tar.xz "https://ziglang.org/download/$ZIG/zig-x86_64-linux-$ZIG.tar.xz"
mkdir -p ~/.local/opt && tar xf /tmp/zig.tar.xz -C ~/.local/opt
ln -sf ~/.local/opt/zig-x86_64-linux-$ZIG/zig ~/.local/bin/zig

printf '#!/bin/sh\nexec %s/.local/bin/zig cc "$@"\n' "$HOME" > ~/.local/bin/cc
chmod +x ~/.local/bin/cc
```

`zig cc` is clang with a bundled sysroot, and it compiled both the codec suite and GNU Make itself
without complaint.

## 3. GNU Make

Everything in this repository is driven by `make`. Build it with the compiler from step 2:

```bash
curl -sSL -o /tmp/make.tar.gz https://ftp.gnu.org/gnu/make/make-4.4.1.tar.gz
tar xzf /tmp/make.tar.gz -C /tmp && cd /tmp/make-4.4.1
CC="$HOME/.local/bin/zig cc" ./configure --prefix="$HOME/.local" --disable-dependency-tracking
sh ./build.sh && ./make install
```

`build.sh` is make's own bootstrap script, for exactly this situation.

## 4. Renode, Zephyr, libcsp

As in the README, except that `setup-toolchain.sh` must run from inside the venv:

```bash
case "$(uname -m)" in
  x86_64)  ASSET=renode-1.16.1.linux-portable-dotnet.tar.gz ;;
  aarch64) ASSET=renode-1.16.1.linux-arm64-portable-dotnet.tar.gz ;;
esac
curl -L -o /tmp/renode.tar.gz \
  "https://github.com/renode/renode/releases/download/v1.16.1/$ASSET"
mkdir -p ~/tools && tar xzf /tmp/renode.tar.gz -C ~/tools

. ~/.venvs/cuberange/bin/activate
./tools/setup-toolchain.sh
git clone --depth 1 --branch v2.1 https://github.com/libcsp/libcsp ~/libcsp
```

## 5. One file to source

```bash
cat > ~/cuberange-host-env.sh <<'EOF'
export PATH="$HOME/.local/bin:$PATH"
. "$HOME/.venvs/cuberange/bin/activate"
. "$HOME/cuberange-env.sh"
export PATH="$HOME/zephyr-sdk/sysroots/x86_64-pokysdk-linux/usr/bin:$PATH"
export RENODE_DIR="$HOME/tools/renode_1.16.1-dotnet_portable"
export LIBCSP="$HOME/libcsp"
EOF
```

The Zephyr SDK's `sysroots/.../usr/bin` carries `dtc` and the other host tools the build needs.

Then:

```bash
. ~/cuberange-host-env.sh
make probe
make check
```

## Measured on this path

Recorded so a future run can tell a real regression from a slow machine:

| | |
| --- | --- |
| `make probe` | 39 pass, 0 fail, 4 skip, about 6 minutes on a quiet host |
| Four-node speed | 4.50x real time (8-core x86-64; the design's figure from a 14-core host was 2.34x) |
| `make check` | about 10 minutes quiet |
| Disk | Zephyr workspace 4.5 GB, SDK 2.0 GB, Renode 200 MB |

**Do not run other Renode work while the probe runs.** The same probe took 35 minutes with two
other Renode workloads on the box. The design's R22 suspects the reported 13% hang rate was itself
a contention artifact, and this is consistent with that.

## What still does not work here

- **SocketCAN and Wireshark integration.** Both need root, and `probe.sh` correctly reports them as
  SKIP rather than failing. They are optional by design.
- **NASA CryptoLib as a fourth CRC oracle.** `tools/gen_golden.py` expects
  `./cryptolib/build/libcryptolib.so`, which is not vendored and has not been built on this host.
