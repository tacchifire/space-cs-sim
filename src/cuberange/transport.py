"""The frame transport boundary: one interface for every wire a frame can travel on.

Today the ground station talks to the satellite over exactly one wire: a TCP socket into a Renode
socket terminal (see gs.link.SpaceLink). Phase 3 of the testbed roadmap puts real hardware behind
the same ground station - a serial cable, a USB-CAN adapter - and the exercises above this layer
should not be able to tell the difference. That only works if the boundary is cut *before* the
hardware arrives, while the socket implementation is the only one and the interface can still be
checked against a single, well-understood behaviour. Cutting it afterwards, under pressure to make
a board work, is how an interface quietly grows a `if isinstance(transport, Serial...)` in every
caller.

So this module defines the narrow thing every frame wire has in common:

    connect()          open the wire
    send_frame(frame)  put one frame on it, CubeRange lab framing applied
    poll()             return the complete frames received since the last call, never blocking long
    close()            let go of the wire

Framing is part of the boundary, deliberately. proto.frame's wrap/Deframer is the lab framing every
implementation shares, so a caller hands over a bare frame and gets bare frames back, and two
transports can be conformance-tested against each other byte for byte. A wire that cannot carry the
lab framing does not get a different contract; it gets an adapter below this one.

What is NOT in the interface matters as much. Real hardware can do things a socket cannot: cut the
satellite's power, hold it in reset, reflash it. Those abilities are real, and phase 3 will need
them, but they are not properties of *a wire that carries frames*, and forcing them into
FrameTransport would leave SpaceLink either lying (a no-op power_cut) or refusing (raising
NotImplementedError from a method its type promises). Both lies teach something false. Instead,
hardware-specific abilities live on the concrete class that actually has them, and code that needs
one asks for that class - or for a separate capability interface defined next to it when the time
comes. `capabilities` below is the modest version of the same idea: it names what kind of wire this
is ("socket", "serial") so tooling can describe and select transports, and it is a description,
never a dispatch mechanism. Callers must not branch behaviour on it; if a behaviour matters, it
belongs in the contract or in a type, not in a string set.
"""
from __future__ import annotations

import os
import select
import termios
from abc import ABC, abstractmethod
from typing import Optional

from .proto.frame import Deframer, wrap

# Baud rates SerialFrameTransport knows how to ask termios for. A rate not in this table is refused
# outright rather than silently approximated: a wire running at the wrong speed delivers garbage
# that the deframer quite correctly discards, which presents as a satellite that never answers.
_BAUD_CONSTANTS = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
    230400: termios.B230400,
    460800: termios.B460800,
}


class FrameTransport(ABC):
    """One wire that carries CubeRange lab-framed frames. See the module docstring for the rules.

    Implementations must keep two behaviours that callers quietly depend on, both inherited from
    SpaceLink's years as the only transport:

    - poll() distinguishes a quiet wire (returns []) from a dead one (raises ConnectionError).
      A ground station that cannot tell those apart will report a disconnected satellite as one
      that simply had nothing to say.
    - send_frame()/poll() before connect() raise RuntimeError, not an OS error from deep in the
      driver, so a sequencing bug in the caller reads as what it is.
    """

    #: What kind of wire this is, as plain description for tooling and logs: e.g. {"socket"}.
    #: Never branch behaviour on this; see the module docstring.
    capabilities: frozenset = frozenset()

    @abstractmethod
    def connect(self) -> None:
        """Open the wire, retrying or waiting as the medium reasonably requires."""

    @abstractmethod
    def send_frame(self, frame: bytes) -> None:
        """Put one frame on the wire, lab framing applied."""

    @abstractmethod
    def poll(self) -> list:
        """Return any complete frames received since the last call. Never blocks for long."""

    @abstractmethod
    def close(self) -> None:
        """Release the wire. Safe to call when never connected, and more than once."""


class SerialFrameTransport(FrameTransport):
    """A FrameTransport over a character device: a real UART, a USB serial adapter, a pty.

    Standard library only (os/termios) - pyserial is one less dependency between a student and a
    board, and everything needed here is a dozen termios flags. The device is put into raw mode at
    the requested baud rate: no echo, no canonical line editing, no software flow control, because
    the byte stream carries binary frames and any of those would eat or invent bytes inside one.
    Settings are intentionally minimal - device path, baud rate, poll timeout. Parity, stop bits
    and hardware flow control stay at the raw-mode defaults until a real board demands otherwise;
    adding knobs nobody has needed yet is how a constructor becomes a second protocol.
    """

    capabilities = frozenset({"serial"})

    def __init__(self, device: str, baudrate: int = 115200, timeout: float = 0.2):
        if baudrate not in _BAUD_CONSTANTS:
            raise ValueError(
                f"unsupported baud rate {baudrate}; known rates: {sorted(_BAUD_CONSTANTS)}")
        self._device = device
        self._baudrate = baudrate
        self._timeout = timeout
        self._fd: Optional[int] = None
        self._deframer = Deframer()
        self.raw_rx = bytearray()      # kept for forensics: malformed traffic is evidence

    def connect(self) -> None:
        fd = os.open(self._device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attrs = termios.tcgetattr(fd)
            baud = _BAUD_CONSTANTS[self._baudrate]
            attrs[4] = baud            # input speed
            attrs[5] = baud            # output speed
            # Raw mode, spelled out rather than via tty.setraw so the intent survives review:
            attrs[0] &= ~(termios.IXON | termios.IXOFF | termios.ICRNL | termios.BRKINT
                          | termios.INPCK | termios.ISTRIP)
            attrs[1] &= ~termios.OPOST
            attrs[2] |= termios.CLOCAL | termios.CREAD
            attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
            attrs[2] |= termios.CS8
            attrs[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG
                          | termios.IEXTEN)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIOFLUSH)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def send_frame(self, frame: bytes) -> None:
        if self._fd is None:
            raise RuntimeError("connect() first")
        view = memoryview(wrap(frame))
        while view:
            written = os.write(self._fd, view)
            view = view[written:]

    def poll(self) -> list:
        """Return any complete frames received since the last call. Never blocks for long."""
        if self._fd is None:
            raise RuntimeError("connect() first")
        ready, _, _ = select.select([self._fd], [], [], self._timeout)
        if not ready:
            return []
        try:
            chunk = os.read(self._fd, 4096)
        except BlockingIOError:
            return []
        except OSError as exc:
            # A pty whose far end has gone away reads as EIO, not as an orderly EOF. Both mean the
            # wire is dead, and the caller was promised ConnectionError for exactly that.
            raise ConnectionError("serial link failed") from exc
        if not chunk:
            raise ConnectionError("serial link closed by the far end")
        self.raw_rx.extend(chunk)
        return self._deframer.feed(chunk)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
