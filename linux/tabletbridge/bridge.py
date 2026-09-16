"""Connection orchestration: adb forward -> TCP -> feature streams."""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable, Optional

from . import adb, audio, protocol

log = logging.getLogger("tabletbridge")


class Bridge:
    """Owns the connection to the tablet and the mic/speaker/screen streams.

    All public methods are safe to call from the UI thread.
    """

    def __init__(self, on_status: Callable[[str, bool], None]):
        self._on_status = on_status
        self._conn: Optional[protocol.Connection] = None
        self._serial: Optional[str] = None
        self._lock = threading.RLock()
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self._mic: Optional[audio.TabletMic] = None
        self._speaker: Optional[audio.TabletSpeaker] = None
        self._screen = None

        self.screen_monitor = 1
        self.screen_fps = 15
        self.screen_quality = 60

    # ------------------------------------------------------------ connection

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def connect(self) -> None:
        with self._lock:
            if self._conn:
                return
            serial = adb.first_ready_device()
            adb.forward(serial)
            try:
                adb.launch_app(serial)
            except adb.AdbError as e:
                raise adb.AdbError("Tablet Bridge app not installed on the tablet. Install the APK first.") from e
            sock = self._dial()
            conn = protocol.Connection(sock)
            conn.send_json(protocol.HELLO, {"version": protocol.VERSION, "client": "linux"})
            msg_type, payload = conn.recv()
            if msg_type != protocol.HELLO:
                conn.close()
                raise ConnectionError("Unexpected reply from tablet")
            self._serial = serial
            self._conn = conn
            self._stop.clear()
            self._reader = threading.Thread(target=self._read_loop, name="reader", daemon=True)
            self._reader.start()
            log.info("connected to %s (%s)", serial, payload.decode(errors="replace"))
            self._on_status(f"Connected to {serial}", True)

    def _dial(self) -> socket.socket:
        deadline = time.monotonic() + 10
        last: Exception = ConnectionError("timeout")
        while time.monotonic() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", protocol.PORT), timeout=3)
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # adb accepts the forward immediately; the app may still be starting, so the
                # HELLO handshake (with a timeout) is what actually proves it's there.
                sock.settimeout(3)
                return sock
            except OSError as e:
                last = e
                time.sleep(0.3)
        raise ConnectionError(f"Could not reach the app on the tablet: {last}")

    def disconnect(self, reason: str = "Disconnected") -> None:
        with self._lock:
            self._stop.set()
            self.set_screen(False)
            self.set_speaker(False)
            self.set_mic(False)
            if self._conn:
                self._conn.close()
                self._conn = None
            if self._serial:
                adb.remove_forward(self._serial)
                self._serial = None
        self._on_status(reason, False)

    def _read_loop(self) -> None:
        conn = self._conn
        assert conn is not None
        conn.sock.settimeout(None)
        try:
            while not self._stop.is_set():
                msg_type, payload = conn.recv()
                if msg_type == protocol.MIC_PCM:
                    mic = self._mic
                    if mic:
                        mic.write(payload)
        except (ConnectionError, OSError) as e:
            if not self._stop.is_set():
                log.info("connection lost: %s", e)
                threading.Thread(target=self.disconnect, args=("Connection lost - reconnect the tablet",), daemon=True).start()

    def _send(self, msg_type: int, payload: bytes) -> None:
        conn = self._conn
        if conn is None:
            raise ConnectionError("not connected")
        conn.send(msg_type, payload)

    # ------------------------------------------------------------ features

    def set_mic(self, enabled: bool) -> None:
        with self._lock:
            if enabled and not self._mic:
                if self._conn is None:
                    raise ConnectionError("not connected")
                mic = audio.TabletMic()
                mic.start()
                self._mic = mic
                self._conn.send_json(protocol.CONTROL, {"mic": True})
            elif not enabled and self._mic:
                if self._conn:
                    try:
                        self._conn.send_json(protocol.CONTROL, {"mic": False})
                    except OSError:
                        pass
                self._mic.stop()
                self._mic = None

    def set_speaker(self, enabled: bool) -> None:
        with self._lock:
            if enabled and not self._speaker:
                spk = audio.TabletSpeaker(lambda pcm: self._send(protocol.SPK_PCM, pcm))
                spk.start()
                self._speaker = spk
            elif not enabled and self._speaker:
                self._speaker.stop()
                self._speaker = None

    def set_screen(self, enabled: bool) -> None:
        with self._lock:
            if enabled and not self._screen:
                from .screen import ScreenStreamer  # optional dependency (mss, pillow)
                streamer = ScreenStreamer(
                    lambda jpeg: self._send(protocol.FRAME_JPEG, jpeg),
                    monitor=self.screen_monitor, fps=self.screen_fps, quality=self.screen_quality,
                )
                streamer.start()
                self._screen = streamer
            elif not enabled and self._screen:
                self._screen.stop()
                self._screen = None
