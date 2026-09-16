"""Wire format shared with the Android app (see Protocol.kt).

Every message is: 1 byte type, 4 byte big-endian payload length, payload.
"""
from __future__ import annotations

import json
import socket
import struct
import threading

PORT = 27183
VERSION = 1

HELLO = 0x01        # JSON {"version": 1}
MIC_PCM = 0x10      # tablet -> laptop, s16le 48k mono
SPK_PCM = 0x20      # laptop -> tablet, s16le 48k stereo
FRAME_JPEG = 0x30   # laptop -> tablet
CONTROL = 0x40      # laptop -> tablet, JSON {"mic": bool}

SAMPLE_RATE = 48000
MAX_PAYLOAD = 16 * 1024 * 1024

_HEADER = struct.Struct("!BI")


class Connection:
    """Thread-safe framed socket wrapper."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._send_lock = threading.Lock()
        self._rfile = sock.makefile("rb", buffering=256 * 1024)

    def send(self, msg_type: int, payload: bytes) -> None:
        with self._send_lock:
            self.sock.sendall(_HEADER.pack(msg_type, len(payload)) + payload)

    def send_json(self, msg_type: int, obj: dict) -> None:
        self.send(msg_type, json.dumps(obj).encode())

    def recv(self) -> tuple[int, bytes]:
        header = self._rfile.read(_HEADER.size)
        if len(header) < _HEADER.size:
            raise ConnectionError("connection closed")
        msg_type, length = _HEADER.unpack(header)
        if length > MAX_PAYLOAD:
            raise ConnectionError(f"bad frame length {length}")
        payload = self._rfile.read(length)
        if len(payload) < length:
            raise ConnectionError("connection closed mid-frame")
        return msg_type, payload

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
