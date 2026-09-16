"""Virtual audio devices via PulseAudio / PipeWire (pactl, parec).

- Mic:     module-pipe-source creates a source "tablet_mic"; we write PCM from the tablet into its FIFO.
- Speaker: module-null-sink creates a sink "tablet_speaker"; we `parec` its monitor and send the PCM to the tablet.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from typing import Callable, Optional

from . import protocol

MIC_SOURCE = "tablet_mic"
SPEAKER_SINK = "tablet_speaker"


class AudioError(RuntimeError):
    pass


def _pactl(*args: str) -> str:
    exe = shutil.which("pactl")
    if exe is None:
        raise AudioError("pactl not found. Install pulseaudio-utils (works with PipeWire too).")
    proc = subprocess.run([exe, *args], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise AudioError((proc.stderr or proc.stdout).strip() or f"pactl {' '.join(args)} failed")
    return proc.stdout.strip()


def _unload_named(kind: str, name: str) -> None:
    """Unload any leftover module owning a sink/source with this name (e.g. after a crash)."""
    try:
        out = _pactl("list", "short", kind)
    except AudioError:
        return
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1] == name:
            subprocess.run(["pactl", "unload-module", parts[2]], capture_output=True)


class TabletMic:
    """Exposes the tablet microphone as a PulseAudio source."""

    def __init__(self):
        self._module: Optional[str] = None
        self._fifo_path: Optional[str] = None
        self._fifo = None
        self._lock = threading.Lock()

    def start(self) -> None:
        _unload_named("sources", MIC_SOURCE)
        tmpdir = tempfile.mkdtemp(prefix="tabletbridge-")
        self._fifo_path = os.path.join(tmpdir, "mic.fifo")
        self._module = _pactl(
            "load-module", "module-pipe-source",
            f"source_name={MIC_SOURCE}",
            f"file={self._fifo_path}",
            "format=s16le", f"rate={protocol.SAMPLE_RATE}", "channels=1",
            f"source_properties=device.description=Tablet_Microphone",
        )
        # Opening for write blocks until the module opens the read end.
        self._fifo = open(self._fifo_path, "wb", buffering=0)

    def write(self, pcm: bytes) -> None:
        with self._lock:
            if self._fifo is not None:
                try:
                    self._fifo.write(pcm)
                except (BrokenPipeError, OSError):
                    pass

    def stop(self) -> None:
        with self._lock:
            if self._fifo is not None:
                try:
                    self._fifo.close()
                except OSError:
                    pass
                self._fifo = None
        if self._module:
            subprocess.run(["pactl", "unload-module", self._module], capture_output=True)
            self._module = None
        if self._fifo_path:
            try:
                os.unlink(self._fifo_path)
                os.rmdir(os.path.dirname(self._fifo_path))
            except OSError:
                pass
            self._fifo_path = None


class TabletSpeaker:
    """Exposes the tablet speaker as a PulseAudio sink; captured PCM is passed to `on_pcm`."""

    def __init__(self, on_pcm: Callable[[bytes], None]):
        self._on_pcm = on_pcm
        self._module: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if shutil.which("parec") is None:
            raise AudioError("parec not found. Install pulseaudio-utils.")
        _unload_named("sinks", SPEAKER_SINK)
        self._module = _pactl(
            "load-module", "module-null-sink",
            f"sink_name={SPEAKER_SINK}",
            f"rate={protocol.SAMPLE_RATE}", "channels=2",
            "sink_properties=device.description=Tablet_Speaker",
        )
        self._proc = subprocess.Popen(
            [
                "parec", f"--device={SPEAKER_SINK}.monitor",
                "--format=s16le", f"--rate={protocol.SAMPLE_RATE}", "--channels=2",
                "--latency-msec=20", "--raw",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._pump, name="speaker-pump", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        chunk = protocol.SAMPLE_RATE // 50 * 4  # 20 ms stereo s16
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            data = proc.stdout.read(chunk)
            if not data:
                break
            try:
                self._on_pcm(data)
            except Exception:
                break

    def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._module:
            subprocess.run(["pactl", "unload-module", self._module], capture_output=True)
            self._module = None
