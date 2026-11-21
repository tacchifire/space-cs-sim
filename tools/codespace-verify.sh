#!/usr/bin/env bash
# One bounded verification run. --env-only never launches Renode or builds firmware.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 2
MODE=run
case "${1:-}" in '') ;; --env-only) MODE=env ;; *) echo 'usage: tools/codespace-verify.sh [--env-only]' >&2; exit 2 ;; esac
LOG="$REPO/codespace-verify-result.log"
# Do not inherit an isolation override from an interactive shell.
unset CUBERANGE_ALLOW_UNISOLATED CUBERANGE_NETWORK_ISOLATED
export PYTHONPATH="$REPO/src"
export RENODE_DIR="${RENODE_DIR:-$HOME/tools/renode_1.16.1-dotnet_portable}"
TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT
exec > >(tee "$LOG") 2>&1
failed=0
started=$SECONDS
printf 'META date=%s commit=%s mode=%s arch=%s\n' "$(date -u +%FT%TZ)" "$(git rev-parse HEAD)" "$MODE" "$(uname -m)"
result() { printf 'RESULT\t%s\t%s\t%s\n' "$1" "$2" "$3"; }
check() {
    local name=$1 limit=$2 begin=$SECONDS rc
    shift 2
    printf 'BEGIN\t%s\tlimit=%ss\n' "$name" "$limit"
    timeout --kill-after=10s "$limit" "$@" > "$TMP/output" 2>&1
    rc=$?
    cat "$TMP/output"
    if [ "$rc" = 0 ]; then
        result "$name" PASS "elapsed=$((SECONDS-begin))s"
    else
        result "$name" FAIL "exit=$rc elapsed=$((SECONDS-begin))s (124/137 indicates timeout)"
        failed=1
    fi
    return "$rc"
}
version() {
    local executable=$1
    command -v "$executable" || { echo "missing executable: $executable"; return 1; }
    "$executable" --version
}
# Export functions only for bounded child shells, not interactive startup files.
export -f version
for tool in python3 west bwrap; do
    check "$tool" 20 bash -c 'version "$1"' -- "$tool" || :
done
check renode-version 30 bash -c '
    version=$("$RENODE_DIR/renode" --version) || exit $?
    printf "%s\n" "$version"
    [[ "$version" == *1.16.1* ]]
' || :
if [ "$MODE" = env ]; then
    for item in isolation renode-start firmware exercise gui; do result "$item" SKIP 'env-only: not tested'; done
else
    # Test bubblewrap specifically, using the project's interface + loopback probe.
    check isolation 70 python3 -c '
from cuberange.safety.isolate import BACKENDS, probe
ok, detail = probe(*BACKENDS[0])
print(detail)
raise SystemExit(0 if ok else 1)
'
    isolated=$?
    if [ "$isolated" = 0 ]; then
        check renode-start 60 python3 -m cuberange.safety.isolate -- \
            bash -c 'cd "$RENODE_DIR" && ./renode --disable-xwt --console --plain --hide-log -e "mach create" -e "quit" < /dev/null' || :
        check firmware 1200 make firmware-p1
        built=$?
        if [ "$built" = 0 ]; then
            # Same representative exercise and assertion as test_documented_workflow.py.
            check exercise 300 bash -c '
                set -o pipefail
                make exercise EX=EX-B01-eps-killswitch RUN="python3 exercises/EX-B01-eps-killswitch/solve.py" | tee "$1"
                rc=${PIPESTATUS[0]}
                [ "$rc" = 0 ] && grep -q injected "$1"
            ' -- "$TMP/exercise" || :
            out=$(make -s out)
            if [ -f "$out/EX-B01-eps-killswitch-renode.log" ]; then
                printf 'DETAIL\texercise\tRenode log (last 80 lines)\n'
                tail -n 80 "$out/EX-B01-eps-killswitch-renode.log"
            fi
        else
            result exercise SKIP 'firmware failed'; failed=1
        fi
    else
        for item in renode-start firmware exercise; do result "$item" SKIP 'blocked: bubblewrap isolation failed'; done
    fi
    # Local HTTP only: forwarding/browser behavior is a separate manual observation.
    check gui 40 python3 - <<'PY'
import json
import subprocess
import sys
import time
import urllib.request
from cuberange import ports
url = f"http://127.0.0.1:{ports.gui()}"
p = subprocess.Popen([sys.executable, "-m", "cuberange.gui"])
try:
    for attempt in range(50):
        if p.poll() is not None:
            raise RuntimeError("GUI exited before readiness (port already in use?)")
        try:
            with urllib.request.urlopen(url + "/api/config", timeout=1) as r:
                config = json.load(r)
            break
        except OSError:
            time.sleep(0.2)
    else:
        raise RuntimeError("GUI readiness deadline exceeded")
    time.sleep(0.2)
    assert p.poll() is None, "response may belong to an existing server"
    assert config["gui_port"] == ports.gui(), config
    with urllib.request.urlopen(url, timeout=3) as r:
        assert r.status == 200 and b"<html" in r.read().lower()
    print(f"HTTP 200: / and /api/config on {url}; forwarding not tested")
finally:
    p.terminate()
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
PY
    [ "$?" = 0 ] || failed=1
fi
result summary "$([ "$failed" = 0 ] && echo PASS || echo FAIL)" "mode=$MODE elapsed=$((SECONDS-started))s; Codespace creation time excluded"
printf 'LOG %s\n' "$LOG"
exit "$failed"
