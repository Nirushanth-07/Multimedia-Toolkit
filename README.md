# Multimedia Toolkit

A collection of lightweight, open-source Python utilities for capturing audio, video, and screen content, and for converting between media formats.

![Python](https://img.shields.io/badge/python-3.9%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Table of Contents
- [Features](#features)
- [Installation](#installation)
- [Terminal UI](#terminal-ui)
- [Quick Start (Interactive Menu)](#quick-start-interactive-menu)
- [Voice Recorder](#voice-recorder)
- [Screen Recorder](#screen-recorder)
- [Screenshot Utility](#screenshot-utility)
- [Audio/Video Conversion](#audiovideo-conversion)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Features

| Tool | Highlights |
|------|------------|
| 🎙 **Voice Recorder** | Record until you stop or for a set duration, pause/resume, live level meter, pick your microphone, save as WAV/MP3/FLAC/OGG/M4A/Opus |
| 🖥 **Screen Recorder** | Full screen, a monitor or a mouse-selected region, optional microphone audio, pause/resume, cursor capture, compact H.264 MP4 output |
| 📸 **Screenshot Utility** | Full screen, a single monitor, each monitor, a fixed region or a mouse selection, with a delay timer, PNG/JPG/WEBP/BMP/TIFF output and copy to clipboard |
| 🔄 **Audio/Video Conversion** | Convert between 16 formats, extract audio, trim, resize, change FPS, remove audio, make GIFs, batch-convert folders, show file info |

All output goes to the `output/` folder with timestamped file names unless you choose a path yourself.

---

## Installation

**Requirements:** Python 3.9 or newer.

```bash
git clone https://github.com/Nirushanth-07/Multimedia-Toolkit.git
cd Multimedia-Toolkit
pip install -r requirements.txt
```

You don't need to install ffmpeg separately, because the `imageio-ffmpeg` package bundles it. If that package is missing, the toolkit falls back to any `ffmpeg` on your `PATH`.

> **Linux:** `sounddevice` needs PortAudio (`sudo apt install libportaudio2`), and the mouse-selection overlay needs Tkinter (`sudo apt install python3-tk`).

---

## Terminal UI

The easiest way to use the toolkit is the full-screen grey terminal interface:

```bash
python ui.py
```

```
▌ MULTIMEDIA TOOLKIT                          ● busy: voice recorder · ffmpeg ✓ · mic ✓ · 21:54:50
┌ TOOLS ──────────────┐  VOICE RECORDER
│ 1  Voice Recorder   │  ┌ SETTINGS ─────────────────────┐ ┌ MONITOR ─────────────────────────┐
│ 2  Screen Recorder  │  │ Microphone  System default  ▼ │ │  00:00:12                        │
│ 3  Screenshot       │  │ Channels    Mono            ▼ │ │  ● RECORDING                     │
│ 4  Converter        │  │ Format      MP3             ▼ │ │  ▮▮▮▮▮▮▮▮▮▮▮▯▯▯▯▯▯▯▯▯▯  -18.2 dB  │
│ 5  Files            │  │ Time limit  none              │ │  ▁▂▃▅▇▆▄▃▂▅▇█▆▃                  │
│                     │  └───────────────────────────────┘ └──────────────────────────────────┘
│                     │   ● Record [R]   ❚❚ Pause [P]   ■ Stop [S]
│                     │  ┌ LOG ─────────────────────────────────────────────────────────────┐
│                     │  │ 21:54:52 ✓ Saved output/voice_20260916_215449.mp3 (00:00:02)    │
└─────────────────────┘  └──────────────────────────────────────────────────────────────────┘
```

| Tool | What you get |
|------|--------------|
| **Voice Recorder** | Microphone picker, big timer, live decibel meter and level history |
| **Screen Recorder** | Monitor / mouse-selected area, FPS, scale, countdown, microphone and cursor options, live frame counter |
| **Screenshot** | Capture modes, delay, format, clipboard copy and a greyscale preview of the last capture |
| **Converter** | File browser, media details, format and quality, trimming, resizing, frame rate, mute, progress bar |
| **Files** | Everything in `output/`: open it, send it to the converter, delete it, or open the folder |

| Key | Action |
|-----|--------|
| `1`–`5` | Switch tool |
| `R` | Record, capture or convert (depending on the tool) |
| `P` | Pause or resume a recording |
| `S` | Stop a recording |
| `O` | Open the `output/` folder |
| `C` | Clear the log |
| `Q` | Quit (asks again if something is still running) |

Everything also works with the mouse. Use Windows Terminal or any modern terminal at least 110×36 characters for the best layout.

---

## Quick Start (Interactive Menu)

For a simple text menu, or in terminals that can't run the full UI, run:

```bash
python toolkit.py
```

```
========================================
          Multimedia Toolkit
========================================
  1. Voice Recorder
  2. Screen Recorder
  3. Screenshot
  4. Audio/Video Converter
  5. Media File Info
  0. Exit
```

---

## Voice Recorder
Records your microphone and streams the audio straight to disk, so even long recordings use very little memory.

### How to Use
```bash
python voice_recorder.py                       # record until you press Q
python voice_recorder.py -d 30                 # record for 30 seconds
python voice_recorder.py -m 5 -o lecture.mp3   # record 5 minutes to MP3
python voice_recorder.py -c 2 -r 48000         # stereo at 48 kHz
python voice_recorder.py --list-devices        # show available microphones
python voice_recorder.py --device 1            # record from a specific microphone
```

**While recording:** press `P` to pause or resume and `Q` to stop (`Ctrl+C` also stops and still saves the file).

```
🎙  Device: Microphone Array  |  44100 Hz  |  1 ch
Recording until you stop it.  Controls: [P] pause/resume   [Q] stop   (Ctrl+C also stops)

● REC      00:00:12  ██████████░░░░░░░░░░░░░░░░░░░░
```

| Option | Description |
|--------|-------------|
| `-d`, `--duration` | Length in seconds |
| `-m`, `--minutes` | Length in minutes |
| `-o`, `--output` | Output file: `.wav`, `.mp3`, `.flac`, `.ogg`, `.m4a`, `.opus` |
| `-r`, `--samplerate` | Sample rate in Hz (default: the device's own rate) |
| `-c`, `--channels` | `1` = mono (default), `2` = stereo |
| `--device` | Input device index or name |
| `--list-devices` | List input devices |

---

## Screen Recorder
Captures your desktop to video. Capture and encoding run on separate threads, and frames are timed against the clock, so the video plays back at real speed even when the computer is busy. When recording stops, the video is encoded to H.264 so it's small and plays in any player or browser.

### How to Use
```bash
python screen_recorder.py                        # record everything until you press Q
python screen_recorder.py --audio                # include microphone audio
python screen_recorder.py --select               # drag a rectangle to choose the area
python screen_recorder.py --monitor 1 -d 60      # primary monitor for 60 seconds
python screen_recorder.py --region 0,0,1280,720 --fps 30
python screen_recorder.py --scale 0.5 -o demo.mp4
```

**While recording:** press `P` to pause or resume and `Q` to stop. A 3-second countdown runs before recording starts.

| Option | Description |
|--------|-------------|
| `--monitor N` | `0` = all monitors (default), `1` = primary, `2` = second, … |
| `--region x,y,w,h` | Record a fixed rectangle |
| `-s`, `--select` | Select the area with the mouse |
| `-d`, `--duration` / `-m`, `--minutes` | Stop automatically |
| `-o`, `--output` | Output file: `.mp4` (default), `.mkv`, `.mov`, `.avi` |
| `--fps` | Frames per second, 1–60 (default: 24) |
| `--scale` | Downscale factor 0.1–1.0 (for example, `0.5` for half size) |
| `-a`, `--audio` | Record the microphone as well |
| `--audio-device` | Microphone index or name |
| `--no-cursor` | Don't draw the mouse pointer |
| `--delay` | Countdown in seconds (default: 3, `0` to disable) |
| `--raw` | Skip the final H.264 encode (faster to finish, larger file) |

> **Tip:** If the tool warns that it captured fewer frames per second than the target, lower `--fps`, record a smaller `--region`, or use `--scale 0.5`.
> To make a GIF or WebM, record to MP4 first and then use the [converter](#audiovideo-conversion).

---

## Screenshot Utility
Captures high-resolution screenshots. On high-DPI (scaled) Windows displays, captures use the real pixel resolution.

### How to Use
```bash
python screenshot.py                              # all monitors in one image
python screenshot.py --select                     # drag a rectangle with the mouse
python screenshot.py --monitor 1 --delay 5        # primary monitor after 5 seconds
python screenshot.py --each                       # one file per monitor
python screenshot.py --region 100,100,800,600 -o crop.jpg --quality 90
python screenshot.py -s --clipboard               # select and copy to clipboard
python screenshot.py --list-monitors
```

| Option | Description |
|--------|-------------|
| `--monitor N` | `0` = all monitors (default), `1` = primary, … |
| `--region x,y,w,h` | Capture a fixed rectangle |
| `-s`, `--select` | Select the area with the mouse (`Esc` or right-click cancels) |
| `--each` | Save each monitor to a separate file |
| `-o`, `--output` | Output file: `.png`, `.jpg`, `.webp`, `.bmp`, `.tiff` |
| `-f`, `--format` | Image format when no `--output` is given (default: `png`) |
| `--quality` | JPEG/WEBP quality 1–100 (default: 95) |
| `--delay` | Seconds to wait before capturing |
| `-c`, `--clipboard` | Also copy the image to the clipboard (Windows) |
| `--list-monitors` | List monitors and their positions |

---

## Audio/Video Conversion
Converts, compresses, trims, and extracts media with ffmpeg. The output format comes from the output file's extension, and a progress bar shows how far the conversion has got.

**Supported formats:** Audio: `mp3`, `wav`, `flac`, `ogg`, `m4a`, `aac`, `opus`, `wma`. Video: `mp4`, `mkv`, `mov`, `avi`, `webm`, `gif`, `flv`, `wmv`.

### How to Use
```bash
python converter.py recording.wav recording.mp3                 # WAV → MP3
python converter.py video.mp4 audio.mp3 --bitrate 320k          # extract audio
python converter.py video.mp4 video.webm --quality high         # MP4 → WebM
python converter.py video.mp4 clip.mp4 --start 1:10 --end 1:40  # trim
python converter.py video.mp4 small.mp4 --width 1280 --quality low
python converter.py video.mp4 silent.mp4 --mute                 # remove audio
python converter.py video.mp4 demo.gif --width 640 --fps 12     # high-quality GIF
python converter.py --batch output/ --to mp3                    # convert a whole folder
python converter.py --info video.mp4                            # show duration, codecs, resolution
```

```
  ███████████████████░░░░░░░░░░░  63.2%  00:01:03 / 00:01:40
✅ small.mp4  (48.2 MB → 9.7 MB, -80%)
```

| Option | Description |
|--------|-------------|
| `--start`, `--end` | Trim the file. Times can be seconds (`90`) or `HH:MM:SS` (`1:30`) |
| `--width`, `--height` | Resize while keeping the aspect ratio |
| `--fps` | Change the frame rate |
| `--mute` | Remove the audio track |
| `-q`, `--quality` | `low`, `medium` (default) or `high` |
| `-b`, `--bitrate` | Audio bitrate for audio output (such as `192k`), or maximum video bitrate for video output (such as `2M`) |
| `--samplerate`, `--channels` | Audio sample rate and channel count |
| `--batch FOLDER --to EXT` | Convert every media file in a folder (add `--recursive` to include subfolders) |
| `--info` | Print information about a media file |
| `-y`, `--overwrite` | Replace existing output files |

---

## Project Structure

```
Multimedia-Toolkit/
├── ui.py               # full-screen grey terminal UI (Textual)
├── toolkit.py          # simple text menu for all tools
├── voice_recorder.py   # microphone recording
├── screen_recorder.py  # screen recording (+ optional audio)
├── screenshot.py       # screenshots
├── converter.py        # audio/video conversion (ffmpeg)
├── common.py           # shared helpers: key controls, region selector, file naming
├── requirements.txt
├── LICENSE
└── README.md
```

Each tool can also be imported and used from your own Python code:

```python
from voice_recorder import record
from screenshot import take_screenshot
from screen_recorder import record_screen
from converter import convert

audio = record(duration=10, quiet=True)              # returns the saved file path
convert(audio, "output/voice.mp3", quality="high")
take_screenshot(monitor=1, image_format="jpg")
record_screen(duration=5, audio=True, delay=0)
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `PortAudio library not found` | Linux: `sudo apt install libportaudio2` |
| Recording is silent or uses the wrong microphone | Run `python voice_recorder.py --list-devices` and pass `--device N` |
| Screen recording is choppy | Lower `--fps`, use `--scale 0.5`, or record a smaller region |
| Black screen recording on macOS | Allow Terminal or your IDE under **System Settings → Privacy & Security → Screen Recording** |
| `ffmpeg not found` | `pip install imageio-ffmpeg` |
| P/Q keys don't respond | The terminal must be interactive. Use `Ctrl+C` to stop instead |
| The UI looks broken or cramped | Use Windows Terminal (not the old console) and enlarge the window |

---

## License

This project is licensed under the [MIT License](LICENSE).
