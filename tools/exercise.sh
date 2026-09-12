#!/bin/bash
# Start one exercise's scenario and hand over - a shell, or one command via CUBERANGE_RUN.
#
# This runs INSIDE the network namespace ISOLATE created, which is the whole point: the space
# link, the Monitor and the injectors live on a loopback that exists only in here.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EX="${CUBERANGE_EX:?}"
D="$REPO/exercises/$EX"
LOG="$OUT/$EX-renode.log"
STARTED="$(python3 -c 'import time; print(time.time())')"

ports() { PYTHONPATH="$REPO/src" python3 -c "from cuberange import ports; print(ports.$1)"; }

cd "$RENODE_DIR"
# stdin from /dev/null is safe because --port moves the Monitor to telnet, so nothing here reads
# it. RenodeSupervisor has launched Renode this way since P0.
./renode --disable-xwt --plain --hide-analyzers --port "$(ports 'monitor()')" \
    -e "\$injector=@$REPO/attacker/TcpCanInjector.cs" \
    -e "\$profile=@$REPO/scripts/profiles/interactive.resc" \
    -e "\$out=@$OUT" \
    -e "include @$D/scenario.resc" \
    -e 'start' < /dev/null > "$LOG" 2>&1 &
RENODE_PID=$!
trap 'kill $RENODE_PID 2>/dev/null' EXIT

# Wait for a console this run wrote, not for a console file to exist. The .uart backends are
# append-mode files that survive between runs, so "the file is there" was true before Renode had
# opened anything - the first version of this waited on that, broke out immediately, and the
# solver met an injector that was not listening yet. ConnectionRefusedError, which is exactly the
# error this whole launcher exists to stop people from getting.
ready() {
    python3 - "$OUT" "$STARTED" <<'PYEOF'
import pathlib, sys
out, started = pathlib.Path(sys.argv[1]), float(sys.argv[2])
for p in out.glob("*.uart"):
    try:
        if p.stat().st_mtime >= started and "Booting Zephyr" in p.read_text(errors="replace"):
            sys.exit(0)
    except OSError:
        pass
sys.exit(1)
PYEOF
}

for _ in $(seq 1 120); do
    ready && break
    kill -0 $RENODE_PID 2>/dev/null || { echo "Renode exited during startup; see $LOG"; exit 1; }
    python3 -c 'import time; time.sleep(0.5)'
done
ready || { echo "no node reported booting within 60s; see $LOG"; exit 1; }

cd "$REPO"
if [ -n "${CUBERANGE_RUN:-}" ]; then
    eval "$CUBERANGE_RUN"
    exit $?
fi

# The ports THIS scenario opens, read out of the scenario. Printing ports.link(0) and
# ports.injector(0) unconditionally - which the Makefile used to do - is right for most exercises
# and wrong for EX-X01, whose injector is on the crosslink and is not indexed by satellite.
# tests/pytest/test_port_literals.py already checks these defaults against cuberange.ports, so
# reading them here is both accurate and map-derived.
echo
echo "  exercises/$EX is running, and this shell is inside its network namespace."
echo "  Nothing outside can reach these ports, which is why the solver runs from here."
echo
sed -n 's/^\$\([a-z0-9]*port\)?=\([0-9]*\).*/    \1  localhost:\2/p' "$D/scenario.resc"
echo "    monitor   localhost:$(ports 'monitor()')"
cat <<BANNER

    python3 exercises/$EX/solve.py
    tail -f $OUT/*.uart
    renode log: $LOG

  exit, or Ctrl-D, stops the range.

BANNER
PS1='cuberange:'"$EX"'$ ' bash --norc -i
