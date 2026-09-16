"""Minimal Tkinter UI."""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk

from .bridge import Bridge

log = logging.getLogger("tabletbridge")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Tablet Bridge")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._quit)

        self.bridge = Bridge(on_status=self._status_from_thread)

        pad = {"padx": 12, "pady": 6}
        frame = ttk.Frame(root, padding=12)
        frame.grid(sticky="nsew")

        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(frame, textvariable=self.status_var, wraplength=360).grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        self.connect_btn = ttk.Button(frame, text="Connect", command=self._toggle_connect)
        self.connect_btn.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Separator(frame).grid(row=2, column=0, columnspan=2, sticky="ew", pady=6)

        self.mic_var = tk.BooleanVar()
        self.spk_var = tk.BooleanVar()
        self.scr_var = tk.BooleanVar()
        self.mic_cb = ttk.Checkbutton(frame, text="Use tablet microphone  (source: tablet_mic)", variable=self.mic_var,
                                      command=lambda: self._toggle("mic", self.mic_var))
        self.spk_cb = ttk.Checkbutton(frame, text="Use tablet speaker  (sink: tablet_speaker)", variable=self.spk_var,
                                      command=lambda: self._toggle("speaker", self.spk_var))
        self.scr_cb = ttk.Checkbutton(frame, text="Mirror screen to tablet", variable=self.scr_var,
                                      command=lambda: self._toggle("screen", self.scr_var))
        self.mic_cb.grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        self.spk_cb.grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        self.scr_cb.grid(row=5, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(frame, text="Monitor to mirror:").grid(row=6, column=0, sticky="w", **pad)
        self.monitor_var = tk.StringVar(value="1")
        self.monitor_box = ttk.Combobox(frame, textvariable=self.monitor_var, width=28, state="readonly")
        self.monitor_box.grid(row=6, column=1, sticky="w", **pad)
        self.monitor_box.bind("<<ComboboxSelected>>", self._monitor_changed)
        self._fill_monitors()

        ttk.Label(frame, text="Screen FPS:").grid(row=7, column=0, sticky="w", **pad)
        self.fps_var = tk.IntVar(value=15)
        ttk.Spinbox(frame, from_=1, to=30, textvariable=self.fps_var, width=6, command=self._fps_changed).grid(row=7, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Tip: pick 'Tablet_Microphone' / 'Tablet_Speaker' in your system sound settings.",
                  wraplength=360, foreground="#666").grid(row=8, column=0, columnspan=2, sticky="w", **pad)

        self._set_connected(False)

    # ------------------------------------------------------------ helpers

    def _fill_monitors(self) -> None:
        try:
            from .screen import list_monitors
            monitors = list_monitors()
        except Exception as e:  # mss/pillow missing or no display
            monitors = ["1: (screen capture unavailable: %s)" % e]
        self.monitor_box["values"] = monitors
        if monitors:
            self.monitor_box.current(0)

    def _monitor_changed(self, _event=None) -> None:
        try:
            self.bridge.screen_monitor = int(self.monitor_var.get().split(":")[0])
        except ValueError:
            pass
        if self.scr_var.get():
            self._toggle("screen", self.scr_var, restart=True)

    def _fps_changed(self) -> None:
        self.bridge.screen_fps = self.fps_var.get()
        if self.scr_var.get():
            self._toggle("screen", self.scr_var, restart=True)

    def _status_from_thread(self, text: str, connected: bool) -> None:
        self.root.after(0, lambda: (self.status_var.set(text), self._set_connected(connected)))

    def _set_connected(self, connected: bool) -> None:
        self.connect_btn.config(text="Disconnect" if connected else "Connect", state="normal")
        state = "normal" if connected else "disabled"
        for cb in (self.mic_cb, self.spk_cb, self.scr_cb):
            cb.config(state=state)
        if not connected:
            for var in (self.mic_var, self.spk_var, self.scr_var):
                var.set(False)

    # ------------------------------------------------------------ actions

    def _toggle_connect(self) -> None:
        self.connect_btn.config(state="disabled")
        if self.bridge.connected:
            self.status_var.set("Disconnecting…")
            threading.Thread(target=self.bridge.disconnect, daemon=True).start()
        else:
            self.status_var.set("Connecting… (make sure the tablet is plugged in with USB debugging on)")
            threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self) -> None:
        try:
            self.bridge.connect()
        except Exception as e:
            log.exception("connect failed")
            self._status_from_thread(f"Connect failed: {e}", False)

    def _toggle(self, feature: str, var: tk.BooleanVar, restart: bool = False) -> None:
        setter = {"mic": self.bridge.set_mic, "speaker": self.bridge.set_speaker, "screen": self.bridge.set_screen}[feature]
        enabled = var.get()

        def work():
            try:
                if restart:
                    setter(False)
                setter(enabled)
            except Exception as e:
                log.exception("%s toggle failed", feature)
                self.root.after(0, lambda: (var.set(False), self.status_var.set(f"{feature}: {e}")))

        threading.Thread(target=work, daemon=True).start()

    def _quit(self) -> None:
        try:
            self.bridge.disconnect()
        finally:
            self.root.destroy()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = tk.Tk()
    App(root)
    root.mainloop()
