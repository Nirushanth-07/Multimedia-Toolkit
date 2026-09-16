"""Voice Recorder - record your microphone to WAV (or MP3/FLAC/OGG/M4A).

Examples:
    python voice_recorder.py                      # record until you press Q
    python voice_recorder.py -d 30                # record for 30 seconds
    python voice_recorder.py -m 2 -o memo.mp3     # record 2 minutes straight to MP3
    python voice_recorder.py --list-devices
"""

import argparse
import os
import queue
import sys
import tempfile
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from common import KeyListener, format_duration, format_size, timestamped_path

CONVERTIBLE_FORMATS = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}


class AudioRecorder:
    """Streams microphone input to a WAV file on disk, with pause/resume and level metering."""

    def __init__(self, filename, samplerate=None, channels=1, device=None):
        self.filename = filename
        self.device = device
        info = sd.query_devices(device, "input")
        self.samplerate = int(samplerate or info["default_samplerate"])
        self.channels = min(channels, info["max_input_channels"]) or 1
        self.level = 0.0          # current peak level 0.0 - 1.0
        self.paused = False
        self.frames_written = 0
        self.overflows = 0
        self._queue = queue.Queue()
        self._stream = None
        self._writer = None

    @property
    def elapsed(self):
        return self.frames_written / self.samplerate

    def _callback(self, indata, frames, time_info, status):
        if status.input_overflow:
            self.overflows += 1
        self.level = float(np.abs(indata).max()) if len(indata) else 0.0
        if not self.paused:
            self._queue.put(indata.copy())

    def _write_loop(self):
        with wave.open(self.filename, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)  # 16-bit PCM
            wav.setframerate(self.samplerate)
            while True:
                block = self._queue.get()
                if block is None:
                    break
                pcm = (np.clip(block, -1.0, 1.0) * 32767).astype("<i2")
                wav.writeframes(pcm.tobytes())
                self.frames_written += len(block)

    def start(self):
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()
        self._stream = sd.InputStream(samplerate=self.samplerate, channels=self.channels,
                                      device=self.device, dtype="float32", callback=self._callback)
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._writer:
            self._queue.put(None)
            self._writer.join()
            self._writer = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def level_bar(level, width=30):
    filled = int(min(level, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def record(output=None, duration=None, samplerate=None, channels=1, device=None, quiet=False):
    """Record audio and return the path of the saved file.

    duration: seconds to record, or None to record until the user presses Q / Ctrl+C.
    """
    path = timestamped_path("voice", "wav", output)
    extension = os.path.splitext(path)[1].lower()
    needs_conversion = extension in CONVERTIBLE_FORMATS
    wav_path = path
    if needs_conversion:
        handle, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
    elif extension != ".wav":
        raise ValueError(f"Unsupported audio format '{extension}'. Use .wav, "
                         + ", ".join(sorted(CONVERTIBLE_FORMATS)))

    recorder = AudioRecorder(wav_path, samplerate, channels, device)
    device_name = sd.query_devices(device, "input")["name"]
    if not quiet:
        print(f"🎙  Device: {device_name}  |  {recorder.samplerate} Hz  |  "
              f"{'stereo' if recorder.channels == 2 else f'{recorder.channels} ch'}")
        limit = f"for {format_duration(duration)}" if duration else "until you stop it"
        print(f"Recording {limit}.  Controls: [P] pause/resume   [Q] stop   (Ctrl+C also stops)\n")

    try:
        with KeyListener() as keys, recorder:
            while True:
                key = keys.get()
                if key == "q":
                    break
                if key == "p":
                    recorder.paused = not recorder.paused
                if duration and recorder.elapsed >= duration:
                    break
                if not quiet:
                    state = "⏸ PAUSED   " if recorder.paused else "● REC      "
                    print(f"\r{state}{format_duration(recorder.elapsed)}  {level_bar(recorder.level)}",
                          end="", flush=True)
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    if not quiet:
        print()
        if recorder.overflows:
            print(f"⚠  {recorder.overflows} input overflow(s) - some audio may have been dropped.")

    if recorder.frames_written == 0:
        os.remove(wav_path)
        raise RuntimeError("Nothing was recorded.")

    if needs_conversion:
        from converter import convert
        try:
            convert(wav_path, path, overwrite=True, quiet=True)
        finally:
            os.remove(wav_path)

    if not quiet:
        print(f"✅ Saved {path}  ({format_duration(recorder.elapsed)}, {format_size(os.path.getsize(path))})")
    return path


def build_parser():
    parser = argparse.ArgumentParser(description="Record audio from your microphone.")
    length = parser.add_mutually_exclusive_group()
    length.add_argument("-d", "--duration", type=float, help="recording length in seconds")
    length.add_argument("-m", "--minutes", type=float, help="recording length in minutes")
    parser.add_argument("-o", "--output", help="output file (.wav, .mp3, .flac, .ogg, .m4a, .opus). "
                                               "Default: output/voice_<timestamp>.wav")
    parser.add_argument("-r", "--samplerate", type=int, help="sample rate in Hz (default: device default)")
    parser.add_argument("-c", "--channels", type=int, default=1, choices=(1, 2), help="1 = mono, 2 = stereo")
    parser.add_argument("--device", help="input device index or name (see --list-devices)")
    parser.add_argument("--list-devices", action="store_true", help="list audio input devices and exit")
    return parser


def list_input_devices():
    default_input = sd.default.device[0]
    print("Input devices:")
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            marker = "*" if index == default_input else " "
            api = sd.query_hostapis(dev["hostapi"])["name"]
            print(f" {marker} [{index:2d}] {dev['name']}  ({api}, {dev['max_input_channels']} ch)")
    print("\n * = default device")


def main():
    args = build_parser().parse_args()
    if args.list_devices:
        list_input_devices()
        return

    duration = args.duration if args.duration is not None else (
        args.minutes * 60 if args.minutes is not None else None)
    if duration is not None and duration <= 0:
        sys.exit("Error: duration must be greater than zero.")
    device = int(args.device) if args.device and args.device.isdigit() else args.device

    try:
        record(args.output, duration, args.samplerate, args.channels, device)
    except (ValueError, RuntimeError, sd.PortAudioError) as error:
        sys.exit(f"Error: {error}")


if __name__ == "__main__":
    main()
