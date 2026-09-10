#!/usr/bin/env bash
#
# Build the two INDEPENDENT ORACLES that tools/gen_golden.py needs.
#
# The golden vectors in tests/golden/ exist so that this project's codecs are checked against
# somebody else's implementation rather than against a second copy of their own misreading. That
# only holds if the oracles can be rebuilt: `gen_golden.py` called `./csp_oracle` and
# `./cryptolib/build/libcryptolib.so`, neither of which was in the repository or documented
# anywhere, so the vectors were unreproducible and the independence was an assertion. The handoff
# document had already recorded the gap.
#
#   csp_oracle          libcsp (MIT) itself, printing the CSP v1 header and CFP-over-CAN frames
#   libcryptolib.so     NASA CryptoLib (NOSA 1.3), for Crypto_Calc_FECF as a fourth CRC oracle
#
# NEITHER IS VENDORED. Both are cloned and built here, outside the tree, because the licence policy
# (README, section 11) permits them as external oracles and not as dependencies. Nothing this
# builds is committed except the JSON they produce.
#
# Usage:
#   tools/oracles/build.sh [work_dir]        # default: /tmp/cuberange-oracles
#
# Then:
#   cd <work_dir> && python3 <repo>/tools/gen_golden.py <repo>/tests/golden
set -euo pipefail

WORK="${1:-/tmp/cuberange-oracles}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIBCSP="${LIBCSP:-$HOME/libcsp}"
CC="${CC:-cc}"

say() { printf '\033[1m==> %s\033[0m\n' "$1"; }

mkdir -p "$WORK"
cd "$WORK"

# --------------------------------------------------------------------------- libcsp oracle
say "csp_oracle (libcsp $LIBCSP)"
[ -d "$LIBCSP" ] || {
  echo "libcsp not found at $LIBCSP. Clone it first:" >&2
  echo "  git clone --depth 1 --branch v2.1 https://github.com/libcsp/libcsp $LIBCSP" >&2
  exit 1
}

# The source list is a glob with exclusions rather than an enumeration. Naming twenty files by
# hand broke twice on this one clone: csp_if_lo.c and csp_hmac.c were missing and only turned up as
# link errors, and libcsp moves files between point releases. Excluding what needs an external
# dependency is a shorter and more durable statement than listing what does not.
#
#   zmqhub    needs libzmq        udp/tun/eth   need a socket stack this oracle never opens
#   socketcan needs libsocketcan  bindings      need Python headers
#   yaml      needs libyaml
mapfile -t CSP_SRC < <(
  find "$LIBCSP/src" -name '*.c' \
    -not -path '*/bindings/*' \
    -not -path '*/drivers/*' \
    -not -path '*/arch/freertos/*' -not -path '*/arch/zephyr/*' -not -path '*/arch/windows/*' \
    -not -name 'csp_if_zmqhub.c' -not -name 'csp_if_udp.c' \
    -not -name 'csp_if_tun.c' -not -name 'csp_if_eth*.c' \
    -not -name 'csp_if_i2c.c' -not -name 'csp_if_kiss.c' \
    -not -name 'csp_yaml.c' \
    | sort)
[ "${#CSP_SRC[@]}" -gt 15 ] || {
  echo "only ${#CSP_SRC[@]} libcsp sources found under $LIBCSP/src - is that a full clone?" >&2
  exit 1
}
echo "    compiling ${#CSP_SRC[@]} libcsp sources"

# libcsp's headers include "csp/autoconfig.h", which its own build generates and which therefore
# does not exist in a source clone. Let its CMake produce one rather than hand-writing a header
# whose contents decide the protocol version - the whole point of this oracle is that libcsp
# chooses the layout, not us.
if [ ! -f "$WORK/csp-config/include/csp/autoconfig.h" ]; then
  mkdir -p "$WORK/csp-config"
  ( cd "$WORK/csp-config" && cmake "$LIBCSP" -DCMAKE_C_COMPILER="$CC" \
      -DCMAKE_CXX_COMPILER="${CXX:-c++}" >/dev/null )
fi
CSP_AUTOCONF="$WORK/csp-config/include"
# -Wno-date-time: libcsp compiles csp_service_handler.c with -Werror,-Wdate-time under clang
# because it stamps __DATE__/__TIME__ into the CMP identity response. Nothing here reads that
# response, and the warning is about build reproducibility rather than correctness.
"$CC" -O1 -std=gnu11 -Wno-date-time -o "$WORK/csp_oracle" \
  "$REPO/tools/oracles/csp_oracle.c" "${CSP_SRC[@]}" \
  -I"$LIBCSP/include" -I"$LIBCSP/src" -I"$CSP_AUTOCONF" -I"$REPO/tools/oracles" -lpthread
echo "    $WORK/csp_oracle"

# --------------------------------------------------------------------------- NASA CryptoLib
say "libcryptolib.so (NASA CryptoLib, NOSA 1.3 - external oracle, not vendored)"
# Rebuild when the library is absent OR when it predates the options tc_oracle needs. A checkout
# that already had a libcryptolib.so from before those options existed would otherwise be skipped
# here and then segfault inside tc_oracle - the failure would look like a bug in the oracle rather
# than a stale build, which is the same confusion the firmware pair gate's provenance check exists
# to prevent.
if [ ! -f "$WORK/cryptolib/build/libcryptolib.so" ] || \
   ! nm -D --defined-only "$WORK/cryptolib/build/libcryptolib.so" 2>/dev/null \
     | grep -q 'get_mc_interface_disabled'; then
  rm -rf "$WORK/cryptolib/build"
  [ -d "$WORK/cryptolib" ] || git clone --depth 1 https://github.com/nasa/CryptoLib "$WORK/cryptolib"
  mkdir -p "$WORK/cryptolib/build"
  ( cd "$WORK/cryptolib/build"
    # -Wno-self-assign in the CONFIG-specific slot, because CryptoLib appends -Werror to
    # CMAKE_C_FLAGS after ours and its sources contain deliberate self-assignments that clang
    # rejects and gcc does not. CryptoLib's own ENABLE_FUZZING path does the same thing for afl.
    #
    # The four *_INTERNAL/_DISABLED options are what tc_oracle needs, and each was added because
    # its absence produced a segfault rather than a message:
    #
    #   KEY_INTERNAL   - without it Crypto_Init leaves a stub key interface and dies.
    #   SA_INTERNAL    - the Security Association store is walked after the header is parsed.
    #   MC_DISABLED    - the interesting one. The INTERNAL monitoring interface logs through an
    #                    fprintf to a FILE* that Crypto_Init opens; on the ordinary path where a
    #                    frame has no managed parameters, mc_log fired with that handle still NULL
    #                    and libc segfaulted. The disabled interface is a no-op, and logging is not
    #                    part of what the oracle measures.
    #   MC_INTERNAL    - kept so the choice above is a choice and not the only option compiled in.
    #
    # Not enabled: CRYPTO_LIBGCRYPT. Crypto_Init_TC_Unit_Test asks for it, gcrypt.h is not on this
    # host and installing it needs root, and a cryptography backend has nothing to do with reading
    # a primary header - so tc_oracle configures the parse path directly instead of using that
    # helper. See its header comment.
    cmake .. -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
             -DKEY_INTERNAL=ON -DMC_INTERNAL=ON -DMC_DISABLED=ON -DSA_INTERNAL=ON \
             -DCMAKE_C_FLAGS_RELEASE="-O2 -Wno-self-assign -Wno-unused-but-set-variable" >/dev/null
    make -j"$(nproc)" >/dev/null )
fi
echo "    $WORK/cryptolib/build/libcryptolib.so"

# --------------------------------------------------------------------------- TC header oracle
say "tc_oracle (TC transfer frame primary header, parsed by CryptoLib)"
"$CC" -O2 -I"$WORK/cryptolib/include" -I"$WORK/cryptolib/build/include" \
      -o "$WORK/tc_oracle" "$REPO/tools/oracles/tc_oracle.c" \
      -L"$WORK/cryptolib/build" -lcryptolib -Wl,-rpath,"$WORK/cryptolib/build"
echo "    $WORK/tc_oracle"

say "done"
echo
echo "Generate the vectors with:"
echo "  cd $WORK && python3 $REPO/tools/gen_golden.py $REPO/tests/golden"
