#!/usr/bin/env bash
# CubeRange — Renode capability probe
#
# Every capability claim in docs/superpowers/specs/*-design.md must be reproducible by this
# script. If a claim cannot be reproduced here, the claim does not go in the design.
#
# Usage:
#   RENODE_DIR=/path/to/renode_portable ./tools/renode-probe/probe.sh
#
# Exit code is non-zero if any REQUIRED probe fails.

set -uo pipefail

RENODE_DIR="${RENODE_DIR:-$HOME/tools/renode_1.16.1-dotnet_portable}"
RENODE="$RENODE_DIR/renode"
OUT="${OUT:-$(mktemp -d)}"
BOARD="@platforms/boards/nucleo_h753zi.repl"

# Prebuilt Zephyr CAN sample published by Antmicro and used by Renode's own MCAN.robot.
ZEPHYR_CAN_COUNTER='@https://dl.antmicro.com/projects/renode/nucleo_h743zi--zephyr-samples-drivers-can-counter.elf-s_1391464-17e71d5820ab718e5dc89f8480644c576306d24c'

RC=0
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s  -- %s\n' "$1" "${2:-}"; RC=1; }
skip() { printf '  \033[33mSKIP\033[0m  %s  -- %s\n' "$1" "${2:-}"; }

[ -x "$RENODE" ] || { echo "Renode not found at $RENODE (set RENODE_DIR)"; exit 2; }
mkdir -p "$OUT" || { echo "cannot create output dir $OUT"; exit 2; }
echo "Renode: $("$RENODE" --version 2>/dev/null || echo "$RENODE_DIR")"
echo "Output: $OUT"
echo

# renode_run <name> <-e args...>  -> stdout+stderr captured to $OUT/<name>.log
renode_run() {
  local name="$1"; shift
  ( cd "$RENODE_DIR" && timeout 900 ./renode --disable-xwt --console --plain \
      --hide-analyzers --hide-log "$@" -e 'quit' ) >"$OUT/$name.log" 2>&1
}

# A missing or empty log means the probe did not actually run. That MUST be a failure, never a
# silent pass -- an earlier version of this script reported PASS for every probe because the
# output directory did not exist and grep on a missing file returns "no match".
had_error() {
  local log="$OUT/$1.log"
  [ -s "$log" ] || { echo "    (probe produced no log: $log)" >&2; return 0; }
  grep -q "There was an error executing command" "$log"
}

echo "== A. Core topology =="

renode_run can_hub -e 'emulation CreateCANHub "canHub"'
had_error can_hub && fail "CAN hub creation" "$(grep -m1 'Error E' "$OUT/can_hub.log")" || pass "emulation CreateCANHub"

renode_run four_nodes \
  -e 'emulation CreateCANHub "canHub"' \
  -e 'mach create "OBC"'  -e "machine LoadPlatformDescription $BOARD" -e 'connector Connect sysbus.fdcan1 canHub' \
  -e 'mach create "COMM"' -e "machine LoadPlatformDescription $BOARD" -e 'connector Connect sysbus.fdcan1 canHub' \
  -e 'mach create "EPS"'  -e "machine LoadPlatformDescription $BOARD" -e 'connector Connect sysbus.fdcan1 canHub' \
  -e 'mach create "ADCS"' -e "machine LoadPlatformDescription $BOARD" -e 'connector Connect sysbus.fdcan1 canHub'
had_error four_nodes && fail "4 nodes on one CAN hub" "$(grep -m1 'Error E' "$OUT/four_nodes.log")" || pass "4x nucleo_h753zi joined to canHub"

renode_run socket_terminal \
  -e 'mach create "COMM"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'emulation CreateServerSocketTerminal 5020 "spacelink" false' \
  -e 'connector Connect sysbus.usart3 spacelink'
had_error socket_terminal && fail "UART -> host TCP socket" "$(grep -m1 'Error E' "$OUT/socket_terminal.log")" || pass "CreateServerSocketTerminal + connect (space link)"

renode_run uart_filebackend \
  -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e "sysbus.usart3 CreateFileBackend @$OUT/fb.txt true"
had_error uart_filebackend && fail "UART file backend" "$(grep -m1 'Error E' "$OUT/uart_filebackend.log")" || pass "usart CreateFileBackend (headless console capture)"

echo
echo "== B. Peripheral models required by the node design =="

probe_periph() { # <label> <repl fragment>
  local label="$1" frag="$2" tag
  tag="p_$(echo "$label" | tr -c 'a-zA-Z0-9' '_')"
  renode_run "$tag" -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
    -e "machine LoadPlatformDescriptionFromString \"x: $frag\""
  if had_error "$tag"; then
    fail "$label" "$(grep -m1 -oE 'Error E[0-9]+: .{0,70}' "$OUT/$tag.log")"
  else
    pass "$label"
  fi
}

probe_periph "temperature  TMP108 @ i2c1"            "Sensors.TMP108 @ i2c1 0x48"
probe_periph "power monitor PAC1934 @ i2c1"          "Sensors.PAC1934 @ i2c1 0x10"
probe_periph "battery gauge MAX77818 @ i2c1"         "Sensors.MAX77818 @ i2c1 0x36"
probe_periph "IMU          LSM9DS1_IMU @ i2c1"       "Sensors.LSM9DS1_IMU @ i2c1 0x6b"
probe_periph "magnetometer LSM9DS1_Magnetic @ i2c1"  "Sensors.LSM9DS1_Magnetic @ i2c1 0x1e"
probe_periph "gyroscope    LSM330_Gyroscope @ i2c1"  "Sensors.LSM330_Gyroscope @ i2c1 0x6b"
probe_periph "light/sun    OB1203 @ i2c1"            "Sensors.OB1203 @ i2c1 0x53"

echo
echo "== C. Optional host integrations (must NOT be required) =="

renode_run socketcan -e 'mach create "T"' -e "machine LoadPlatformDescription $BOARD" \
  -e 'machine CreateSocketCANBridge "scb" "vcan0"'
if grep -q 'Could not get the "vcan0" interface index' "$OUT/socketcan.log"; then
  skip "SocketCAN bridge" "command exists; host vcan0 absent (needs: sudo modprobe vcan; sudo ip link add dev vcan0 type vcan; sudo ip link set up vcan0)"
elif had_error socketcan; then
  fail "SocketCAN bridge" "$(grep -m1 'Error E' "$OUT/socketcan.log")"
else
  pass "SocketCAN bridge attached to vcan0"
fi

renode_run logcan -e 'emulation CreateCANHub "canHub"' -e 'emulation LogCANTraffic'
if grep -qi 'Wireshark is not installed' "$OUT/logcan.log"; then
  skip "LogCANTraffic (Wireshark pcap)" "Wireshark absent; not usable headless - use SocketCAN or the Robot CAN tester instead"
elif had_error logcan; then
  fail "LogCANTraffic" "$(grep -m1 'Error E' "$OUT/logcan.log")"
else
  pass "LogCANTraffic"
fi

echo
echo "== D. Real firmware: Zephyr + FDCAN across four nodes =="

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

VIRT_SECONDS=8
START=$(date +%s.%N)
renode_run four_zephyr -e "include @$RESC" -e "emulation RunFor \"$VIRT_SECONDS\""
END=$(date +%s.%N)
WALL=$(echo "$END - $START" | bc)

if had_error four_zephyr; then
  fail "Zephyr boots on 4 nodes" "$(grep -m1 'Error E' "$OUT/four_zephyr.log")"
else
  booted=0; received=0
  for N in OBC COMM EPS ADCS; do
    grep -q "Booting Zephyr OS" "$OUT/uart_$N.txt" 2>/dev/null && booted=$((booted+1))
    grep -q "Counter received" "$OUT/uart_$N.txt" 2>/dev/null && received=$((received+1))
  done
  [ "$booted"   -eq 4 ] && pass "Zephyr booted on 4/4 nodes"            || fail "Zephyr boot" "only $booted/4 booted"
  [ "$received" -eq 4 ] && pass "4/4 nodes received CAN frames via hub" || fail "inter-node CAN" "only $received/4 received"
  RATIO=$(echo "scale=2; $VIRT_SECONDS / $WALL" | bc)
  printf '  \033[36mMEAS\033[0m  4-node speed: %s virtual s in %.1f s wall = %sx real time\n' "$VIRT_SECONDS" "$WALL" "$RATIO"
fi

echo
echo "Logs: $OUT"
exit $RC
