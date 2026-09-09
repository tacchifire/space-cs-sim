#!/usr/bin/env bash
#
# CubeRange - Renode capability probe / evidence gate
# ============================================================================
#
# Every capability claim in docs/superpowers/specs/*-design.md must be reproducible by this
# script. If a claim cannot be reproduced here, the claim does not belong in the design.
#
# This harness is required to be able to FAIL. An earlier version reported PASS for every probe
# because the output directory did not exist and `grep` on a missing file returns "no match".
# Section Z runs deliberate-failure self-tests to prove the failure path still works. If the
# self-tests do not fail, the whole run is void.
#
# Usage:
#   ./tools/renode-probe/probe.sh                    # everything
#   ./tools/renode-probe/probe.sh --fetch-only       # network stage only (populates the cache)
#   ./tools/renode-probe/probe.sh --offline          # assert no network is needed; fail if it is
#
#   RENODE_DIR=/path/to/renode_portable  OUT=/path/for/logs  ./tools/renode-probe/probe.sh
#
# Exit code: 0 only if every REQUIRED probe passed and every self-test failed as designed.
# ============================================================================

set -uo pipefail

RENODE_DIR="${RENODE_DIR:-$HOME/tools/renode_1.16.1-dotnet_portable}"
RENODE="$RENODE_DIR/renode"
OUT="${OUT:-${TMPDIR:-/tmp}/cuberange-probe.$$}"

# Pinned Renode. The design fixes a released version for reproducibility; master is not supported.
#
# The build string is <commit>-<build timestamp>, and Antmicro builds the two architectures as
# separate jobs in one release run, ten minutes apart: both artifacts of v1.16.1 come from commit
# d66b0c2a, but x86-64 is stamped 09:23 and arm64 09:33. So the pin has to be per-architecture.
# Do NOT relax this to match only the commit prefix - the timestamp is the only thing that
# distinguishes the two artifacts, and it is what proves the right one is installed.
EXPECT_RENODE_VERSION="1.16.1"
case "$(uname -m)" in
  # aarch64 value read from the released asset's embedded version resource, not yet confirmed by
  # running `renode --version` on an arm64 host. If it is wrong this probe fails loudly, which is
  # the correct outcome - correct it against the observed string and say so in the commit.
  aarch64) EXPECT_RENODE_BUILD="d66b0c2a-202602160933" ;;
  *)       EXPECT_RENODE_BUILD="d66b0c2a-202602160923" ;;
esac

BOARD="@platforms/boards/nucleo_h753zi.repl"

# Prebuilt Zephyr images published by Antmicro and used by Renode's own tests/peripherals/MCAN.robot.
# Renode's URL convention embeds the artifact size and SHA1 (-s_<size>-<sha1>), so the URL is itself
# the integrity pin: Renode refuses a download whose size/hash does not match.
ZEPHYR_CAN_COUNTER='@https://dl.antmicro.com/projects/renode/nucleo_h743zi--zephyr-samples-drivers-can-counter.elf-s_1391464-17e71d5820ab718e5dc89f8480644c576306d24c'
ZEPHYR_CAN_SHELL='@https://dl.antmicro.com/projects/renode/nucleo_h743zi--zephyr-tests-drivers-can-shell.elf-s_1642156-92afb142a6be519e6cf51ecb34023167bb66e1fd'

MODE=all
case "${1:-}" in
  --fetch-only) MODE=fetch ;;
  --offline)    MODE=offline ;;
  '')           ;;
  *)            echo "unknown option: $1" >&2; exit 2 ;;
esac

# Minimum acceptable 4-node speed, as a fraction of real time, in the INTERACTIVE profile
# (quantum 2 ms + AdvanceImmediately). Measured 2.34x on a quiet 14-core host; the floor is set
# well below that so it catches a regression, not host-to-host variation.
#
# NOTE: the design's earlier 0.19-0.30x figure was an artifact of Renode's defaults - a 100 us
# global quantum and a real-time throttle that caps the emulation at exactly 1.0x. Both are
# disabled below. Do not "fix" a slow measurement by lowering this floor; check the tuning first.
#
# The floor is host-dependent and the default is an x86-64 workstation number. Renode pins one
# thread per machine, so a slower single core moves this directly: on a Raspberry Pi expect it to
# fail for host reasons rather than regression. Override with PERF_FLOOR=<n> to get a run through,
# then set the default from an actual measurement and record the host it came from. The rule stands
# either way - do not lower the floor to make a slow number pass without measuring first.
PERF_FLOOR="${PERF_FLOOR:-1.5}"
PERF_QUANTUM="0.002"

PASS_N=0; FAIL_N=0; SKIP_N=0
RC=0
pass() { PASS_N=$((PASS_N+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { FAIL_N=$((FAIL_N+1)); RC=1; printf '  \033[31mFAIL\033[0m  %s  -- %s\n' "$1" "${2:-}"; }
skip() { SKIP_N=$((SKIP_N+1)); printf '  \033[33mSKIP\033[0m  %s  -- %s\n' "$1" "${2:-}"; }
meas() { printf '  \033[36mMEAS\033[0m  %s: %s\n' "$1" "$2"; }
head1() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

[ -x "$RENODE" ] || { echo "Renode not found at $RENODE (set RENODE_DIR)"; exit 2; }
mkdir -p "$OUT" || { echo "cannot create output dir $OUT"; exit 2; }

# ---------------------------------------------------------------------------
# renode_run [-t seconds] <name> <-e args...>
# Captures stdout+stderr AND the exit status. Both are consulted by had_error.
#
# The per-probe timeout is not decoration. `emulation LogCANTraffic` blocks indefinitely when it
# cannot reach Wireshark, and with a single generous timeout it held the whole evidence gate for
# fifteen minutes - an optional feature stalling the thing that decides whether anything works.
# Probes that should answer in seconds are given seconds.
# ---------------------------------------------------------------------------
# `< /dev/null` is not decoration, and its absence cost 30 minutes of every `make check`.
#
# A failing Monitor command aborts the rest of the `-e` chain, INCLUDING the trailing `quit`, and
# drops Renode into its interactive Monitor. What happens next depends entirely on stdin. Run the
# probe straight from a shell whose stdin is closed and Renode reads EOF and exits in a second; run
# it under `make`, where stdin is an inherited open pipe, and it blocks on the prompt until the
# timeout fires. The section Z self-tests fail on purpose, so each of them burned its full 900 s.
# Measured: `make probe` about 6 minutes standalone, the same probe inside `make check` still in
# section Z after 41.
#
# The project's own rule L9 already says CI must redirect stdin. This is that rule, applied to the
# harness that enforces the rules.
renode_run() {
  local secs=900
  if [ "$1" = "-t" ]; then secs="$2"; shift 2; fi
  local name="$1"; shift
  ( cd "$RENODE_DIR" && timeout "$secs" ./renode --disable-xwt --console --plain \
      --hide-analyzers --hide-log "$@" -e 'quit' ) >"$OUT/$name.log" 2>&1 </dev/null
  echo $? > "$OUT/$name.rc"
}

# had_error <name> -> true (0) when the probe did NOT cleanly succeed.
# Fails closed: a missing log, an empty log, a nonzero/timeout exit, or any recognised Renode
# error string all count as failure. Never assume success from the absence of a known string.
had_error() {
  local log="$OUT/$1.log" rcf="$OUT/$1.rc" rc
  [ -f "$rcf" ] || { echo "    (no exit status recorded for $1)" >&2; return 0; }
  rc="$(cat "$rcf")"
  [ -s "$log" ] || { echo "    (probe produced no log: $log)" >&2; return 0; }
  if [ "$rc" != "0" ]; then
    echo "    (renode exited $rc${rc:+$([ "$rc" = 124 ] && echo ' = timeout')})" >&2
    return 0
  fi
  # These patterns were each observed in a real failing run. "No such command or device" and
  # "Fatal error:" were added because the self-tests caught this function missing them: Renode
  # prints them and still exits 0, so an exit-status check alone is not enough either.
  grep -qE "There was an error executing command|^Error E[0-9]+:|No such command or device|Fatal error:|Exception has been thrown|Unhandled exception|Could not find suitable constructor|Could not resolve type" "$log"
}

err_of() { grep -m1 -oE '(Error E[0-9]+: .{0,70}|There was an error.{0,60})' "$OUT/$1.log" 2>/dev/null; }

# ---------------------------------------------------------------------------
head1 "0. Toolchain and pinning"
# ---------------------------------------------------------------------------
VER_RAW="$("$RENODE" --version 2>&1 | head -3)"
echo "$VER_RAW" | sed 's/^/      /'
if echo "$VER_RAW" | grep -q "$EXPECT_RENODE_VERSION"; then
  pass "Renode version is the pinned $EXPECT_RENODE_VERSION"
else
  fail "Renode version pin" "expected $EXPECT_RENODE_VERSION, got: $(echo "$VER_RAW" | head -1)"
fi
if echo "$VER_RAW" | grep -q "$EXPECT_RENODE_BUILD"; then
  pass "Renode build is the pinned $EXPECT_RENODE_BUILD"
else
  fail "Renode build pin" "expected build $EXPECT_RENODE_BUILD"
fi
echo "      output dir: $OUT"

if [ "$MODE" = offline ]; then
  # Renode caches downloaded artifacts and reuses them without going to the network, so an offline
  # run works as long as the cache is warm. Be precise about what this does: it does NOT isolate
  # the network or verify that nothing is fetched. All it does is refuse to start when the cache
  # is empty, which is exactly the case where the five ELF-loading probes would otherwise try to
  # download and fail late with a confusing error.
  #
  # It previously did nothing at all - the option was parsed and the value never read again, so
  # --offline ran the full online probe and reported success.
  CACHE="${XDG_CONFIG_HOME:-$HOME/.config}/renode/cached_binaries"
  if [ -z "$(ls -A "$CACHE" 2>/dev/null)" ]; then
    echo "offline mode: Renode's artifact cache at $CACHE is empty." >&2
    echo "Run '$0 --fetch-only' once while online, then retry." >&2
    exit 2
  fi
  echo "      offline mode: reusing Renode's artifact cache at $CACHE"
fi

if [ "$MODE" = fetch ]; then
  head1 "Fetch stage only"
  renode_run fetch -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
    -e "sysbus LoadELF $ZEPHYR_CAN_COUNTER" -e "sysbus LoadELF $ZEPHYR_CAN_SHELL"
  had_error fetch && fail "artifact fetch" "$(err_of fetch)" || pass "artifacts fetched into Renode's cache"
  echo; echo "pass=$PASS_N fail=$FAIL_N"; exit $RC
fi

# ---------------------------------------------------------------------------
head1 "A. Core topology"
# ---------------------------------------------------------------------------
renode_run can_hub -e 'emulation CreateCANHub "canHub"'
had_error can_hub && fail "emulation CreateCANHub" "$(err_of can_hub)" || pass "emulation CreateCANHub"

renode_run four_nodes \
  -e 'emulation CreateCANHub "canHub"' \
  -e 'mach create "OBC"'  -e "machine LoadPlatformDescription $BOARD" -e 'connector Connect sysbus.fdcan1 canHub' \
  -e 'mach create "COMM"' -e "machine LoadPlatformDescription $BOARD" -e 'connector Connect sysbus.fdcan1 canHub' \
  -e 'mach create "EPS"'  -e "machine LoadPlatformDescription $BOARD" -e 'connector Connect sysbus.fdcan1 canHub' \
  -e 'mach create "ADCS"' -e "machine LoadPlatformDescription $BOARD" -e 'connector Connect sysbus.fdcan1 canHub'
had_error four_nodes && fail "4 nodes on one CAN hub" "$(err_of four_nodes)" || pass "4x nucleo_h753zi joined to canHub"

renode_run socket_terminal \
  -e 'mach create "COMM"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'emulation CreateServerSocketTerminal 5020 "spacelink" false' \
  -e 'connector Connect sysbus.usart3 spacelink'
had_error socket_terminal && fail "UART -> host TCP socket" "$(err_of socket_terminal)" || pass "CreateServerSocketTerminal + connect"

renode_run uart_filebackend \
  -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e "sysbus.usart3 CreateFileBackend @$OUT/fb.txt true"
had_error uart_filebackend && fail "UART file backend" "$(err_of uart_filebackend)" || pass "usart CreateFileBackend"

# ---------------------------------------------------------------------------
head1 "B. Host control channel"
# ---------------------------------------------------------------------------
# The design's host planes slave to Renode virtual time. Prove time can be read and advanced,
# and that the advance is exactly what was asked for.
renode_run vtime \
  -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'emulation RunFor "1.5"' -e 'machine ElapsedVirtualTime'
if had_error vtime; then
  fail "virtual time read/advance" "$(err_of vtime)"
elif grep -q 'Elapsed Virtual Time: 00:00:01.500000000' "$OUT/vtime.log"; then
  pass "machine ElapsedVirtualTime reports exactly 1.5s after RunFor \"1.5\""
else
  fail "virtual time read/advance" "expected 00:00:01.500000000, got: $(grep -m1 'Elapsed Virtual Time' "$OUT/vtime.log")"
fi

# External Control API: the typed binary control channel the host coordinator should own.
renode_run extctl \
  -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'emulation CreateExternalControlServer "extctl" 5045'
had_error extctl && fail "External Control server" "$(err_of extctl)" || pass "emulation CreateExternalControlServer"

[ -f "$RENODE_DIR/tools/external_control_client/include/renode_api.h" ] \
  && pass "external control client headers shipped with this Renode" \
  || fail "external control client" "renode_api.h not found in $RENODE_DIR/tools/external_control_client"

# ---------------------------------------------------------------------------
head1 "C. Peripheral models required by the node design"
# ---------------------------------------------------------------------------
probe_periph() { # <label> <repl fragment>
  local label="$1" frag="$2" tag
  tag="p_$(printf '%s' "$label" | tr -c 'a-zA-Z0-9' '_')"
  renode_run "$tag" -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
    -e "machine LoadPlatformDescriptionFromString \"x: $frag\""
  had_error "$tag" && fail "$label" "$(err_of "$tag")" || pass "$label"
}

probe_periph "temperature  TMP108 @ i2c1"            "Sensors.TMP108 @ i2c1 0x48"
probe_periph "power monitor PAC1934 @ i2c1"          "Sensors.PAC1934 @ i2c1 0x10"
probe_periph "battery gauge MAX77818 @ i2c1"         "Sensors.MAX77818 @ i2c1 0x36"
probe_periph "IMU          LSM9DS1_IMU @ i2c1"       "Sensors.LSM9DS1_IMU @ i2c1 0x6b"
probe_periph "magnetometer LSM9DS1_Magnetic @ i2c1"  "Sensors.LSM9DS1_Magnetic @ i2c1 0x1e"
probe_periph "gyroscope    LSM330_Gyroscope @ i2c1"  "Sensors.LSM330_Gyroscope @ i2c1 0x6b"

# KNOWN RENODE DEFECT (1.16.1): 'Sensors.OB1203 @ i2c1' aborts the process with
#   "Fatal error: Exception has been thrown by the target of an invocation"
#   at PlatformDescription.CreationDriver.CreateFromEntry -> exit 134 (SIGABRT).
# Reproducible in isolation, not load-related. An earlier version of this harness reported it as
# OK because it only grepped for an error string and ignored the exit status.
# 'Sensors.VEML7700' does not exist in 1.16.1 at all (Error E04).
# There is therefore NO usable ambient-light model, so the coarse sun sensor is modelled the way
# real CubeSats build it: photodiodes read through an ADC channel. Probed below.
probe_periph_neg() { # <label> <repl fragment> -- asserts the fragment FAILS, so we notice if it starts working
  local label="$1" frag="$2" tag
  tag="n_$(printf '%s' "$label" | tr -c 'a-zA-Z0-9' '_')"
  renode_run "$tag" -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
    -e "machine LoadPlatformDescriptionFromString \"x: $frag\""
  had_error "$tag" && pass "$label (still broken, as documented)" \
                   || fail "$label" "this model now WORKS - the design's workaround can be removed"
}
probe_periph_neg "known-bad OB1203 @ i2c1 aborts Renode"   "Sensors.OB1203 @ i2c1 0x53"
probe_periph_neg "known-bad VEML7700 does not exist"       "Sensors.VEML7700 @ i2c1 0x10"

# Analog sensing path: coarse sun sensor, battery/solar rails, thermistors.
# All three injection forms are exercised. The file form is what CI replay mode uses: a recorded
# voltage trace makes analog input deterministic without a live physics process.
printf '1200\n1350\n1500\n1650\n1800\n' > "$OUT/adc_trace.txt"
renode_run adc_inject \
  -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'sysbus.adc3 SetDefaultValue 1650 0' \
  -e 'sysbus.adc3 FeedVoltageSampleToChannel 1 900 5' \
  -e "sysbus.adc3 FeedVoltageSampleToChannel 2 @$OUT/adc_trace.txt"
had_error adc_inject && fail "ADC voltage injection" "$(err_of adc_inject)" \
  || pass "ADC injection: SetDefaultValue + FeedVoltageSampleToChannel (value and file forms)"

# Peripherals the SDLS design depends on. Asserted by name in the board's peripheral tree.
renode_run periph_tree -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" -e 'peripherals'
if had_error periph_tree; then
  fail "peripheral tree" "$(err_of periph_tree)"
else
  for P in "crypto (STM32H7_CRYPTO)" "rng (STM32F4_RNG)" "fdcan1 (MCAN)" "watchdog (STM32_IndependentWatchdog)" "i2c1 (STM32F7_I2C)" "usart3 (STM32F7_USART)"; do
    grep -qF "$P" "$OUT/periph_tree.log" && pass "board provides $P" || fail "board provides $P" "absent from 'peripherals' output"
  done
fi

# ---------------------------------------------------------------------------
head1 "D. Optional host integrations (must NOT be required)"
# ---------------------------------------------------------------------------
renode_run socketcan -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'machine CreateSocketCANBridge "scb" "vcan0"'
if grep -q 'Could not get the "vcan0" interface index' "$OUT/socketcan.log" 2>/dev/null; then
  skip "SocketCAN bridge" "command exists; host vcan0 absent (sudo modprobe vcan; sudo ip link add dev vcan0 type vcan; sudo ip link set up vcan0)"
elif had_error socketcan; then
  fail "SocketCAN bridge" "$(err_of socketcan)"
else
  pass "SocketCAN bridge attached to vcan0"
fi

renode_run -t 60 logcan -e 'emulation CreateCANHub "canHub"' -e 'emulation LogCANTraffic'
if [ "$(cat "$OUT/logcan.rc" 2>/dev/null)" = "124" ]; then
  skip "LogCANTraffic (Wireshark pcap)" "blocked for 60s with no Wireshark present - unusable headless, and a reason this probe carries its own timeout"
elif grep -qi 'Wireshark is not installed' "$OUT/logcan.log" 2>/dev/null; then
  skip "LogCANTraffic (Wireshark pcap)" "Wireshark absent; unusable headless - use SocketCAN or the Robot CAN tester"
elif had_error logcan; then
  fail "LogCANTraffic" "$(err_of logcan)"
else
  pass "LogCANTraffic"
fi

# ---------------------------------------------------------------------------
head1 "E. Real firmware: Zephyr + FDCAN across four nodes"
# ---------------------------------------------------------------------------
rm -f "$OUT"/uart_*.txt
RESC="$OUT/four_zephyr.resc"
{
  echo 'emulation CreateCANHub "canHub"'
  for N in OBC COMM EPS ADCS; do
    echo "mach create \"$N\""
    echo "machine LoadPlatformDescription $BOARD"
    echo 'connector Connect sysbus.fdcan1 canHub'
    echo "sysbus LoadELF $ZEPHYR_CAN_COUNTER"
    echo "sysbus.usart3 CreateFileBackend @$OUT/uart_$N.txt true"
    # Suppressing unhandled-register warnings is a PERFORMANCE requirement, not cosmetics:
    # leaving them on measured ~12x slower (74s vs 9.95s wall for the same workload).
    # Log suppression is for readable output, NOT performance: the unhandled-register warnings
    # are boot-only (~56/node) and cost 0-9%. The 12x figure an earlier revision reported came
    # from routing UART through LoggingUartAnalyzer, not from these warnings.
    echo 'logLevel 3 sysbus'
    echo 'logLevel 3 rcc'
    echo 'logLevel 3 fdcan1'
  done
  # The two settings that actually matter. Quantum 2 ms is the largest that keeps firmware output
  # byte-identical to a 100 us reference; above it, timing drifts silently and duration-dependently
  # (5 ms looks correct at 5 s and 10 s and only breaks at 20 s).
  echo "emulation SetGlobalQuantum \"$PERF_QUANTUM\""
  echo 'emulation SetGlobalAdvanceImmediately true'
} > "$RESC"

CORES=$(nproc)
LOAD1=$(awk '{print $1}' /proc/loadavg)
VIRT_SECONDS=8
START=$(date +%s.%N)
# GetTimeSourceInfo reports Renode's OWN accounting: "Elapsed Host Time" and "Cumulative load"
# (= host seconds per virtual second). That excludes process startup and ELF fetch, which the
# wall-clock figure does not - measuring the wrapper made the emulator look 2x slower than it is.
renode_run four_zephyr -e "include @$RESC" -e "emulation RunFor \"$VIRT_SECONDS\"" \
  -e 'emulation GetTimeSourceInfo'
END=$(date +%s.%N)
WALL=$(echo "$END - $START" | bc)

if had_error four_zephyr; then
  fail "Zephyr on 4 nodes" "$(err_of four_zephyr)"
else
  booted=0; received=0
  for N in OBC COMM EPS ADCS; do
    grep -q "Booting Zephyr OS" "$OUT/uart_$N.txt" 2>/dev/null && booted=$((booted+1))
    grep -q "Counter received"  "$OUT/uart_$N.txt" 2>/dev/null && received=$((received+1))
  done
  [ "$booted"   -eq 4 ] && pass "Zephyr booted on 4/4 nodes"            || fail "Zephyr boot" "only $booted/4 booted"
  [ "$received" -eq 4 ] && pass "4/4 nodes received CAN frames via hub" || fail "inter-node CAN" "only $received/4 received"

  WALL_RATIO=$(echo "scale=3; $VIRT_SECONDS / $WALL" | bc)
  meas "4-node, wall clock incl. startup" "$VIRT_SECONDS virtual s in $(printf '%.1f' "$WALL") s = ${WALL_RATIO}x real time"

  # Renode's own accounting. "Cumulative load" is host seconds per virtual second, so the
  # emulation-only real-time ratio is 1/load.
  CUMLOAD=$(grep -m1 'Cumulative load' "$OUT/four_zephyr.log" | sed 's/.*Cumulative load: *//' | tr -d '\r')
  if [ -n "$CUMLOAD" ] && [ "$(echo "$CUMLOAD > 0" | bc 2>/dev/null)" = "1" ]; then
    RATIO=$(echo "scale=3; 1 / $CUMLOAD" | bc)
    meas "4-node, emulation only" "cumulative load ${CUMLOAD} => ${RATIO}x real time (host load ${LOAD1}, ${CORES} cores)"
  else
    RATIO="$WALL_RATIO"
    skip "emulation-only speed" "could not parse Cumulative load; falling back to wall clock"
  fi

  # Renode is CPU bound, so the number is meaningless on a contended host: the same machine
  # measured 0.30x at load 0.5 and 0.079x at load 18.5. Assert only when the host is quiet.
  if [ "$(echo "$LOAD1 > $CORES / 2" | bc)" = "1" ]; then
    skip "4-node speed floor" "host too busy to measure (load ${LOAD1} on ${CORES} cores); rerun on an idle machine"
  elif [ "$(echo "$RATIO >= $PERF_FLOOR" | bc)" = "1" ]; then
    pass "4-node speed ${RATIO}x is at or above the ${PERF_FLOOR}x floor"
  else
    fail "4-node speed" "${RATIO}x is below the ${PERF_FLOOR}x floor"
  fi
fi

# ---------------------------------------------------------------------------
head1 "F. Space link: real bytes over a real socket"
# ---------------------------------------------------------------------------
# Charge answered here: the previous harness only attached a connector and never proved that a host
# process can exchange bytes with firmware. Connect a socket and require real output from Zephyr.
LINKPORT=5077
LINKRESC="$OUT/link.resc"
cat > "$LINKRESC" <<EOF
mach create "COMM"
machine LoadPlatformDescription $BOARD
sysbus LoadELF $ZEPHYR_CAN_SHELL
emulation CreateServerSocketTerminal $LINKPORT "spacelink" false
connector Connect sysbus.usart3 spacelink
logLevel 3 sysbus
logLevel 3 rcc
EOF

# Renode must stay alive while the host client talks to it. --console reads Monitor commands from
# stdin and quits at EOF, which silently killed an earlier version of this probe and produced a
# misleading "could not connect". --port keeps Renode listening and does not consume stdin.
MONPORT=$((LINKPORT + 1))
# setsid puts renode and its `timeout` wrapper in their own process group so the whole group can
# be terminated by PGID. Killing only $! leaves the real renode child alive: a previous run left
# two stray processes holding port 5078, which then skewed the next run's performance figure.
# NEVER use `pkill -f renode` for cleanup - it kills other people's Renode instances (and, as
# learned the hard way, the shell command doing the killing).
setsid bash -c "cd '$RENODE_DIR' && exec timeout 180 ./renode --disable-xwt --plain \
    --hide-analyzers --hide-log --port $MONPORT -e \"include @$LINKRESC\" -e 'start'" \
    >"$OUT/link.log" 2>&1 &
RENODE_BG=$!

python3 - "$LINKPORT" "$OUT/link_rx.bin" <<'PY' >"$OUT/link_client.log" 2>&1
import socket, sys, time

port, outp = int(sys.argv[1]), sys.argv[2]

s = None
deadline = time.time() + 60
while time.time() < deadline and s is None:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
    except OSError:
        time.sleep(0.5)
if s is None:
    print("CONNECT_FAILED")
    sys.exit(1)
print("CONNECTED")

def drain(sock, seconds, stop_on=None):
    got = b""
    end = time.time() + seconds
    sock.settimeout(1.0)
    while time.time() < end:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        except ConnectionResetError:
            print("RESET_BY_PEER")
            break
        if not chunk:
            print("EOF")
            break
        got += chunk
        if stop_on and stop_on in got:
            break
    return got

# Downlink direction: firmware -> host.
rx = drain(s, 40, stop_on=b"Booting Zephyr OS")
print("RX_BYTES", len(rx))
print("PREVIEW", rx[:160])

# Uplink direction: host -> firmware. The image runs a Zephyr shell, so a bare newline should
# produce a fresh prompt. Anything new arriving after our write proves the write reached firmware.
uplink = b"UNPROVEN"
try:
    s.sendall(b"\r\n")
    more = drain(s, 15)
    uplink = b"OK" if more else b"NO_RESPONSE"
    print("UPLINK_RX_BYTES", len(more))
    print("UPLINK_PREVIEW", more[:160])
except OSError as e:
    print("UPLINK_ERROR", e)

open(outp, "wb").write(rx)
print("UPLINK", uplink.decode())
PY
LINK_CLIENT_RC=$?
# Terminate the whole process group (negative PID), then verify nothing survives on the port.
kill -TERM -"$RENODE_BG" 2>/dev/null
sleep 1
kill -KILL -"$RENODE_BG" 2>/dev/null
wait "$RENODE_BG" 2>/dev/null
if ss -ltn 2>/dev/null | grep -q ":$LINKPORT "; then
  fail "space link cleanup" "port $LINKPORT still bound after cleanup - a stray Renode survived"
fi

if [ "$LINK_CLIENT_RC" != 0 ] || grep -q CONNECT_FAILED "$OUT/link_client.log"; then
  fail "space link: host->socket connect" "could not connect to 127.0.0.1:$LINKPORT (see $OUT/link_client.log)"
elif [ -s "$OUT/link_rx.bin" ] && grep -qa "Booting Zephyr OS" "$OUT/link_rx.bin"; then
  pass "space link downlink: host socket received live firmware output ($(wc -c <"$OUT/link_rx.bin") bytes)"
  if grep -q "^UPLINK OK" "$OUT/link_client.log"; then
    pass "space link uplink: firmware responded to bytes written by the host"
  else
    fail "space link uplink" "host wrote to the socket but firmware produced no response ($(grep -m1 '^UPLINK ' "$OUT/link_client.log"))"
  fi
  # The hazard the design must handle: this UART is also Zephyr's console.
  skip "link UART carries no diagnostics" "the Zephyr banner IS present on the link UART - the design must move the link to a separate UART or disable console output on it"
else
  fail "space link downlink" "connected but received no recognisable firmware output ($(wc -c <"$OUT/link_rx.bin" 2>/dev/null || echo 0) bytes)"
fi


# ---------------------------------------------------------------------------
head1 "G. Firmware-exercise primitives (EX-F01 / EX-F02 rest on these)"
# ---------------------------------------------------------------------------
# The design's table of "learner tools, all verified by measurement" (section 4.5) listed watchpoint
# hooks, symbol hooks and MPU enforcement, and ASSURANCE.md states that Renode enforces MPU
# execute-never. None of it was reproducible from this repository - the same shape as W23, W24 and
# W26, where a verification mechanism was described and never built. These probes are that mechanism.
#
# A hook reports through cpu.Log, not print(): a hook's print() goes to Renode's own stdout and never
# reaches a --port Monitor session. --hide-log would suppress the log lines too, so these probes use
# renode_run_logged.
renode_run_logged() {
  local secs=900
  if [ "$1" = "-t" ]; then secs="$2"; shift 2; fi
  local name="$1"; shift
  ( cd "$RENODE_DIR" && timeout "$secs" ./renode --disable-xwt --console --plain \
      --hide-analyzers "$@" -e 'quit' ) >"$OUT/$name.log" 2>&1 </dev/null
  echo $? > "$OUT/$name.rc"
}

# Resolve a data symbol with Renode itself, so this probe needs no cross toolchain. Checked against
# arm-zephyr-eabi-nm on a locally built image: both report _kernel at 0x24000894.
renode_run -t 120 g_symaddr \
  -e 'mach create "G"' \
  -e "machine LoadPlatformDescription $BOARD" \
  -e "sysbus LoadELF $ZEPHYR_CAN_COUNTER" \
  -e 'sysbus GetSymbolAddress "_kernel"' \
  -e 'sysbus ReadDoubleWord 0x08000000'
KERNEL_ADDR="$(grep -oE '0x[0-9a-fA-F]{16}' "$OUT/g_symaddr.log" | head -1)"
if had_error g_symaddr || [ -z "$KERNEL_ADDR" ]; then
  fail "sysbus GetSymbolAddress resolves a Zephyr symbol" "$(err_of g_symaddr)"
else
  pass "sysbus GetSymbolAddress resolves a Zephyr symbol (_kernel = $KERNEL_ADDR)"
fi

# The watchpoint target is the stack, not _kernel. A symbol in .bss is only written when the code
# that owns it runs, and in this sample _kernel saw no write in two virtual seconds - the probe
# reported a broken watchpoint when what was actually wrong was the address. The stack is written by
# every function call, so it tests the mechanism rather than the workload. Renode prints the initial
# SP while loading, which is where the address comes from.
# The initial stack pointer is the first word of the Cortex-M vector table, which LoadELF has
# already placed at 0x08000000. Renode also prints it as "SP = 0x..." - but only when the machine
# STARTS, and this probe never runs the emulation, so an earlier version of this line grepped a
# string that was never going to be in the log and skipped the watchpoint probe every time.
# Reading the vector table needs no run and no toolchain.
# GetSymbolAddress prints 16 hex digits and ReadDoubleWord prints 8, so the 8-digit form picks out
# the stack pointer unambiguously.
#
# `tr -d '\r'` is required, not tidy-up. Renode's console writes CRLF, and its value lines end in
# TWO carriage returns - measured with `cat -A`: `0x24002A00^M^M$`. An anchored regex therefore
# matches nothing at all, and because a missing address makes the watchpoint probe SKIP rather than
# FAIL, two earlier versions of this line reported a capability as untested while looking healthy.
INIT_SP="$(tr -d '\r' < "$OUT/g_symaddr.log" | grep -oE '^0x[0-9A-Fa-f]{8}$' | tail -1)"
if [ -n "$INIT_SP" ]; then
  WATCH_ADDR="$(printf '0x%X' $(( INIT_SP - 0x40 )))"
else
  WATCH_ADDR=""
fi

# G1. The watchpoint hook - the design calls this the highest-value grading primitive, and EX-F01
# uses it on the saved return-address slot to observe a control-flow hijack.
if [ -n "$WATCH_ADDR" ]; then
  renode_run_logged -t 180 g_watchpoint \
    -e 'mach create "G"' \
    -e "machine LoadPlatformDescription $BOARD" \
    -e "sysbus LoadELF $ZEPHYR_CAN_COUNTER" \
    -e "sysbus AddWatchpointHook $WATCH_ADDR DoubleWord Write \"cpu.Log(LogLevel.Error, 'PROBE_WP pc={0:X}'.format(cpu.PC.RawValue))\"" \
    -e 'emulation SetGlobalQuantum "0.002"' \
    -e 'emulation RunFor "2"'
  WP_HITS="$(grep -c 'PROBE_WP pc=' "$OUT/g_watchpoint.log" 2>/dev/null || echo 0)"
  if [ "$WP_HITS" -gt 0 ] && grep -qE 'PROBE_WP pc=[0-9A-F]+' "$OUT/g_watchpoint.log"; then
    pass "sysbus AddWatchpointHook Write fires and exposes cpu.PC ($WP_HITS hits at $WATCH_ADDR)"
  else
    fail "sysbus AddWatchpointHook Write" "no hook output in $OUT/g_watchpoint.log"
  fi
else
  skip "sysbus AddWatchpointHook Write" "could not read the initial SP, so there is no address to watch"
fi

# G2. The symbol hook - reaching a named function is how an exercise scores "you got there".
renode_run_logged -t 180 g_symhook \
  -e 'mach create "G"' \
  -e "machine LoadPlatformDescription $BOARD" \
  -e "sysbus LoadELF $ZEPHYR_CAN_COUNTER" \
  -e "cpu AddSymbolHook \"main\" \"cpu.Log(LogLevel.Error, 'PROBE_SYM pc={0:X}'.format(cpu.PC.RawValue))\"" \
  -e 'emulation SetGlobalQuantum "0.002"' \
  -e 'emulation RunFor "2"'
if grep -qE 'PROBE_SYM pc=[0-9A-F]+' "$OUT/g_symhook.log"; then
  pass "cpu AddSymbolHook resolves and fires on a named function"
else
  fail "cpu AddSymbolHook" "hook never fired ($(err_of g_symhook))"
fi

# G3. MPU execute-never. This is the claim EX-F01's honesty rests on: shellcode in SRAM must fail
# here exactly as it fails on the real part, which is why the exercise teaches code reuse instead.
#
# Query order matters - see G5. InstructionFetch must be the FIRST translation asked about this
# address in the session, so it gets its own process.
renode_run -t 120 g_mpu_xn \
  -e 'mach create "G"' \
  -e "machine LoadPlatformDescription $BOARD" \
  -e "sysbus LoadELF $ZEPHYR_CAN_COUNTER" \
  -e 'emulation SetGlobalQuantum "0.002"' \
  -e 'emulation RunFor "1"' \
  -e 'cpu TranslateAddress 0x24003000 InstructionFetch'
if grep -q 'Failed to translate address' "$OUT/g_mpu_xn.log"; then
  pass "MPU execute-never is enforced: SRAM refuses an instruction fetch"
else
  fail "MPU execute-never" "SRAM accepted an instruction fetch - EX-F01 must not teach shellcode if this is true"
fi

# G4. The control for G3. Without it, "the fetch failed" could just mean every query fails.
renode_run -t 120 g_mpu_flash \
  -e 'mach create "G"' \
  -e "machine LoadPlatformDescription $BOARD" \
  -e "sysbus LoadELF $ZEPHYR_CAN_COUNTER" \
  -e 'emulation SetGlobalQuantum "0.002"' \
  -e 'emulation RunFor "1"' \
  -e 'cpu TranslateAddress 0x08001000 InstructionFetch'
if had_error g_mpu_flash; then
  fail "MPU control: flash is executable" "$(err_of g_mpu_flash)"
else
  pass "MPU control: flash accepts an instruction fetch, so G3 is not a blanket refusal"
fi

# G5. D22, pinned as a negative assertion.
#
# `TranslateAddress` caches by address and NOT by access type. Ask about Read first and the very
# next InstructionFetch on the SAME address returns a false success - measured here: no prior query
# refuses the fetch, a prior Read or Write on the same address allows it, and a prior query on a
# different page or on flash changes nothing.
#
# This matters more than a curiosity. The natural way to verify "SRAM is readable but not
# executable" is to ask about Read and then about InstructionFetch, and that order silently produces
# the wrong answer - which would have told the design that shellcode works here. Pinned so that a
# future Renode which fixes the cache makes this probe fail and the workaround gets removed.
renode_run -t 120 g_xlat_cache \
  -e 'mach create "G"' \
  -e "machine LoadPlatformDescription $BOARD" \
  -e "sysbus LoadELF $ZEPHYR_CAN_COUNTER" \
  -e 'emulation SetGlobalQuantum "0.002"' \
  -e 'emulation RunFor "1"' \
  -e 'cpu TranslateAddress 0x24003000 Read' \
  -e 'cpu TranslateAddress 0x24003000 InstructionFetch'
if grep -q 'Failed to translate address' "$OUT/g_xlat_cache.log"; then
  fail "known-bad D22: TranslateAddress cache is keyed by address only" \
       "the fetch was refused after a prior Read - Renode appears fixed; drop the query-order workaround and this probe"
else
  pass "known-bad D22: a prior Read makes an SRAM instruction fetch falsely succeed (still broken, as documented)"
fi

# ---------------------------------------------------------------------------
head1 "Z. Harness self-tests (these MUST fail)"
# ---------------------------------------------------------------------------
# A harness that cannot fail is not evidence. Each case below is a known-bad input; if had_error
# does not flag it, this script is lying and the whole run is void.
SELFTEST_OK=1

renode_run -t 60 st_badtype -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'machine LoadPlatformDescriptionFromString "x: Sensors.ThisTypeDoesNotExist @ i2c1 0x10"'
had_error st_badtype && pass "self-test: unresolvable type is detected" || { SELFTEST_OK=0; fail "SELF-TEST" "an unresolvable peripheral type was NOT detected"; }

renode_run -t 60 st_badcmd -e 'this is not a monitor command'
had_error st_badcmd && pass "self-test: bad monitor command is detected" || { SELFTEST_OK=0; fail "SELF-TEST" "an invalid monitor command was NOT detected"; }

: > "$OUT/st_emptylog.log"; echo 0 > "$OUT/st_emptylog.rc"
had_error st_emptylog && pass "self-test: empty log is treated as failure" || { SELFTEST_OK=0; fail "SELF-TEST" "an empty log was treated as success"; }

rm -f "$OUT/st_missing.log" "$OUT/st_missing.rc"
had_error st_missing && pass "self-test: missing log is treated as failure" || { SELFTEST_OK=0; fail "SELF-TEST" "a missing log was treated as success"; }

echo "irrelevant output" > "$OUT/st_badrc.log"; echo 124 > "$OUT/st_badrc.rc"
had_error st_badrc && pass "self-test: nonzero/timeout exit is treated as failure" || { SELFTEST_OK=0; fail "SELF-TEST" "a timeout exit status was treated as success"; }

[ "$SELFTEST_OK" = 1 ] || { RC=1; printf '\n\033[31mSELF-TESTS FAILED - this run proves nothing.\033[0m\n'; }

# ---------------------------------------------------------------------------
printf '\n\033[1m== Summary ==\033[0m\n'
printf '  pass=%d  fail=%d  skip=%d\n' "$PASS_N" "$FAIL_N" "$SKIP_N"
echo "  logs: $OUT"
exit $RC
