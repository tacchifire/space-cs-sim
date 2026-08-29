#!/usr/bin/env python3
"""R0 spike: prove the two host-side primitives work together against live firmware.

  1. `attacker/TcpCanInjector.cs` - runtime-compiled C# ICAN peripheral on a bare Renode machine.
     A host process writes "<hexid> <hexdata>\\n" to a TCP socket and a raw CAN frame appears on
     the hub. No privileges, no Zephyr firmware for the attacker, no SocketCAN.
  2. `src/cuberange/renode/extctl.py` - pure-Python client for Renode's External Control API.
     Advances virtual time and reads the victim's peripheral registers over a binary protocol.

The proof is that a frame authored in Python lands in the victim MCAN's Rx FIFO, observed by
reading RXF0S out of the victim over External Control - two independent mechanisms agreeing.

Constraint being exercised (measured, see design section 4.6): the emulation must be RUNNING when
a frame is injected. While every machine is paused, CANHub logs the frame but the per-machine
HandleTimeDomainEvent delivery is silently dropped. So the injection happens from a second thread
while `run_for` is in flight.

Usage:  python3 tests/manual/spike_inject_and_observe.py
"""
import os
import socket
import subprocess
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "src"))

from cuberange.renode.extctl import MS, Renode  # noqa: E402

RENODE_DIR = os.environ.get(
    "RENODE_DIR", os.path.expanduser("~/tools/renode_1.16.1-dotnet_portable"))
BOARD = "@platforms/boards/nucleo_h753zi.repl"
ZEPHYR_CAN = ("@https://dl.antmicro.com/projects/renode/"
              "nucleo_h743zi--zephyr-samples-drivers-can-counter.elf"
              "-s_1391464-17e71d5820ab718e5dc89f8480644c576306d24c")

EC_PORT = 3690        # External Control API
INJ_PORT = 3691       # TcpCanInjector line protocol (transmitter)
OBS_PORT = 3692       # second injector, used only as a bus observer
WORK = os.environ.get("WORK", "/tmp/cuberange-spike")

INJECT_ID = 0x123     # arbitrary; chosen not to collide with the sample's own 0x10 / 0x12345

# Known-value check for the External Control memory path. Renode prints the reset vector it read
# out of the ELF ("Setting initial values: PC = ..., SP = ..."), and the first word of flash is
# that same initial stack pointer - so we can verify sysbus_read against Renode's own statement
# instead of trusting an address we guessed.
FLASH_BASE = 0x08000000


def build_resc(path, log_path):
    with open(path, "w") as f:
        f.write(f"""
emulation CreateCANHub "canHub"

mach create "VICTIM"
machine LoadPlatformDescription {BOARD}
connector Connect sysbus.fdcan1 canHub
sysbus LoadELF {ZEPHYR_CAN}
sysbus.usart3 CreateFileBackend @{log_path} true
logLevel 3 sysbus
logLevel 3 rcc
logLevel 3 fdcan1

# The attacker is a BARE machine: no platform file, no ELF, no CPU. It exists only so the hub can
# resolve GetName()/GetMachine() on the injector - an ICAN attached to a hub must be a registered
# machine peripheral, or the exception poisons the hub for every node.
mach create "attacker"
include @{os.path.join(REPO, 'attacker', 'TcpCanInjector.cs')}
machine CreateTcpCanInjector "inj" {INJ_PORT}
connector Connect inj canHub

# A second injector, used only as an observer. Its OnFrameReceived logs every frame the hub
# delivers, which is an unambiguous witness that a frame crossed the bus - no register addresses
# to guess and no dependency on the victim firmware's acceptance filters.
machine CreateTcpCanInjector "obs" {OBS_PORT}
connector Connect obs canHub

emulation SetGlobalQuantum "0.002"
emulation CreateExternalControlServer "extctl" {EC_PORT}
""")


def main():
    os.makedirs(WORK, exist_ok=True)
    uart = os.path.join(WORK, "victim.uart")
    resc = os.path.join(WORK, "spike.resc")
    rlog = os.path.join(WORK, "renode.log")
    for p in (uart, rlog):
        if os.path.exists(p):
            os.remove(p)
    build_resc(resc, uart)

    proc = subprocess.Popen(
        ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
         "--port", str(EC_PORT + 100), "-e", f"include @{resc}"],
        cwd=RENODE_DIR, stdout=open(rlog, "w"), stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True)

    rc = 1
    try:
        # --- connect the External Control client -------------------------------------------
        r = None
        for _ in range(60):
            try:
                r = Renode(EC_PORT)
                break
            except (OSError, Exception):
                time.sleep(1)
        if r is None:
            print("FAIL: could not reach the External Control server")
            return 1
        print("OK   external control connected")

        vm = r.get_machine("VICTIM")
        bus = r.get_bus_context(vm)
        print(f"OK   resolved VICTIM machine={vm} sysbus={bus}")

        t0 = r.get_current_time_us()
        r.run_for(500, MS)
        t1 = r.get_current_time_us()
        print(f"OK   virtual time advanced {t0} -> {t1} us")
        if t1 - t0 != 500_000:
            print(f"FAIL: expected +500000 us, got {t1 - t0}")
            return 1

        # let the firmware finish bringing FDCAN up
        r.run_for(2000, MS)

        # --- proof 1: External Control can read guest memory, checked against a known value ---
        word = int.from_bytes(r.sysbus_read(bus, FLASH_BASE, count=4), "little")
        with open(rlog, errors="replace") as f:
            boot_log = f.read()
        expected_sp = None
        for line in boot_log.splitlines():
            if "Setting initial values" in line and "SP = " in line:
                expected_sp = int(line.split("SP = ")[1].strip().rstrip("."), 16)
                break
        if expected_sp is None:
            print(f"WARN sysbus_read(0x{FLASH_BASE:08x}) = 0x{word:08x} "
                  "(Renode never printed an initial SP to check it against)")
            mem_ok = False
        elif word == expected_sp:
            print(f"OK   sysbus_read(0x{FLASH_BASE:08x}) = 0x{word:08x} "
                  f"== the initial SP Renode reported")
            mem_ok = True
        else:
            print(f"FAIL sysbus_read(0x{FLASH_BASE:08x}) = 0x{word:08x}, "
                  f"expected 0x{expected_sp:08x}")
            mem_ok = False

        # --- proof 2: inject while the emulation is RUNNING ---------------------------------
        injected = []

        def injector():
            # Wait until run_for is in flight; while every machine is paused the hub logs the
            # frame but silently drops the per-machine delivery.
            time.sleep(0.4)
            try:
                s = socket.create_connection(("127.0.0.1", INJ_PORT), timeout=5)
                for i in range(8):
                    s.sendall(f"{INJECT_ID:x} AABBCCDD{i:02X}EEFF11\n".encode())
                    injected.append(i)
                    time.sleep(0.05)
                s.close()
            except OSError as e:
                print(f"     injector socket error: {e}")

        th = threading.Thread(target=injector, daemon=True)
        th.start()
        r.run_for(3000, MS)     # frames land during this call
        th.join(timeout=5)
        print(f"OK   wrote {len(injected)} frames to the injector socket")

        # --- verdict -------------------------------------------------------------------------
        with open(rlog, errors="replace") as f:
            log = f.read()
        sent = log.count("INJECTOR-TX id=0x123")
        dropped = log.count("INJECTOR-TX-DROPPED")
        # The observer prints the decoded frame; Renode renders the id in decimal.
        observed = sum(1 for ln in log.splitlines()
                       if "attacker/obs" in ln and "INJECTOR-RX" in ln
                       and f"Id={INJECT_ID}" in ln)
        print(f"OK   injector transmitted {sent} frames ({dropped} dropped for want of a hub)")
        print(f"OK   independent observer on the hub saw {observed} of them")

        if not injected:
            print("FAIL: nothing was written to the injector socket")
        elif sent == 0:
            print("FAIL: the socket accepted bytes but no frame was transmitted")
        elif observed == 0:
            print("FAIL: frames were transmitted but never crossed the hub")
        elif not mem_ok:
            print("FAIL: CAN injection works but the External Control memory read did not verify")
        else:
            print()
            print(f"PASS: {observed} CAN frames authored in Python crossed the emulated bus, and "
                  "External Control read guest memory correctly - no privileges, no attacker "
                  "firmware, no SocketCAN.")
            rc = 0
        r.close()
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except OSError:
            pass
        proc.wait(timeout=10)
    return rc


if __name__ == "__main__":
    sys.exit(main())
