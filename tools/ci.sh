#!/usr/bin/env bash
#
# The gate, as one command.
#
# `make check` has always been the gate; what did not exist was anything that ran it without a
# human. Nine places in this repository described gates as CI-enforced while there was no `.github`
# directory at all, and the honest fix has two halves: say so in the documents (done), and build
# the mechanism (this).
#
# This script is the half that can be verified here. It checks the environment, runs the gate, and
# reports. `.github/workflows/check.yml` is a thin caller, so "CI runs the gate" reduces to "the
# workflow invokes this file" - and this file has been run.
#
# WHAT HAS NOT BEEN RUN: the workflow itself. Nobody has watched GitHub Actions execute it, and
# until somebody has, the README says the gate is a command a human types. Do not promote that
# sentence on the strength of a YAML file existing.
#
# Usage:
#   tools/ci.sh                 # environment check, then `make check`
#   tools/ci.sh --env-only      # just the environment check
#   tools/ci.sh --self-test     # prove the environment check can fail
#
# Exit code 0 only if every stage passed.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

MODE=run
case "${1:-}" in
  --env-only)  MODE=env ;;
  --self-test) MODE=selftest ;;
  '')          ;;
  *)           echo "unknown option: $1" >&2; exit 2 ;;
esac

ok()   { printf '  %sOK%s    %s\n'   "$GREEN" "$RESET" "$1"; }
bad()  { printf '  %sMISS%s  %s%s\n' "$RED" "$RESET" "$1" "${2:+  -- $2}"; }

# --------------------------------------------------------------------------- environment
#
# Every one of these has been the cause of a confusing failure at least once. A missing `bc` makes
# probe.sh produce arithmetic errors six times; a missing cmake makes the Zephyr smoke test fail at
# the very end of a long install; the wrong Renode build makes probe.sh fail its own version pin
# with a message about a hash. Naming them here turns each into one line instead of a hunt.
check_env() {
  local missing=0

  printf '%s== environment ==%s\n' "$BOLD" "$RESET"

  for tool in bc make python3 git; do
    if command -v "$tool" >/dev/null; then ok "$tool"; else bad "$tool"; missing=1; fi
  done

  if command -v cc >/dev/null; then
    ok "cc ($(cc --version 2>/dev/null | head -1))"
  else
    bad "cc" "make check compiles the shared codec; see docs/host-setup-without-root.md"
    missing=1
  fi

  if command -v cmake >/dev/null && command -v ninja >/dev/null; then
    ok "cmake + ninja"
  else
    bad "cmake or ninja" "the Zephyr build needs both; pip install cmake ninja works"
    missing=1
  fi

  local renode="${RENODE_DIR:-$HOME/tools/renode_1.16.1-dotnet_portable}"
  if [ -x "$renode/renode" ]; then
    local ver
    ver="$("$renode/renode" --version 2>&1 | head -1)"
    if echo "$ver" | grep -q '1\.16\.1'; then
      ok "Renode 1.16.1 ($renode)"
    else
      bad "Renode version" "found '$ver'; probe.sh asserts 1.16.1 and its exact build string"
      missing=1
    fi
  else
    bad "Renode" "not at $renode; set RENODE_DIR"
    missing=1
  fi

  # A containment backend. Every Renode launch goes into a loopback-only network namespace and
  # refuses to start without one, so its absence stops the gate before a single node boots - and
  # the message it stops with is about namespaces, which reads like a defect if you do not know
  # this is required. Name it here instead.
  if PYTHONPATH="$REPO/src" python3 -m cuberange.safety.isolate --probe 2>/dev/null | grep -q '^  OK'; then
    ok "network isolation ($(PYTHONPATH="$REPO/src" python3 -m cuberange.safety.isolate --probe \
          2>/dev/null | awk '/^  OK/{print $2; exit}' | tr -d ':'))"
  else
    bad "network isolation" "no working backend; apt install bubblewrap, or see SAFE_USE.md"
    missing=1
  fi

  if python3 -c "import spacepackets, crcmod" 2>/dev/null; then
    ok "conformance oracles (spacepackets, crcmod)"
  else
    bad "spacepackets or crcmod" "pip install -r requirements.txt"
    missing=1
  fi

  # The committed vectors, which the conformance layer reads directly. Without them that layer is
  # gone, and it went missing once before by exactly this route.
  local golden=0
  local want="crc.json csp.json pus.json space_packet.json transfer_frame.json"
  local n=0
  for f in $want; do
    n=$((n + 1))
    [ -f "$REPO/tests/golden/$f" ] || { bad "tests/golden/$f" "run make golden"; golden=1; }
  done
  # The count is derived from the list rather than written beside it. It said "4 files" while the
  # list had grown to five, which is a small lie of exactly the kind this project keeps finding.
  [ "$golden" = 0 ] && ok "golden vectors ($n files)"
  [ "$golden" = 0 ] || missing=1

  if [ -f "${ENV_FILE:-$HOME/cuberange-env.sh}" ]; then
    ok "Zephyr environment ($(basename "${ENV_FILE:-$HOME/cuberange-env.sh}"))"
  else
    bad "Zephyr environment" "run ./tools/setup-toolchain.sh"
    missing=1
  fi

  return "$missing"
}

# --------------------------------------------------------------------------- self-test
#
# A gate that cannot fail is not evidence - probe.sh section Z, the firmware pair gate and the
# Monitor concurrency tests all carry this, and so does this. The environment check is the only
# logic here worth testing; `make check` tests itself.
self_test() {
  printf '%s== self-test: the environment check must detect a broken environment ==%s\n' \
         "$BOLD" "$RESET"
  local failures=0

  # A Renode that is not there.
  if ( RENODE_DIR=/nonexistent check_env >/dev/null 2>&1 ); then
    printf '  %sFAIL%s  a missing Renode was NOT detected\n' "$RED" "$RESET"; failures=1
  else
    printf '  %sPASS%s  rejected: Renode missing\n' "$GREEN" "$RESET"
  fi

  # A Zephyr environment file that is not there.
  if ( ENV_FILE=/nonexistent/cuberange-env.sh check_env >/dev/null 2>&1 ); then
    printf '  %sFAIL%s  a missing Zephyr environment was NOT detected\n' "$RED" "$RESET"; failures=1
  else
    printf '  %sPASS%s  rejected: Zephyr environment missing\n' "$GREEN" "$RESET"
  fi

  # A host with no containment backend. PATH is emptied so neither bwrap nor unshare is found;
  # the probe then reports both as "not installed" and the check must refuse.
  if ( PATH=/nonexistent check_env >/dev/null 2>&1 ); then
    printf '  %sFAIL%s  a host with no isolation backend was NOT detected\n' "$RED" "$RESET"
    failures=1
  else
    printf '  %sPASS%s  rejected: no network-isolation backend\n' "$GREEN" "$RESET"
  fi

  # And a good environment must still pass, or this is a rejector rather than a check.
  if check_env >/dev/null 2>&1; then
    printf '  %sPASS%s  accepted: this host\n' "$GREEN" "$RESET"
  else
    printf '  %sFAIL%s  this host was rejected; run tools/ci.sh --env-only to see why\n' \
           "$RED" "$RESET"
    failures=1
  fi

  echo
  if [ "$failures" = 0 ]; then
    printf '%sself-test passed%s\n' "$GREEN" "$RESET"
    return 0
  fi
  printf '%sself-test FAILED: the environment check is not evidence%s\n' "$RED" "$RESET"
  return 1
}

# --------------------------------------------------------------------------- main
cd "$REPO"

case "$MODE" in
  selftest) self_test; exit $? ;;
  env)      check_env; exit $? ;;
esac

if ! check_env; then
  echo
  printf '%sthe environment is incomplete; the gate would fail for reasons that are not defects%s\n' \
         "$RED" "$RESET"
  echo "see docs/host-setup-without-root.md for a path that needs no package manager"
  exit 1
fi

echo
printf '%s== make check ==%s\n' "$BOLD" "$RESET"
started=$(date +%s)
if make check; then
  elapsed=$(( $(date +%s) - started ))
  echo
  printf '%sCI PASSED%s in %dm%02ds\n' "$GREEN" "$RESET" $((elapsed / 60)) $((elapsed % 60))
  exit 0
fi

elapsed=$(( $(date +%s) - started ))
echo
printf '%sCI FAILED%s after %dm%02ds\n' "$RED" "$RESET" $((elapsed / 60)) $((elapsed % 60))
printf '%sthe last line of a passing run is CHECK PASSED; if you did not see it, it did not pass%s\n' \
       "$YELLOW" "$RESET"
exit 1
