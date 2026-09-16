"""Thin wrapper around the adb CLI."""
from __future__ import annotations

import shutil
import subprocess

from . import protocol


class AdbError(RuntimeError):
    pass


def _run(*args: str, timeout: float = 15) -> str:
    exe = shutil.which("adb")
    if exe is None:
        raise AdbError("adb not found. Install android-tools-adb (Debian/Ubuntu) or android-tools (Arch/Fedora).")
    try:
        proc = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"adb {' '.join(args)} timed out") from e
    if proc.returncode != 0:
        raise AdbError((proc.stderr or proc.stdout).strip() or f"adb {' '.join(args)} failed")
    return proc.stdout


def devices() -> list[tuple[str, str]]:
    """Return [(serial, state)] for attached devices."""
    out = _run("devices")
    result = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            result.append((parts[0], parts[1]))
    return result


def first_ready_device() -> str:
    devs = devices()
    if not devs:
        raise AdbError("No tablet found. Plug in via USB and enable USB debugging (Developer options).")
    for serial, state in devs:
        if state == "device":
            return serial
    states = ", ".join(f"{s} ({st})" for s, st in devs)
    raise AdbError(f"Tablet not ready: {states}. Accept the 'Allow USB debugging' prompt on the tablet.")


def forward(serial: str, port: int = protocol.PORT) -> None:
    _run("-s", serial, "forward", f"tcp:{port}", f"tcp:{port}")


def remove_forward(serial: str, port: int = protocol.PORT) -> None:
    try:
        _run("-s", serial, "forward", "--remove", f"tcp:{port}")
    except AdbError:
        pass


def launch_app(serial: str) -> None:
    _run("-s", serial, "shell", "am", "start", "-n", "com.tabletbridge/.MainActivity")
