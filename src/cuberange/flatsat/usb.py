"""Electronic Cats three-CDC USB discovery and bounded serial IO.

The 1209:babc firmware exposes Cat-Radio0, Cat-Radio1 and Cat-Shell.
Roles come from USB descriptors, never the ttyACM enumeration order.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

USB_VID = 0x1209
USB_PID = 0xBABC
BAUDRATE = 115200
INTERFACE_ROLES = {0: "radio0", 2: "radio1", 4: "shell"}
QUERY_COMMANDS = ("fw_version", "status", "sensors", "help")


class FlatSatError(RuntimeError):
    """An actionable discovery, permissions, or USB IO failure."""


def _serial_module():
    try:
        import serial
        return serial
    except ImportError as exc:
        raise FlatSatError("pyserial is missing; install requirements-flatsat.txt") from exc


@dataclass(frozen=True)
class FlatSatPort:
    path: str
    role: str
    serial_number: str | None
    interface: str | None
    location: str | None
    accessible: bool

    @property
    def board_id(self) -> str:
        if self.serial_number:
            return self.serial_number
        # CDC interfaces on one physical USB device share the prefix before ':'.
        return (self.location or self.path).split(":", 1)[0]


def discover_ports(infos=None) -> list[FlatSatPort]:
    """Read USB metadata only. No port is opened and no probe is sent."""
    if infos is None:
        _serial_module()
        from serial.tools.list_ports import comports
        infos = comports()
    found = []
    for info in infos:
        if (info.vid, info.pid) != (USB_VID, USB_PID):
            continue
        interface = getattr(info, "interface", None)
        description = f"{interface or ''} {getattr(info, 'description', '')}".lower()
        role = "unknown"
        for name in ("shell", "radio0", "radio1"):
            if name in description.replace(" ", ""):
                role = name
                break
        location = getattr(info, "location", None)
        if role == "unknown" and location:
            match = re.search(r":\d+\.(\d+)$", location)
            if match:
                role = INTERFACE_ROLES.get(int(match[1]), "unknown")
        found.append(FlatSatPort(
            path=info.device, role=role,
            serial_number=getattr(info, "serial_number", None),
            interface=interface, location=location,
            accessible=os.access(info.device, os.R_OK | os.W_OK),
        ))
    return sorted(found, key=lambda p: (p.board_id, p.role, p.path))


def select_ports(ports: list[FlatSatPort], port: str = "all",
                 serial_number: str | None = None) -> list[FlatSatPort]:
    candidates = [p for p in ports if serial_number is None or p.board_id == serial_number]
    if port not in ("all", "radio0", "radio1", "shell"):
        candidates = [p for p in candidates
                      if Path(p.path).resolve() == Path(port).resolve()]
    elif port != "all":
        candidates = [p for p in candidates if p.role == port]
    if not candidates:
        raise FlatSatError(
            f"No matching FlatSat port ({port}); run 'devices' and check USB connection. "
            "Supported USB identity: 1209:babc (Cat-Radio0/1/Shell).")
    if len({p.board_id for p in candidates}) > 1:
        raise FlatSatError("Multiple FlatSat boards found; choose --serial or an explicit --port.")
    if port != "all" and len(candidates) != 1:
        raise FlatSatError(f"Multiple ports match {port}; choose an explicit --port.")
    return candidates


class SerialSession:
    def __init__(self, port: FlatSatPort, timeout: float = 0.05):
        self.port = port
        self.timeout = timeout
        self._serial = None

    def __enter__(self):
        serial = _serial_module()
        connection = serial.Serial()
        connection.port = self.port.path
        connection.baudrate = BAUDRATE
        connection.timeout = self.timeout
        connection.write_timeout = self.timeout
        connection.dtr = False
        connection.rts = False
        if sys.platform.startswith("linux"):
            connection.exclusive = True
        try:
            connection.open()
        except (OSError, serial.SerialException) as exc:
            connection.close()
            if getattr(exc, "errno", None) in (1, 13):
                raise FlatSatError(
                    f"Permission denied opening {self.port.path}. "
                    "Grant this user serial access; see docs/flatsat.ja.md.") from exc
            raise FlatSatError(f"Cannot open {self.port.path}: {exc}") from exc
        self._serial = connection
        return self

    def __exit__(self, *_):
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def read(self, size: int = 4096) -> bytes:
        if self._serial is None:
            raise FlatSatError("Serial port is not open")
        serial = _serial_module()
        try:
            # Avoid waiting for an entire 4096-byte block when just one line arrived.
            return self._serial.read(min(size, max(1, self._serial.in_waiting)))
        except (OSError, serial.SerialException) as exc:
            raise FlatSatError(f"Read failed on {self.port.path}: {exc}") from exc

    def write(self, data: bytes) -> None:
        if self._serial is None:
            raise FlatSatError("Serial port is not open")
        if self.port.role != "shell" or data not in (
                (command + "\r\n").encode("ascii") for command in QUERY_COMMANDS):
            raise FlatSatError("Only fw_version/status/sensors/help queries on Cat-Shell are supported")
        serial = _serial_module()
        try:
            if self._serial.write(data) != len(data):
                raise FlatSatError(f"Incomplete write on {self.port.path}")
            # Do not call flush()/tcdrain(): pyserial's drain has no deadline.
            # A recognized shell response confirms delivery of the query.
        except (OSError, serial.SerialException) as exc:
            raise FlatSatError(f"Write failed on {self.port.path}: {exc}") from exc
