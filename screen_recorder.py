"""Screen Recorder - record your desktop (optionally with microphone audio) to MP4.

Examples:
    python screen_recorder.py                        # record all monitors until you press Q
    python screen_recorder.py --audio                # include microphone audio
    python screen_recorder.py --select --fps 30      # record a region chosen with the mouse
    python screen_recorder.py -d 60 --monitor 1      # record the primary monitor for 60 seconds
    python screen_recorder.py --scale 0.5 -o demo.mp4
"""

import argparse
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

import cv2
import numpy as np

from common import (KeyListener, countdown, enable_dpi_awareness, find_ffmpeg, format_duration,
                    format_size, parse_region, screen_capture, select_region, timestamped_path)

VIDEO_FORMATS = {".mp4", ".mkv", ".mov", ".avi"}

# Arrow-shaped cursor polygon (points relative to the hotspot)
CURSOR_SHAPE = np.array([[0, 0], [0, 17], [4, 13], [7, 20], [10, 19], [7, 12], [12, 12]], np.int32)


def cursor_position():
    """Return the mouse position in screen pixels (Windows only), or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    point = wintypes.POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return point.x, point.y
    return None


def draw_cursor(frame, area, scale):
    position = cursor_position()
    if not position:
        return
    x = int((position[0] - area["left"]) * scale)
    y = int((position[1] - area["top"]) * scale)
    if not (0 <= x < frame.shape[1] and 0 <= y < frame.shape[0]):
        return
    shape = (CURSOR_SHAPE * max(scale, 0.6)).astype(np.int32) + (x, y)
    cv2.fillPoly(frame, [shape], (255, 255, 255), lineType=cv2.LINE_AA)
    cv2.polylines(frame, [shape], True, (0, 0, 0), 1, lineType=cv2.LINE_AA)


class ScreenRecorder:
    def __init__(self, filename, area, fps=24, scale=1.0, show_cursor=True):
        self.filename = filename
        self.area = area
        self.fps = fps
        self.scale = scale
        self.show_cursor = show_cursor
        self.audio = None            # optional AudioRecorder recording alongside
        # most codecs need even dimensions
        self.width = int(area["width"] * scale) // 2 * 2
        self.height = int(area["height"] * scale) // 2 * 2
        self.paused = False
        self.stop_requested = False  # set from another thread (e.g. the TUI) to finish recording
        self.active_time = 0.0       # recorded seconds, excluding pauses
        self.phase = "idle"          # idle -> recording -> encoding -> done
        self.frames_written = 0
        self.frames_captured = 0
        self._queue = queue.Queue(maxsize=fps * 2)
        self._writer = None
        self._writer_error = None

    @property
    def elapsed(self):
        return self.frames_written / self.fps

    def _write_loop(self):
        writer = cv2.VideoWriter(self.filename, cv2.VideoWriter_fourcc(*"mp4v"), self.fps,
                                 (self.width, self.height))
        if not writer.isOpened():
            self._writer_error = "Could not open the video writer."
            while self._queue.get() is not None:  # keep draining so the capture loop never blocks
                pass
            return
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                frame, repeat = item
                for _ in range(repeat):
                    writer.write(frame)
                    self.frames_written += 1
        finally:
            writer.release()

    def run(self, duration=None, audio=None, quiet=False, keyboard=True):
        """Capture until Q / Ctrl+C / stop_requested / duration.

        `audio` is an optional AudioRecorder kept in sync with pauses.
        """
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()
        frame_interval = 1.0 / self.fps
        active_time = 0.0       # recording time excluding pauses
        frames_due_total = 0
        last_tick = None

        try:
            with screen_capture() as sct, KeyListener(keyboard) as keys:
                if audio:
                    audio.start()
                self.phase = "recording"
                last_tick = time.perf_counter()
                next_status = 0.0
                while not self._writer_error:
                    key = keys.get()
                    if key == "q" or self.stop_requested:
                        break
                    if key == "p":
                        self.paused = not self.paused
                    if audio:
                        audio.paused = self.paused

                    now = time.perf_counter()
                    if not self.paused:
                        active_time += now - last_tick
                        self.active_time = active_time
                    last_tick = now
                    if duration and active_time >= duration:
                        break

                    if not self.paused:
                        shot = sct.grab(self.area)
                        frame = np.asarray(shot)[:, :, :3]
                        if self.scale != 1.0 or frame.shape[1] != self.width or frame.shape[0] != self.height:
                            frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
                        else:
                            frame = np.ascontiguousarray(frame)
                        if self.show_cursor:
                            draw_cursor(frame, self.area, self.width / self.area["width"])
                        self.frames_captured += 1

                        # write as many copies as needed so video time matches real time
                        frames_due = int(active_time * self.fps) + 1
                        repeat = frames_due - frames_due_total
                        if repeat > 0:
                            self._queue.put((frame, repeat))
                            frames_due_total = frames_due

                    if not quiet and now >= next_status:
                        state = "⏸ PAUSED" if self.paused else "● REC   "
                        level = ""
                        if audio:
                            filled = int(min(audio.level, 1.0) * 10)
                            level = "  🎙 " + "█" * filled + "░" * (10 - filled)
                        print(f"\r{state}  {format_duration(active_time)}  "
                              f"{self.width}x{self.height} @ {self.fps} fps{level}   ", end="", flush=True)
                        next_status = now + 0.25

                    sleep_for = frame_interval - (time.perf_counter() - now)
                    if sleep_for > 0:
                        time.sleep(sleep_for)
        except KeyboardInterrupt:
            pass
        finally:
            if audio:
                audio.stop()
            self._queue.put(None)
            self._writer.join()
            if not quiet:
                print()

        if self._writer_error:
            raise RuntimeError(self._writer_error)
        return active_time


def finalize(raw_video, output, audio_file=None, quiet=False):
    """Re-encode to H.264 (much smaller, plays everywhere) and mux audio. Returns True on success."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    extension = os.path.splitext(output)[1].lower()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", raw_video]
    if audio_file:
        cmd += ["-i", audio_file]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    if audio_file:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-shortest"]
    if extension in (".mp4", ".mov"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(output)
    if not quiet:
        print("⚙  Encoding final video...")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 and not quiet:
        print(f"⚠  ffmpeg failed: {result.stderr.strip()}")
    return result.returncode == 0


def record_screen(output=None, duration=None, fps=24, monitor=0, region=None, select=False,
                  scale=1.0, audio=False, audio_device=None, show_cursor=True, delay=3,
                  raw=False, quiet=False, keyboard=True, on_start=None):
    """Record the screen and return the saved file path.

    on_start: optional callback receiving the ScreenRecorder before capture begins, so callers
              can pause it, stop it (`stop_requested = True`) and read `active_time` / `phase`.
    """
    enable_dpi_awareness()
    path = timestamped_path("screen", "mp4", output)
    extension = os.path.splitext(path)[1].lower()
    if extension not in VIDEO_FORMATS:
        raise ValueError(f"Unsupported video format '{extension}'. Use {', '.join(sorted(VIDEO_FORMATS))} "
                         "(convert to GIF/WebM afterwards with converter.py).")
    if not 1 <= fps <= 60:
        raise ValueError("FPS must be between 1 and 60.")
    if not 0.1 <= scale <= 1.0:
        raise ValueError("Scale must be between 0.1 and 1.0.")

    if select:
        region = select_region()
        if region is None:
            raise RuntimeError("Selection cancelled.")
    with screen_capture() as sct:
        if region:
            area = region
        elif 0 <= monitor < len(sct.monitors):
            area = sct.monitors[monitor]
        else:
            raise ValueError(f"Monitor {monitor} not found. Available: 0-{len(sct.monitors) - 1}")

    post_process = not raw and find_ffmpeg() is not None
    temp_files = []

    def temp(suffix):
        handle, name = tempfile.mkstemp(suffix=suffix)
        os.close(handle)
        temp_files.append(name)
        return name

    raw_video = temp(".mp4") if post_process else path
    recorder = ScreenRecorder(raw_video, area, fps, scale, show_cursor)
    audio_recorder = None
    if audio:
        from voice_recorder import AudioRecorder
        audio_recorder = AudioRecorder(temp(".wav"), channels=2, device=audio_device)

    if not quiet:
        print(f"🖥  Area: {area['width']}x{area['height']} at ({area['left']}, {area['top']})  →  "
              f"{recorder.width}x{recorder.height} @ {fps} fps"
              + ("  +  🎙 microphone" if audio else ""))
        print("Controls: [P] pause/resume   [Q] stop   (Ctrl+C also stops)")
    recorder.audio = audio_recorder
    if on_start:
        on_start(recorder)
    if delay:
        countdown(delay, "Recording starts in")

    try:
        seconds = recorder.run(duration, audio_recorder, quiet, keyboard)
        recorder.phase = "encoding"
        if recorder.frames_written == 0:
            raise RuntimeError("Nothing was recorded.")

        if post_process:
            audio_file = audio_recorder.filename if audio_recorder and audio_recorder.frames_written else None
            if not finalize(raw_video, path, audio_file, quiet):
                os.replace(raw_video, path)  # fall back to the unprocessed recording
                temp_files.remove(raw_video)
        elif audio_recorder and not quiet:
            audio_path = os.path.splitext(path)[0] + ".wav"
            os.replace(audio_recorder.filename, audio_path)
            temp_files.remove(audio_recorder.filename)
            print(f"ℹ  ffmpeg not available - audio saved separately as {audio_path}")
    finally:
        for name in temp_files:
            if os.path.exists(name):
                os.remove(name)

    recorder.phase = "done"
    if not quiet:
        actual_fps = recorder.frames_captured / seconds if seconds else 0
        print(f"✅ Saved {path}  ({format_duration(seconds)}, {format_size(os.path.getsize(path))})")
        if actual_fps and actual_fps < fps * 0.8:
            print(f"ℹ  Captured {actual_fps:.1f} unique fps (target {fps}). "
                  "Try a lower --fps, a smaller --region or --scale 0.5 for smoother video.")
    return path


def build_parser():
    parser = argparse.ArgumentParser(description="Record your screen to a video file.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--monitor", type=int, default=0, help="0 = all monitors (default), 1 = primary, ...")
    target.add_argument("--region", help="record x,y,width,height")
    target.add_argument("-s", "--select", action="store_true", help="select the area with the mouse")
    length = parser.add_mutually_exclusive_group()
    length.add_argument("-d", "--duration", type=float, help="recording length in seconds")
    length.add_argument("-m", "--minutes", type=float, help="recording length in minutes")
    parser.add_argument("-o", "--output", help="output file (.mp4, .mkv, .mov, .avi). "
                                               "Default: output/screen_<timestamp>.mp4")
    parser.add_argument("--fps", type=int, default=24, help="frames per second, 1-60 (default: 24)")
    parser.add_argument("--scale", type=float, default=1.0, help="resize factor 0.1-1.0 (default: 1.0)")
    parser.add_argument("-a", "--audio", action="store_true", help="also record the microphone")
    parser.add_argument("--audio-device", help="microphone index or name (see voice_recorder.py --list-devices)")
    parser.add_argument("--no-cursor", action="store_true", help="don't draw the mouse cursor")
    parser.add_argument("--delay", type=int, default=3, help="countdown before recording (default: 3)")
    parser.add_argument("--raw", action="store_true", help="skip the final H.264 encode (faster, larger file)")
    return parser


def main():
    args = build_parser().parse_args()
    duration = args.duration if args.duration is not None else (
        args.minutes * 60 if args.minutes is not None else None)
    if duration is not None and duration <= 0:
        sys.exit("Error: duration must be greater than zero.")
    audio_device = args.audio_device
    if audio_device and audio_device.isdigit():
        audio_device = int(audio_device)
    try:
        region = parse_region(args.region) if args.region else None
        record_screen(args.output, duration, args.fps, args.monitor, region, args.select, args.scale,
                      args.audio, audio_device, not args.no_cursor, max(0, args.delay), args.raw)
    except (ValueError, RuntimeError) as error:
        sys.exit(f"Error: {error}")
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")
    except Exception as error:  # e.g. microphone errors from sounddevice
        sys.exit(f"Error: {error}")


if __name__ == "__main__":
    main()
