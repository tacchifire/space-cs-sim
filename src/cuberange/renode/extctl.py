#!/usr/bin/env python3
"""
Pure-Python client for the Renode 1.16.1 External Control API.
No ctypes, no C dependency. Reversed from
tools/external_control_client/lib/renode_api.c

WIRE PROTOCOL
=============
Transport: plain TCP, all integers little-endian.

Handshake (client -> server), 14 bytes for API version table v1.16.1:
    u16 n_entries (=6)  then n_entries * { u8 command_id, u8 version }
    06 00 | 01 00 | 02 00 | 03 00 | 04 00 | 05 01 | 06 00
Server replies with ONE byte: 0x05 (SUCCESS_HANDSHAKE) on match.

Request frame (client -> server):
    'R' 'E' | u8 command | u32 payload_len | payload[payload_len]

Response frame (server -> client), first byte is the return code:
    0 COMMAND_FAILED       : rc | u8 cmd | u32 len | utf8 message
    1 FATAL_ERROR          : rc |         u32 len | utf8 message      (NO cmd byte)
    2 INVALID_COMMAND      : rc | u8 cmd
    3 SUCCESS_WITH_DATA    : rc | u8 cmd | u32 len | data
    4 SUCCESS_WITHOUT_DATA : rc | u8 cmd
    5 SUCCESS_HANDSHAKE    : rc
    6 ASYNC_EVENT          : rc | u8 cmd | u32 event_descriptor | u32 len | data
"""

import socket
import struct
import collections

MAGIC = b"RE"

# api_command_t
CMD_RUN_FOR = 1
CMD_GET_TIME = 2
CMD_GET_MACHINE = 3
CMD_ADC = 4
CMD_GPIO = 5
CMD_SYSBUS = 6

# ReturnCode
RC_COMMAND_FAILED = 0
RC_FATAL_ERROR = 1
RC_INVALID_COMMAND = 2
RC_SUCCESS_WITH_DATA = 3
RC_SUCCESS_WITHOUT_DATA = 4
RC_SUCCESS_HANDSHAKE = 5
RC_ASYNC_EVENT = 6

# API version table the client claims to speak (command_id, version)
API_VERSIONS = [(CMD_RUN_FOR, 0), (CMD_GET_TIME, 0), (CMD_GET_MACHINE, 0),
                (CMD_ADC, 0), (CMD_GPIO, 1), (CMD_SYSBUS, 0)]

# sub-commands
ADC_GET_CHANNEL_COUNT, ADC_GET_CHANNEL_VALUE, ADC_SET_CHANNEL_VALUE = 0, 1, 2
GPIO_GET_STATE, GPIO_SET_STATE, GPIO_REGISTER_EVENT = 0, 1, 2
SYSBUS_READ, SYSBUS_WRITE = 0, 1

# renode_access_width_t
AW_MULTI_BYTE, AW_BYTE, AW_WORD, AW_DOUBLE_WORD, AW_QUAD_WORD = 0, 1, 2, 4, 8

US, MS, S = 1, 1000, 1000000

GpioEvent = collections.namedtuple("GpioEvent", "ed timestamp_us state raw")


class RenodeError(Exception):
    pass


class Renode(object):
    def __init__(self, port, host="127.0.0.1", timeout=None, trace=False):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.trace = trace
        self.events = collections.deque()   # unconsumed ASYNC_EVENT frames
        self._handshake()

    # ---------- low level ----------
    def _send(self, b):
        if self.trace:
            print("  TX %s" % b.hex())
        self.sock.sendall(b)

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RenodeError("socket closed by Renode")
            buf += chunk
        if self.trace:
            print("  RX %s" % buf.hex())
        return buf

    def _handshake(self):
        payload = struct.pack("<H", len(API_VERSIONS))
        for cmd, ver in API_VERSIONS:
            payload += struct.pack("<BB", cmd, ver)
        self._send(payload)
        # A control client that died mid-run_for leaves its GPIO registrations
        # alive in the server; those ASYNC_EVENT frames are pushed onto the NEXT
        # accepted socket *before* the handshake ack.  The shipped C library
        # mis-reports this as "API command version mismatch".  Skip them.
        self.orphan_events = []
        while True:
            rc = self._recv(1)[0]
            if rc == RC_SUCCESS_HANDSHAKE:
                return
            if rc != RC_ASYNC_EVENT:
                raise RenodeError("handshake rejected, return code %d" % rc)
            self._recv(1)                                   # command byte
            ed, size = struct.unpack("<II", self._recv(8))
            data = self._recv(size) if size else b""
            ts, state = struct.unpack_from("<Q?", data, 0)
            self.orphan_events.append(GpioEvent(ed, ts, state, data))

    def _read_response(self):
        """Read one frame. Returns ('event', GpioEvent) or ('reply', cmd, data)."""
        rc = self._recv(1)[0]
        if rc == RC_ASYNC_EVENT:
            cmd = self._recv(1)[0]
            ed, size = struct.unpack("<II", self._recv(8))
            data = self._recv(size) if size else b""
            ts, state = struct.unpack_from("<Q?", data, 0)
            return ("event", GpioEvent(ed, ts, state, data))
        if rc == RC_FATAL_ERROR:
            (size,) = struct.unpack("<I", self._recv(4))
            msg = self._recv(size).decode("utf-8", "replace") if size else ""
            raise RenodeError("FATAL_ERROR: %s" % msg)
        cmd = self._recv(1)[0]
        if rc == RC_COMMAND_FAILED:
            (size,) = struct.unpack("<I", self._recv(4))
            msg = self._recv(size).decode("utf-8", "replace") if size else ""
            raise RenodeError("COMMAND_FAILED(cmd=%d): %s" % (cmd, msg))
        if rc == RC_INVALID_COMMAND:
            raise RenodeError("INVALID_COMMAND(cmd=%d)" % cmd)
        if rc == RC_SUCCESS_WITHOUT_DATA:
            return ("reply", cmd, b"")
        if rc == RC_SUCCESS_WITH_DATA:
            (size,) = struct.unpack("<I", self._recv(4))
            return ("reply", cmd, self._recv(size) if size else b"")
        raise RenodeError("unexpected return code %d" % rc)

    def _cmd(self, command, payload=b""):
        self._send(MAGIC + struct.pack("<BI", command, len(payload)) + payload)
        while True:
            r = self._read_response()
            if r[0] == "event":
                self.events.append(r[1])
                continue
            _, cmd, data = r
            if cmd != command:
                raise RenodeError("command mismatch: sent %d got %d" % (command, cmd))
            return data

    # ---------- API ----------
    def close(self):
        self.sock.close()

    def run_for(self, value, unit=MS):
        self._cmd(CMD_RUN_FOR, struct.pack("<Q", value * unit))

    def get_current_time_us(self):
        return struct.unpack("<Q", self._cmd(CMD_GET_TIME))[0]

    def get_machine(self, name):
        n = name.encode()
        return struct.unpack("<i", self._cmd(CMD_GET_MACHINE,
                                             struct.pack("<i", len(n)) + n))[0]

    def _get_instance(self, command, md, name):
        n = name.encode()
        return struct.unpack("<i", self._cmd(
            command, struct.pack("<iii", -1, md, len(n)) + n))[0]

    def get_gpio(self, md, name):
        return self._get_instance(CMD_GPIO, md, name)

    def get_adc(self, md, name):
        return self._get_instance(CMD_ADC, md, name)

    def get_bus_context(self, md, name="sysbus"):
        return self._get_instance(CMD_SYSBUS, md, name)

    def gpio_get_state(self, gid, pin):
        d = self._cmd(CMD_GPIO, struct.pack("<ibi", gid, GPIO_GET_STATE, pin))
        return bool(d[0])

    def gpio_set_state(self, gid, pin, state):
        self._cmd(CMD_GPIO, struct.pack("<ibiB", gid, GPIO_SET_STATE, pin, 1 if state else 0))

    def gpio_register_event(self, gid, pin, ed):
        self._cmd(CMD_GPIO, struct.pack("<ibii", gid, GPIO_REGISTER_EVENT, pin, ed))

    def adc_channel_count(self, aid):
        return struct.unpack("<i", self._cmd(CMD_ADC, struct.pack("<ib", aid, ADC_GET_CHANNEL_COUNT)))[0]

    def adc_get_channel(self, aid, ch):
        return struct.unpack("<I", self._cmd(CMD_ADC, struct.pack("<ibi", aid, ADC_GET_CHANNEL_VALUE, ch)))[0]

    def adc_set_channel(self, aid, ch, microvolts):
        self._cmd(CMD_ADC, struct.pack("<ibiI", aid, ADC_SET_CHANNEL_VALUE, ch, microvolts))

    def sysbus_read(self, ctx, address, width=AW_MULTI_BYTE, count=4):
        return self._cmd(CMD_SYSBUS,
                         struct.pack("<iBBQI", ctx, SYSBUS_READ, width, address, count))

    def sysbus_write(self, ctx, address, data, width=AW_MULTI_BYTE, count=None):
        if count is None:
            count = len(data) if width == AW_MULTI_BYTE else len(data) // width
        self._cmd(CMD_SYSBUS,
                  struct.pack("<iBBQI", ctx, SYSBUS_WRITE, width, address, count) + data)

    # ---------- event pump ----------
    def poll_event(self, timeout=0.0):
        """Return a queued GpioEvent, or read one if it arrives within timeout."""
        if self.events:
            return self.events.popleft()
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            r = self._read_response()
        except socket.timeout:
            return None
        finally:
            self.sock.settimeout(old)
        if r[0] != "event":
            raise RenodeError("expected event, got reply cmd=%d" % r[1])
        return r[1]

    def fileno(self):
        return self.sock.fileno()
