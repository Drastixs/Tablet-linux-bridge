# Tablet Bridge

Use an Android tablet's **microphone**, **speaker** and **screen** from a Linux laptop over a plain USB cable.
All data travels through `adb forward` (USB debugging) — no Wi-Fi, no pairing, no root.

```
 Linux laptop                              Android tablet
 ┌───────────────────────┐   adb forward   ┌────────────────────┐
 │ tabletbridge (Tk UI)  │ ──── USB ─────▶ │ Tablet Bridge app  │
 │  tablet_mic  (source) │ ◀── mic PCM ─── │  AudioRecord       │
 │  tablet_speaker (sink)│ ─── spk PCM ──▶ │  AudioTrack        │
 │  screen capture (mss) │ ─── JPEG ─────▶ │  full-screen view  │
 └───────────────────────┘                 └────────────────────┘
```

* `android/` – Kotlin app. Listens on `localhost:27183`, streams the mic, plays audio, shows frames.
* `linux/`   – Python app (Tkinter). Sets up `adb forward`, creates PulseAudio/PipeWire virtual devices, captures the screen.

## Setup

### Tablet
1. Enable *Developer options → USB debugging*.
2. Build and install the app (or install a prebuilt APK):
   ```sh
   cd android && ./gradlew installDebug      # needs Android SDK; set ANDROID_HOME or android/local.properties
   ```
3. Plug the tablet into the laptop and accept the "Allow USB debugging?" prompt.

### Laptop (Debian/Ubuntu shown)
```sh
sudo apt install adb pulseaudio-utils python3-tk python3-pip
cd linux
pip install -r requirements.txt     # mss + pillow, only needed for screen mirroring
python3 -m tabletbridge
```
`pactl`/`parec` work with both PulseAudio and PipeWire (via pipewire-pulse).

## Use
1. Click **Connect**. The laptop launches the app on the tablet and connects through `adb forward`.
2. Tick the features you want:
   * **Use tablet microphone** – a new input device **Tablet_Microphone** appears in your sound settings.
   * **Use tablet speaker** – a new output device **Tablet_Speaker** appears; anything sent to it plays on the tablet.
   * **Mirror screen to tablet** – the selected monitor is mirrored to the tablet full-screen (JPEG, default 15 fps).
3. Pick those devices in your system sound settings or per-app (e.g. `pavucontrol`).

Unplugging the cable drops the connection; the virtual devices are removed and the UI returns to *Connect*.

## Protocol
Both directions use the same frame: `1 byte type | 4 byte big-endian length | payload`.

| type | dir | payload |
|------|-----|---------|
| `0x01 HELLO` | both | JSON `{"version":1}` |
| `0x10 MIC_PCM` | tablet→laptop | s16le 48 kHz mono |
| `0x20 SPK_PCM` | laptop→tablet | s16le 48 kHz stereo |
| `0x30 FRAME_JPEG` | laptop→tablet | JPEG image |
| `0x40 CONTROL` | laptop→tablet | JSON `{"mic": true/false}` |

## Notes / limitations
* Screen mirroring is a *mirror* of an existing monitor, not an extra virtual monitor. To use the tablet as an
  extended display, create a virtual output first (e.g. `xrandr` with a dummy mode, or a virtual monitor in your
  compositor) and choose it in the *Monitor to mirror* dropdown.
* Screen capture uses `mss`, which works on X11 and on XWayland-backed sessions; pure Wayland compositors may
  return a black screen.
* Audio is uncompressed PCM (~0.8 Mbit/s mic, ~1.5 Mbit/s speaker) — trivial for USB, and no codec means no
  extra latency or dependencies.
