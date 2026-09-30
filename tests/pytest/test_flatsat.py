"""Owned FlatSat USB support, tested without opening a physical device.

Discovery uses fabricated USB descriptors; transport uses a pseudoterminal. The CLI
tests replace the serial transport so accidental command transmission is observable.
"""
from __future__ import annotations

import binascii
import errno
import importlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.flatsat import protocol, usb  # noqa: E402
from cuberange.flatsat.usb import FlatSatError  # noqa: E402


def descriptor(path, interface, serial_number="BOARD-A", *, location="2-1:1.0",
               vid=0x1209, pid=0xBABC):
    return SimpleNamespace(
        device=path, interface=interface, serial_number=serial_number,
        location=location, vid=vid, pid=pid, product="Flat-Sat",
        description=f"Flat-Sat - {interface}", manufacturer="Electronic Cats",
        hwid=f"USB VID:PID={vid:04X}:{pid:04X} SER={serial_number} LOCATION={location}",
    )


def board_ports(serial_number="BOARD-A", prefix="/dev/ttyACM"):
    """Roles deliberately disagree with pathname order."""
    return usb.discover_ports([
        descriptor(prefix + "8", "Cat-Shell", serial_number, location="2-1:1.4"),
        descriptor(prefix + "6", "Cat-Radio1", serial_number, location="2-1:1.2"),
        descriptor(prefix + "9", "Cat-Radio0", serial_number, location="2-1:1.0"),
    ])


def test_usb_roles_come_from_interface_descriptors_and_unrelated_usb_is_ignored():
    infos = [
        descriptor("/dev/ttyACM8", "Cat-Shell", location="2-1:1.4"),
        descriptor("/dev/ttyACM6", "Cat-Radio1", location="2-1:1.2"),
        descriptor("/dev/ttyACM9", "Cat-Radio0", location="2-1:1.0"),
        descriptor("/dev/ttyACM7", "Cat-Shell", pid=0x0001),
    ]
    found = usb.discover_ports(infos)
    assert {p.path: p.role for p in found} == {
        "/dev/ttyACM8": "shell", "/dev/ttyACM6": "radio1", "/dev/ttyACM9": "radio0",
    }
    assert all(p.serial_number == "BOARD-A" for p in found)
    assert all(isinstance(p.accessible, bool) for p in found)


def test_usb_roles_can_be_recovered_from_usb_interface_number():
    found = usb.discover_ports([
        descriptor("/dev/ttyACM8", None, location="2-1:1.4"),
        descriptor("/dev/ttyACM6", None, location="2-1:1.2"),
        descriptor("/dev/ttyACM9", None, location="2-1:1.0"),
    ])
    assert {p.path: p.role for p in found} == {
        "/dev/ttyACM8": "shell", "/dev/ttyACM6": "radio1", "/dev/ttyACM9": "radio0",
    }


def test_port_selection_keeps_all_interfaces_of_one_board_and_resolves_roles():
    ports = board_ports()
    assert {p.path for p in usb.select_ports(ports)} == {p.path for p in ports}
    assert [p.path for p in usb.select_ports(ports, port="shell")] == ["/dev/ttyACM8"]
    assert [p.path for p in usb.select_ports(ports, port="radio0")] == ["/dev/ttyACM9"]
    assert [p.path for p in usb.select_ports(ports, port="/dev/ttyACM6")] == ["/dev/ttyACM6"]


def test_multiple_boards_require_a_serial_number_or_explicit_device_path():
    ports = board_ports() + board_ports("BOARD-B", prefix="/dev/ttyACM1")
    with pytest.raises(FlatSatError):
        usb.select_ports(ports)
    with pytest.raises(FlatSatError):
        usb.select_ports(ports, port="shell")
    selected = usb.select_ports(ports, serial_number="BOARD-B")
    assert len(selected) == 3
    assert {p.serial_number for p in selected} == {"BOARD-B"}
    assert [p.path for p in usb.select_ports(ports, port="/dev/ttyACM18")] == ["/dev/ttyACM18"]


def test_port_selection_refuses_absence_and_paths_outside_discovered_flatsats():
    with pytest.raises(FlatSatError):
        usb.select_ports([])
    with pytest.raises(FlatSatError):
        usb.select_ports(board_ports(), port="/dev/ttyACM88")
    with pytest.raises(FlatSatError):
        usb.select_ports(board_ports(), serial_number="MISSING")


def test_ping_layout_and_crc_match_an_independent_primary_header_decode():
    raw = protocol.build_ping(seq_count=1, timestamp=0)
    # Electronic Cats ground station 3a2d7b9500520015232cd9d6fc7b0d1916ee866c:
    # modules/core/constants.py and ccsds.py; CRC independently computed with binascii.
    assert raw == bytes.fromhex("1820c0010006000000001022d9")
    assert len(raw) == 13
    first = int.from_bytes(raw[:2], "big")
    assert first >> 13 == 0
    assert (first >> 12) & 1 == 1
    assert first & 0x7FF == 0x020
    assert int.from_bytes(raw[2:4], "big") == 0xC001
    assert int.from_bytes(raw[4:6], "big") == 6
    assert raw[6:10] == bytes(4)
    assert raw[10] == 0x10
    assert int.from_bytes(raw[-2:], "big") == binascii.crc_hqx(raw[:-2], 0xFFFF)
    packet = protocol.decode_packet(raw)
    assert packet["apid"] == 0x020
    assert packet["packet_type"] == "TC"
    assert packet["seq_count"] == 1
    assert packet["timestamp"] == 0
    assert packet["crc_valid"] is True
    assert packet["raw_hex"] == raw.hex()


def test_invalid_crc_is_preserved_as_evidence_instead_of_dropped():
    raw = bytearray(protocol.build_ping())
    raw[-1] ^= 1
    packet = protocol.decode_packet(bytes(raw))
    assert packet["crc_valid"] is False
    assert packet["raw_hex"] == raw.hex()
    assert packet["payload_hex"]


@pytest.mark.parametrize("corruption", ["truncated", "trailing", "version", "oversized"])
def test_decode_refuses_malformed_lengths_or_unsupported_version(corruption):
    raw = protocol.build_ping()
    if corruption == "truncated":
        raw = raw[:-1]
    elif corruption == "trailing":
        raw += b"\x00"
    elif corruption == "version":
        raw = bytes([raw[0] | 0xE0]) + raw[1:]
    else:
        raw = raw[:4] + (231).to_bytes(2, "big") + bytes(232)
    with pytest.raises(ValueError):
        protocol.decode_packet(raw)


@pytest.mark.parametrize("seq_count,timestamp", [(-1, 0), (0x4000, 0), (1, -1), (1, 2**32)])
def test_ping_refuses_field_values_that_would_wrap(seq_count, timestamp):
    with pytest.raises(ValueError):
        protocol.build_ping(seq_count=seq_count, timestamp=timestamp)


def cli():
    # Naming the entry point here also exercises test_paths.py's source-module coverage guard.
    return importlib.import_module("cuberange.flatsat.__main__")


class ObservedSerial:
    """Observe serial settings, writes, and any attempt to erase queued evidence."""
    def __init__(self, *, open_error=None, read_error=None, write_error=None,
                 partial_write=False):
        self.dtr = True
        self.rts = True
        self.open_error = open_error
        self.read_error = read_error
        self.write_error = write_error
        self.partial_write = partial_write
        self.pending = bytearray(b"boot\x00\xff\r\n")
        self.written = []
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.pending)

    def open(self):
        assert self.dtr is False and self.rts is False
        assert self.baudrate == 115200
        if self.open_error:
            raise self.open_error

    def close(self):
        self.closed = True

    def reset_input_buffer(self):
        raise AssertionError("queued RX evidence was erased")

    def reset_output_buffer(self):
        raise AssertionError("output buffer was cleared")

    def read(self, size):
        if self.read_error:
            raise self.read_error
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    def write(self, data):
        if self.write_error:
            raise self.write_error
        self.written.append(data)
        return len(data) - 1 if self.partial_write else len(data)

    def flush(self):
        pass


def fake_serial_module(monkeypatch, connection):
    monkeypatch.setattr(usb, "_serial_module", lambda: SimpleNamespace(
        Serial=lambda: connection, SerialException=OSError,
    ))


def test_session_preserves_queued_rx_and_sets_control_lines_before_open(monkeypatch):
    connection = ObservedSerial()
    fake_serial_module(monkeypatch, connection)
    port = usb.select_ports(board_ports(), port="shell")[0]
    with usb.SerialSession(port) as session:
        assert session.read() == b"boot\x00\xff\r\n"
        assert connection.written == []
    assert connection.closed
    with pytest.raises(FlatSatError):
        session.read()


@pytest.mark.parametrize("role,data", [
    ("radio0", b"status\r\n"), ("radio1", b"help\r\n"),
    ("shell", b"inject_tc 00\r\n"), ("shell", b"reboot\r\n"),
    ("shell", b"status\r\nreboot\r\n"),
])
def test_transport_refuses_radio_writes_and_non_allowlisted_shell_commands(monkeypatch, role, data):
    connection = ObservedSerial()
    fake_serial_module(monkeypatch, connection)
    port = usb.select_ports(board_ports(), port=role)[0]
    with usb.SerialSession(port) as session:
        with pytest.raises(FlatSatError):
            session.write(data)
        assert connection.written == []


def test_only_complete_allowlisted_shell_queries_reach_the_transport(monkeypatch):
    connection = ObservedSerial()
    fake_serial_module(monkeypatch, connection)
    port = usb.select_ports(board_ports(), port="shell")[0]
    commands = [b"fw_version\r\n", b"status\r\n", b"sensors\r\n", b"help\r\n"]
    with usb.SerialSession(port) as session:
        for command in commands:
            session.write(command)
    assert connection.written == commands


def test_query_write_has_a_short_timeout_and_never_calls_unbounded_serial_drain(monkeypatch):
    class NoDrain(ObservedSerial):
        def flush(self):
            pytest.fail("serial flush can block outside the query deadline")

    connection = NoDrain()
    fake_serial_module(monkeypatch, connection)
    port = usb.select_ports(board_ports(), port="shell")[0]
    with usb.SerialSession(port) as session:
        assert connection.timeout == connection.write_timeout == 0.05
        session.write(b"sensors\r\n")
    assert connection.written == [b"sensors\r\n"]
    assert connection.closed


def test_permission_failure_names_device_and_is_not_reported_as_timeout(monkeypatch):
    connection = ObservedSerial(open_error=PermissionError(errno.EACCES, "access denied"))
    fake_serial_module(monkeypatch, connection)
    port = usb.select_ports(board_ports(), port="shell")[0]
    with pytest.raises(FlatSatError, match="Permission denied") as exc:
        with usb.SerialSession(port):
            pytest.fail("permission-denied port was opened")
    assert port.path in str(exc.value)
    assert connection.closed
    assert connection.written == []


@pytest.mark.parametrize("failure", ["disconnect", "write", "short-write"])
def test_transport_reports_io_failure_instead_of_silence(monkeypatch, failure):
    connection = ObservedSerial(
        read_error=OSError(errno.EIO, "disconnected") if failure == "disconnect" else None,
        write_error=OSError(errno.EIO, "disconnected") if failure == "write" else None,
        partial_write=failure == "short-write",
    )
    fake_serial_module(monkeypatch, connection)
    port = usb.select_ports(board_ports(), port="shell")[0]
    with usb.SerialSession(port) as session:
        with pytest.raises(FlatSatError):
            session.read() if failure == "disconnect" else session.write(b"status\r\n")


def test_pseudoterminal_receive_keeps_binary_bytes_and_sends_nothing():
    """Exercise real pyserial configuration without any physical USB device."""
    pytest.importorskip("serial", reason="optional FlatSat adapter requires requirements-flatsat.txt")
    master, slave = os.openpty()
    try:
        path = os.ttyname(slave)
        port = usb.discover_ports([descriptor(path, "Cat-Shell")])[0]
        payload = b"telemetry\x00\xff\x03\x11\r\n"
        with usb.SerialSession(port, timeout=0.02) as session:
            assert session.read() == b""
            os.write(master, payload)
            got = bytearray()
            deadline = time.monotonic() + 1.0
            while len(got) < len(payload) and time.monotonic() < deadline:
                got.extend(session.read())
            assert bytes(got) == payload
            assert select.select([master], [], [], 0.02)[0] == [], "receive-only session wrote data"
        with pytest.raises(FlatSatError):
            session.read()
    finally:
        os.close(master)
        os.close(slave)


def forbid_usb(monkeypatch, module):
    def forbidden(*args, **kwargs):
        pytest.fail("offline operation attempted USB discovery or IO")
    monkeypatch.setattr(module, "discover_ports", forbidden)
    monkeypatch.setattr(module, "SerialSession", forbidden)


def test_dry_run_ping_is_offline_and_non_dry_run_ping_cannot_transmit(monkeypatch, capsys):
    module = cli()
    forbid_usb(monkeypatch, module)
    assert module.main(["ping", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["transmitted"] is False
    assert preview["raw_hex"] == "1820c0010006000000001022d9"
    assert module.main(["ping"]) == 1
    error = capsys.readouterr().err
    assert "--dry-run" in error
    assert "not supported" in error


def test_devices_reads_metadata_without_opening_serial(monkeypatch, capsys):
    module = cli()
    ports = board_ports()
    monkeypatch.setattr(module, "discover_ports", lambda: ports)
    monkeypatch.setattr(module, "SerialSession", lambda *a, **k: pytest.fail("devices opened USB"))
    assert module.main(["devices", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert {p["role"] for p in found} == {"shell", "radio0", "radio1"}


def test_offline_decode_retains_bad_crc_and_returns_failure(monkeypatch, capsys):
    module = cli()
    forbid_usb(monkeypatch, module)
    raw = bytes.fromhex("1820c0010006000000001022d8")
    assert module.main(["decode", raw.hex()]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["raw_hex"] == raw.hex()
    assert result["crc_valid"] is False


def test_monitor_preserves_malformed_raw_bytes_per_interface_and_never_writes(monkeypatch, tmp_path):
    module = cli()
    ports = board_ports()
    monkeypatch.setattr(module, "discover_ports", lambda: ports)
    payloads = {
        "shell": [b"\x1b[2Jboot\r\n", b"\x00\xff"],
        "radio0": [b"\xaa", b"\x55broken"],
        "radio1": [b"\xff\x00\x01"],
    }
    closed = []

    class ReceiveOnly:
        def __init__(self, port):
            self.port = port
            self.chunks = list(payloads[port.role])

        def __enter__(self):
            return self

        def __exit__(self, *_):
            closed.append(self.port.role)

        def read(self):
            return self.chunks.pop(0) if self.chunks else b""

        def write(self, data):
            pytest.fail(f"passive monitor wrote to {self.port.role}: {data!r}")

    monkeypatch.setattr(module, "SerialSession", ReceiveOnly)
    output = tmp_path / "capture.jsonl"
    assert module.main(["monitor", "--duration", "0.02", "--output", str(output)]) == 0
    events = [json.loads(line) for line in output.read_text().splitlines()]
    rx = [event for event in events if event["event"] == "rx"]
    for role, chunks in payloads.items():
        actual = b"".join(bytes.fromhex(e["raw_hex"]) for e in rx if e["role"] == role)
        assert actual == b"".join(chunks)
    assert set(closed) == {"shell", "radio0", "radio1"}
    assert events[-1]["received_bytes"] == sum(len(b"".join(chunks)) for chunks in payloads.values())


def test_capture_refuses_overwrite_before_opening_a_device(monkeypatch, tmp_path, capsys):
    module = cli()
    monkeypatch.setattr(module, "discover_ports", board_ports)
    monkeypatch.setattr(module, "SerialSession", lambda *a, **k: pytest.fail("existing capture opened USB"))
    output = tmp_path / "capture.jsonl"
    output.write_bytes(b"previous evidence\n")
    assert module.main(["monitor", "--output", str(output), "--duration", "0.01"]) == 1
    assert output.read_bytes() == b"previous evidence\n"
    assert "exist" in capsys.readouterr().err.lower()


def test_info_refuses_radio_interface_before_open_or_write(monkeypatch, tmp_path, capsys):
    module = cli()
    monkeypatch.setattr(module, "discover_ports", board_ports)
    monkeypatch.setattr(module, "SerialSession", lambda *a, **k: pytest.fail("info opened radio USB"))
    assert module.main(["info", "--port", "radio0", "--output", str(tmp_path / "info.jsonl")]) == 1
    assert "Cat-Shell" in capsys.readouterr().err


@pytest.mark.parametrize("reply,expected", [
    ([b"status\r\n", b"RadioManager State: LISTENING\r\n"], 0),
    ([b"status\r\n"], 1),
    ([b"status\r\nunknown command\r\n"], 1),
    ([], 1),
])
def test_info_requires_a_response_beyond_echo_and_keeps_all_raw_bytes(
        monkeypatch, tmp_path, reply, expected):
    module = cli()
    monkeypatch.setattr(module, "discover_ports", board_ports)
    ticks = iter(range(1000))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(ticks) / 100))
    writes = []
    closed = []
    banner = b"boot\x00\xff\r\n"

    class LocalShell:
        def __init__(self, port):
            self.port = port
            self.chunks = [banner]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            closed.append(self.port.role)

        def read(self):
            return self.chunks.pop(0) if self.chunks else b""

        def write(self, data):
            assert self.port.role == "shell"
            writes.append(data)
            self.chunks = list(reply)

    monkeypatch.setattr(module, "SerialSession", LocalShell)
    output = tmp_path / "info.jsonl"
    assert module.main(["info", "--query", "status", "--timeout", "0.5",
                        "--output", str(output)]) == expected
    assert writes == [b"status\r\n"]
    assert closed == ["shell"]
    events = [json.loads(line) for line in output.read_text().splitlines()]
    assert b"".join(bytes.fromhex(e["raw_hex"]) for e in events if e["event"] == "rx") == (
        banner + b"".join(reply))
    results = [e for e in events if e["event"] == "query_result"]
    assert len(results) == 1
    assert results[0]["command"] == "status"
    assert results[0]["answered"] is (expected == 0)
    assert events[-1]["success"] is (expected == 0)


def test_wrapper_runs_offline_from_another_working_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "flatsat.py"), "ping", "--dry-run"],
        cwd=tmp_path, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    result_json = json.loads(result.stdout)
    assert result_json["raw_hex"] == "1820c0010006000000001022d9"
    assert result_json["transmitted"] is False


@pytest.mark.parametrize("duration", ["0", "-1", "nan", "inf"])
def test_monitor_invalid_duration_is_rejected_before_usb_discovery(monkeypatch, duration):
    module = cli()
    forbid_usb(monkeypatch, module)
    with pytest.raises(SystemExit) as exc:
        module.main(["monitor", "--duration", duration])
    assert exc.value.code == 2
