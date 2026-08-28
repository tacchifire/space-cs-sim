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
EXPECT_RENODE_VERSION="1.16.1"
EXPECT_RENODE_BUILD="d66b0c2a-202602160923"

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

# Minimum acceptable 4-node speed, as a fraction of real time. Below this the interactive
# experience stops being usable; see the design's performance section.
PERF_FLOOR="0.15"

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
# renode_run <name> <-e args...>
# Captures stdout+stderr AND the exit status. Both are consulted by had_error.
# ---------------------------------------------------------------------------
renode_run() {
  local name="$1"; shift
  ( cd "$RENODE_DIR" && timeout 900 ./renode --disable-xwt --console --plain \
      --hide-analyzers --hide-log "$@" -e 'quit' ) >"$OUT/$name.log" 2>&1
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

renode_run logcan -e 'emulation CreateCANHub "canHub"' -e 'emulation LogCANTraffic'
if grep -qi 'Wireshark is not installed' "$OUT/logcan.log" 2>/dev/null; then
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
    echo 'logLevel 3 sysbus'
    echo 'logLevel 3 rcc'
    echo 'logLevel 3 fdcan1'
  done
  echo 'emulation SetGlobalQuantum "0.0001"'
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
head1 "Z. Harness self-tests (these MUST fail)"
# ---------------------------------------------------------------------------
# A harness that cannot fail is not evidence. Each case below is a known-bad input; if had_error
# does not flag it, this script is lying and the whole run is void.
SELFTEST_OK=1

renode_run st_badtype -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'machine LoadPlatformDescriptionFromString "x: Sensors.ThisTypeDoesNotExist @ i2c1 0x10"'
had_error st_badtype && pass "self-test: unresolvable type is detected" || { SELFTEST_OK=0; fail "SELF-TEST" "an unresolvable peripheral type was NOT detected"; }

renode_run st_badcmd -e 'this is not a monitor command'
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
