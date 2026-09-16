"""Screen capture -> JPEG frames, streamed to the tablet."""
from __future__ import annotations

import io
import threading
import time
from typing import Callable, Optional

try:
    import mss
    from PIL import Image
except ImportError as e:  # pragma: no cover
    raise ImportError("Screen sharing needs `pip install mss pillow`") from e


def list_monitors() -> list[str]:
    with mss.mss() as sct:
        return [f"{i}: {m['width']}x{m['height']} @ {m['left']},{m['top']}" for i, m in enumerate(sct.monitors) if i > 0]


class ScreenStreamer:
    def __init__(self, on_jpeg: Callable[[bytes], None], monitor: int = 1, fps: int = 15,
                 quality: int = 60, max_width: int = 1600):
        self._on_jpeg = on_jpeg
        self.monitor = monitor
        self.fps = fps
        self.quality = quality
        self.max_width = max_width
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="screen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        interval = 1.0 / max(1, self.fps)
        last_bytes: Optional[bytes] = None
        with mss.mss() as sct:
            while not self._stop.is_set():
                t0 = time.monotonic()
                try:
                    mon = sct.monitors[self.monitor]
                    shot = sct.grab(mon)
                except Exception:
                    time.sleep(0.5)
                    continue
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                if img.width > self.max_width:
                    h = int(img.height * self.max_width / img.width)
                    img = img.resize((self.max_width, h), Image.BILINEAR)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=self.quality, optimize=False)
                data = buf.getvalue()
                if data != last_bytes:  # skip identical frames (static screen)
                    last_bytes = data
                    try:
                        self._on_jpeg(data)
                    except Exception:
                        return
                dt = time.monotonic() - t0
                if dt < interval:
                    time.sleep(interval - dt)
