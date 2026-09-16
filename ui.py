"""Multimedia Toolkit - grey terminal user interface.

Run:  python ui.py

Keys: 1-5 switch tool · R start/capture/convert · P pause · S stop · O open output folder · Q quit
"""

import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from functools import partial
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)  # keep output/ next to the toolkit no matter where it is launched from
sys.path.insert(0, HERE)

from PIL import Image
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (Button, Checkbox, ContentSwitcher, DataTable, Digits, DirectoryTree, Footer,
                             Input, Label, OptionList, ProgressBar, RichLog, Select, Sparkline, Static)
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from common import OUTPUT_DIR, enable_dpi_awareness, find_ffmpeg, format_duration, format_size, screen_capture, \
    timestamped_path

GREY_THEME = Theme(
    name="grey-terminal",
    primary="#9e9e9e",
    secondary="#7a7a7a",
    accent="#d6d6d6",
    foreground="#cfcfcf",
    background="#1a1a1a",
    surface="#222222",
    panel="#2d2d2d",
    boost="#333333",
    success="#a6b39c",
    warning="#c7b98c",
    error="#c27b7b",
    dark=True,
)

AUDIO_OUTPUTS = ["wav", "mp3", "flac", "ogg", "m4a", "opus"]
CONVERT_OUTPUTS = ["mp3", "wav", "flac", "ogg", "m4a", "opus", "mp4", "mkv", "mov", "webm", "gif", "avi"]
MEDIA_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma", ".mp4", ".mkv",
                    ".mov", ".avi", ".webm", ".gif", ".flv", ".wmv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}


# ─────────────────────────────────────────────────────────────── helpers

def parse_limit(text):
    """'' -> None, '90' -> 90.0, '1:30' -> 90.0"""
    text = text.strip()
    if not text:
        return None
    from converter import parse_time
    seconds = parse_time(text)
    if seconds <= 0:
        raise ValueError("Time limit must be greater than zero.")
    return seconds


def open_path(path):
    path = os.path.abspath(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def pick_region():
    """Run the mouse region selector in a separate process (Tk must own its own main thread)."""
    code = "import json, common; print(json.dumps(common.select_region()))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=HERE)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise RuntimeError(result.stderr.strip() or "Region selection failed.")


def unique_path(path):
    base, ext = os.path.splitext(path)
    counter = 1
    while os.path.exists(path):
        path = f"{base}_{counter}{ext}"
        counter += 1
    return path


def meter_value(peak, floor_db=-60.0):
    """Map a 0-1 peak amplitude to 0-1 on a decibel scale, so quiet speech is still visible."""
    if peak <= 0:
        return 0.0
    db = 20 * math.log10(min(peak, 1.0))
    return max(0.0, (db - floor_db) / -floor_db)


def level_text(peak, width=34):
    level = meter_value(peak)
    filled = round(level * width)
    text = Text()
    for i in range(width):
        if i < filled:
            shade = 0x70 + int(0x7f * i / width)
            text.append("▮", style=f"#{shade:02x}{shade:02x}{shade:02x}")
        else:
            text.append("▯", style="#3a3a3a")
    db = f"{20 * math.log10(min(peak, 1.0)):5.1f} dB" if peak > 0.001 else "  -∞ dB"
    text.append(f"  {db}", style="#8a8a8a")
    return text


def field(label, widget):
    return Horizontal(Label(label), widget, classes="field")


# ─────────────────────────────────────────────────────────────── widgets

class ImagePreview(Static):
    """Greyscale half-block rendering of an image."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.image = None
        self._cache = None

    def set_image(self, image):
        self.image = image.convert("L")
        self._cache = None
        self.refresh()

    def render(self):
        width, height = self.size.width, self.size.height
        if self.image is None or width < 4 or height < 2:
            return Text("\n  no capture yet", style="#5f5f5f")
        if self._cache and self._cache[0] == (width, height):
            return self._cache[1]
        img_w, img_h = self.image.size
        scale = min(width / img_w, (height * 2) / img_h)
        cols = max(1, int(img_w * scale))
        rows = max(2, int(img_h * scale) // 2 * 2)
        pixels = self.image.resize((cols, rows), Image.LANCZOS).load()
        pad = " " * ((width - cols) // 2)
        text = Text()
        for y in range(0, rows, 2):
            text.append(pad)
            for x in range(cols):
                top, bottom = pixels[x, y], pixels[x, y + 1]
                text.append("▀", style=f"#{top:02x}{top:02x}{top:02x} on #{bottom:02x}{bottom:02x}{bottom:02x}")
            if y + 2 < rows:
                text.append("\n")
        self._cache = ((width, height), text)
        return text


class MediaTree(DirectoryTree):
    ICON_NODE = "▸ "
    ICON_NODE_EXPANDED = "▾ "
    ICON_FILE = "· "

    def filter_paths(self, paths):
        return [p for p in paths if not p.name.startswith((".", "__"))
                and (p.is_dir() or p.suffix.lower() in MEDIA_EXTENSIONS)]


class FilePicker(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, start):
        super().__init__()
        self.start = start

    def compose(self) -> ComposeResult:
        with Vertical(id="picker", classes="box") as box:
            box.border_title = "SELECT MEDIA FILE"
            yield Input(str(self.start), id="picker-path", compact=True,
                        placeholder="folder path - press Enter to open")
            yield MediaTree(self.start, id="picker-tree")
            with Horizontal(classes="actions"):
                yield Button("↑ Parent", id="picker-up", compact=True)
                yield Button("Cancel", id="picker-cancel", compact=True)

    @on(Input.Submitted, "#picker-path")
    def go_to(self, event):
        path = Path(event.value).expanduser()
        if path.is_file():
            self.dismiss(str(path))
        elif path.is_dir():
            self.query_one(MediaTree).path = path
        else:
            self.notify("Folder not found", severity="error")

    @on(Button.Pressed, "#picker-up")
    def go_up(self):
        tree = self.query_one(MediaTree)
        parent = Path(tree.path).resolve().parent
        tree.path = parent
        self.query_one("#picker-path", Input).value = str(parent)

    @on(Button.Pressed, "#picker-cancel")
    def cancel(self):
        self.dismiss(None)

    @on(DirectoryTree.FileSelected)
    def chosen(self, event):
        self.dismiss(str(event.path))


class ConfirmDialog(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel"), Binding("y", "dismiss(True)", "Yes")]

    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm", classes="box") as box:
            box.border_title = "CONFIRM"
            yield Label(self.message)
            with Horizontal(classes="actions"):
                yield Button("Yes", id="yes", compact=True)
                yield Button("No", id="no", compact=True)

    @on(Button.Pressed)
    def answer(self, event):
        self.dismiss(event.button.id == "yes")


# ─────────────────────────────────────────────────────────────── tool panes

class Pane(VerticalScroll):
    """Base class: every tool pane supports primary / pause / stop actions."""

    heading = ""
    blurb = ""

    def compose_header(self):
        yield Label(self.heading, classes="pane-title")
        yield Label(self.blurb, classes="pane-sub")

    def primary(self):
        pass

    def pause(self):
        pass

    def stop(self):
        pass

    @property
    def busy(self):
        return False

    def note(self, message, level="info"):
        self.app.write_log(message, level)


class VoicePane(Pane):
    heading = "VOICE RECORDER"
    blurb = "Record your microphone to WAV, MP3, FLAC, OGG, M4A or Opus"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.recorder = None
        self.worker = None
        self.history = [0.0] * 60

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes="columns"):
            with Vertical(classes="box settings") as box:
                box.border_title = "SETTINGS"
                yield field("Microphone", Select(self.device_options(), value=-1, allow_blank=False,
                                                 compact=True, id="voice-device"))
                yield field("Channels", Select([("Mono", 1), ("Stereo", 2)], value=1, allow_blank=False,
                                               compact=True, id="voice-channels"))
                yield field("Format", Select([(f.upper(), f) for f in AUDIO_OUTPUTS], value="wav",
                                             allow_blank=False, compact=True, id="voice-format"))
                yield field("Time limit", Input(placeholder="none  (e.g. 90 or 1:30)", compact=True,
                                                id="voice-limit"))
            with Vertical(classes="box monitor") as box:
                box.border_title = "MONITOR"
                yield Digits("00:00:00", id="voice-time")
                yield Label("○ READY", id="voice-state", classes="state")
                yield Static(level_text(0), id="voice-level")
                yield Sparkline(self.history, id="voice-spark")
        with Horizontal(classes="actions"):
            yield Button("● Record  \\[R]", id="voice-record", compact=True)
            yield Button("❚❚ Pause  \\[P]", id="voice-pause", compact=True)
            yield Button("■ Stop  \\[S]", id="voice-stop", compact=True)

    def device_options(self):
        options = [("System default", -1)]
        try:
            import sounddevice as sd
            default_api = sd.query_devices(kind="input")["hostapi"]
            for index, dev in enumerate(sd.query_devices()):
                if dev["max_input_channels"] > 0 and dev["hostapi"] == default_api:
                    options.append((dev["name"][:40], index))
        except Exception:
            pass
        return options

    def on_mount(self):
        self.set_interval(0.1, self.refresh_monitor)
        self.sync_buttons()

    @property
    def busy(self):
        return self.worker is not None

    def sync_buttons(self):
        self.query_one("#voice-record").disabled = self.busy
        self.query_one("#voice-pause").disabled = self.recorder is None
        self.query_one("#voice-stop").disabled = not self.busy
        for widget in self.query(".settings Select, .settings Input"):
            widget.disabled = self.busy

    def refresh_monitor(self):
        rec = self.recorder
        if rec is None:
            return
        self.query_one("#voice-time", Digits).update(format_duration(rec.elapsed))
        state = self.query_one("#voice-state", Label)
        if rec.stop_requested:
            state.update("◌ SAVING…")
        elif rec.paused:
            state.update("❚❚ PAUSED")
            state.set_class(False, "-rec")
        else:
            state.update("● RECORDING")
            state.set_class(True, "-rec")
        self.query_one("#voice-level", Static).update(level_text(rec.level))
        self.history = self.history[1:] + [meter_value(rec.level)]
        self.query_one("#voice-spark", Sparkline).data = self.history

    def primary(self):
        if self.busy:
            return
        if self.app.recording_elsewhere(self):
            self.note("Another recording is running - stop it first.", "warn")
            return
        try:
            limit = parse_limit(self.query_one("#voice-limit", Input).value)
        except Exception as error:
            self.note(str(error), "error")
            return
        device = self.query_one("#voice-device", Select).value
        channels = self.query_one("#voice-channels", Select).value
        fmt = self.query_one("#voice-format", Select).value
        path = timestamped_path("voice", fmt)

        from voice_recorder import record
        job = partial(record, output=path, duration=limit, channels=channels,
                      device=None if device == -1 else device, quiet=True, keyboard=False,
                      on_start=self.attach)
        self.worker = self.run_worker(job, thread=True, name="voice")
        self.note(f"Recording microphone → {path}")
        self.sync_buttons()

    def attach(self, recorder):
        self.recorder = recorder
        self.app.call_from_thread(self.sync_buttons)

    def pause(self):
        if self.recorder and not self.recorder.stop_requested:
            self.recorder.paused = not self.recorder.paused
            self.note("Recording paused" if self.recorder.paused else "Recording resumed")

    def stop(self):
        if self.recorder:
            self.recorder.stop_requested = True

    @on(Button.Pressed, "#voice-record")
    def _record(self):
        self.primary()

    @on(Button.Pressed, "#voice-pause")
    def _pause(self):
        self.pause()

    @on(Button.Pressed, "#voice-stop")
    def _stop(self):
        self.stop()

    def on_worker_state_changed(self, event: Worker.StateChanged):
        if event.worker is not self.worker or event.state not in (WorkerState.SUCCESS, WorkerState.ERROR):
            return
        if event.state == WorkerState.SUCCESS:
            path = event.worker.result
            self.note(f"Saved {path}  ({format_duration(self.recorder.elapsed)}, "
                     f"{format_size(os.path.getsize(path))})", "ok")
            self.app.files_changed()
        else:
            self.note(f"Voice recorder: {event.worker.error}", "error")
        self.worker = None
        self.recorder = None
        state = self.query_one("#voice-state", Label)
        state.update("○ READY")
        state.set_class(False, "-rec")
        self.query_one("#voice-level", Static).update(level_text(0))
        self.sync_buttons()


class ScreenPane(Pane):
    heading = "SCREEN RECORDER"
    blurb = "Record the desktop, a monitor or a selected area to MP4 - with optional microphone audio"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.recorder = None
        self.worker = None
        self.countdown = 0
        self.status = ""
        self.cancelled = False
        self.history = [0.0] * 60

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes="columns"):
            with Vertical(classes="box settings") as box:
                box.border_title = "SETTINGS"
                yield field("Capture", Select(self.target_options(), value=0, allow_blank=False,
                                              compact=True, id="screen-target"))
                yield field("Frame rate", Select([(f"{n} fps", n) for n in (10, 15, 24, 30, 60)], value=24,
                                                 allow_blank=False, compact=True, id="screen-fps"))
                yield field("Scale", Select([("100%", 1.0), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25)],
                                            value=1.0, allow_blank=False, compact=True, id="screen-scale"))
                yield field("Countdown", Select([("None", 0), ("3 s", 3), ("5 s", 5), ("10 s", 10)], value=3,
                                                allow_blank=False, compact=True, id="screen-delay"))
                yield field("Time limit", Input(placeholder="none  (e.g. 90 or 1:30)", compact=True,
                                                id="screen-limit"))
                yield field("Options", Horizontal(Checkbox("Microphone", id="screen-audio"),
                                                  Checkbox("Cursor", True, id="screen-cursor"),
                                                  classes="checks"))
            with Vertical(classes="box monitor") as box:
                box.border_title = "MONITOR"
                yield Digits("00:00:00", id="screen-time")
                yield Label("○ READY", id="screen-state", classes="state")
                yield Label("", id="screen-info", classes="muted")
                yield Sparkline(self.history, id="screen-spark")
        with Horizontal(classes="actions"):
            yield Button("● Record  \\[R]", id="screen-record", compact=True)
            yield Button("❚❚ Pause  \\[P]", id="screen-pause", compact=True)
            yield Button("■ Stop  \\[S]", id="screen-stop", compact=True)

    def target_options(self):
        options = [("All monitors", 0)]
        try:
            with screen_capture() as sct:
                for index, mon in enumerate(sct.monitors[1:], 1):
                    options.append((f"Monitor {index}  ({mon['width']}x{mon['height']})", index))
        except Exception:
            pass
        options.append(("Select area with mouse…", -1))
        return options

    def on_mount(self):
        self.set_interval(0.1, self.refresh_monitor)
        self.sync_buttons()

    @property
    def busy(self):
        return self.worker is not None

    def sync_buttons(self):
        self.query_one("#screen-record").disabled = self.busy
        self.query_one("#screen-pause").disabled = self.recorder is None or self.recorder.phase != "recording"
        self.query_one("#screen-stop").disabled = not self.busy
        for widget in self.query(".settings Select, .settings Input, .settings Checkbox"):
            widget.disabled = self.busy

    def refresh_monitor(self):
        if not self.busy:
            return
        state = self.query_one("#screen-state", Label)
        rec = self.recorder
        rec_class = False
        if self.status:
            state.update(self.status)
        elif self.countdown:
            state.update(f"◷ STARTING IN {self.countdown}")
        elif rec is None:
            state.update("◌ PREPARING…")
        elif rec.phase == "encoding":
            state.update("◌ ENCODING VIDEO…")
        elif rec.stop_requested:
            state.update("◌ FINISHING…")
        elif rec.paused:
            state.update("❚❚ PAUSED")
        else:
            state.update("● RECORDING")
            rec_class = rec.phase == "recording"
        state.set_class(rec_class, "-rec")
        if rec:
            self.query_one("#screen-time", Digits).update(format_duration(rec.active_time))
            self.query_one("#screen-info", Label).update(
                f"{rec.width}x{rec.height} @ {rec.fps} fps   ·   {rec.frames_captured} frames captured")
            level = meter_value(rec.audio.level) if rec.audio else 0.0
            self.history = self.history[1:] + [level]
            self.query_one("#screen-spark", Sparkline).data = self.history
        pause = self.query_one("#screen-pause")
        pause.disabled = rec is None or rec.phase != "recording" or rec.stop_requested

    def primary(self):
        if self.busy:
            return
        if self.app.recording_elsewhere(self):
            self.note("Another recording is running - stop it first.", "warn")
            return
        try:
            limit = parse_limit(self.query_one("#screen-limit", Input).value)
        except Exception as error:
            self.note(str(error), "error")
            return
        options = dict(
            target=self.query_one("#screen-target", Select).value,
            fps=self.query_one("#screen-fps", Select).value,
            scale=self.query_one("#screen-scale", Select).value,
            delay=self.query_one("#screen-delay", Select).value,
            audio=self.query_one("#screen-audio", Checkbox).value,
            cursor=self.query_one("#screen-cursor", Checkbox).value,
            limit=limit,
        )
        self.cancelled = False
        self.worker = self.run_worker(partial(self.record_job, **options), thread=True, name="screen")
        self.sync_buttons()

    def record_job(self, target, fps, scale, delay, audio, cursor, limit):
        from screen_recorder import record_screen
        region = None
        if target == -1:
            self.status = "◎ DRAG TO SELECT AN AREA"
            region = pick_region()
            self.status = ""
            if region is None:
                raise RuntimeError("Selection cancelled.")
        for remaining in range(delay, 0, -1):
            if self.cancelled:
                raise RuntimeError("Recording cancelled.")
            self.countdown = remaining
            time.sleep(1)
        self.countdown = 0
        if self.cancelled:
            raise RuntimeError("Recording cancelled.")
        path = timestamped_path("screen", "mp4")
        self.app.call_from_thread(self.note, f"Recording screen → {path}")
        return record_screen(output=path, duration=limit, fps=fps, monitor=max(target, 0), region=region,
                             scale=scale, audio=audio, show_cursor=cursor, delay=0, quiet=True,
                             keyboard=False, on_start=self.attach)

    def attach(self, recorder):
        self.recorder = recorder
        self.app.call_from_thread(self.sync_buttons)

    def pause(self):
        rec = self.recorder
        if rec and rec.phase == "recording" and not rec.stop_requested:
            rec.paused = not rec.paused
            self.note("Screen recording paused" if rec.paused else "Screen recording resumed")

    def stop(self):
        self.cancelled = True
        if self.recorder:
            self.recorder.stop_requested = True

    @on(Button.Pressed, "#screen-record")
    def _record(self):
        self.primary()

    @on(Button.Pressed, "#screen-pause")
    def _pause(self):
        self.pause()

    @on(Button.Pressed, "#screen-stop")
    def _stop(self):
        self.stop()

    def on_worker_state_changed(self, event: Worker.StateChanged):
        if event.worker is not self.worker or event.state not in (WorkerState.SUCCESS, WorkerState.ERROR):
            return
        if event.state == WorkerState.SUCCESS:
            path = event.worker.result
            self.note(f"Saved {path}  ({format_duration(self.recorder.active_time)}, "
                     f"{format_size(os.path.getsize(path))})", "ok")
            self.app.files_changed()
        else:
            self.note(f"Screen recorder: {event.worker.error}", "error")
        self.worker = None
        self.recorder = None
        self.countdown = 0
        self.status = ""
        state = self.query_one("#screen-state", Label)
        state.update("○ READY")
        state.set_class(False, "-rec")
        self.sync_buttons()


class ScreenshotPane(Pane):
    heading = "SCREENSHOT"
    blurb = "Capture the full screen, a monitor, every monitor or an area selected with the mouse"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.worker = None
        self.countdown = 0

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes="columns"):
            with Vertical(classes="box settings") as box:
                box.border_title = "SETTINGS"
                yield field("Capture", Select(self.target_options(), value=0, allow_blank=False,
                                              compact=True, id="shot-target"))
                yield field("Delay", Select([("None", 0), ("3 s", 3), ("5 s", 5), ("10 s", 10)], value=0,
                                            allow_blank=False, compact=True, id="shot-delay"))
                yield field("Format", Select([("PNG", "png"), ("JPG", "jpg"), ("WEBP", "webp"), ("BMP", "bmp")],
                                             value="png", allow_blank=False, compact=True, id="shot-format"))
                yield field("Options", Checkbox("Copy to clipboard", sys.platform == "win32",
                                                id="shot-clipboard", disabled=sys.platform != "win32"))
                yield Label("", id="shot-state", classes="state")
                yield Label("", id="shot-info", classes="muted")
            with Vertical(classes="box monitor") as box:
                box.border_title = "PREVIEW"
                yield ImagePreview(id="shot-preview")
        with Horizontal(classes="actions"):
            yield Button("◉ Capture  \\[R]", id="shot-capture", compact=True)
            yield Button("↗ Open last", id="shot-open", compact=True, disabled=True)

    def target_options(self):
        options = [("All monitors", 0)]
        try:
            with screen_capture() as sct:
                for index, mon in enumerate(sct.monitors[1:], 1):
                    options.append((f"Monitor {index}  ({mon['width']}x{mon['height']})", index))
                if len(sct.monitors) > 2:
                    options.append(("Each monitor separately", -2))
        except Exception:
            pass
        options.append(("Select area with mouse…", -1))
        return options

    def on_mount(self):
        self.last_path = None
        self.set_interval(0.2, self.refresh_state)

    @property
    def busy(self):
        return self.worker is not None

    def refresh_state(self):
        if self.busy:
            text = f"◷ CAPTURING IN {self.countdown}" if self.countdown else "◌ CAPTURING…"
            self.query_one("#shot-state", Label).update(text)

    def primary(self):
        if self.busy:
            return
        options = dict(
            target=self.query_one("#shot-target", Select).value,
            delay=self.query_one("#shot-delay", Select).value,
            fmt=self.query_one("#shot-format", Select).value,
            clipboard=self.query_one("#shot-clipboard", Checkbox).value,
        )
        self.worker = self.run_worker(partial(self.capture_job, **options), thread=True, name="shot")
        self.query_one("#shot-capture").disabled = True

    def stop(self):
        pass

    def capture_job(self, target, delay, fmt, clipboard):
        from screenshot import take_screenshot
        region = None
        if target == -1:
            region = pick_region()
            if region is None:
                raise RuntimeError("Selection cancelled.")
        for remaining in range(delay, 0, -1):
            self.countdown = remaining
            time.sleep(1)
        self.countdown = 0
        paths = take_screenshot(monitor=max(target, 0), region=region, each=target == -2, image_format=fmt,
                                clipboard=clipboard, quiet=True)
        return paths, Image.open(paths[-1]).copy()

    @on(Button.Pressed, "#shot-capture")
    def _capture(self):
        self.primary()

    @on(Button.Pressed, "#shot-open")
    def _open(self):
        if self.last_path and os.path.exists(self.last_path):
            open_path(self.last_path)

    def on_worker_state_changed(self, event: Worker.StateChanged):
        if event.worker is not self.worker or event.state not in (WorkerState.SUCCESS, WorkerState.ERROR):
            return
        state = self.query_one("#shot-state", Label)
        if event.state == WorkerState.SUCCESS:
            paths, image = event.worker.result
            self.last_path = paths[-1]
            self.query_one("#shot-preview", ImagePreview).set_image(image)
            for path in paths:
                self.note(f"Saved {path}  ({format_size(os.path.getsize(path))})", "ok")
            state.update("✓ SAVED" + ("  ·  copied to clipboard"
                                      if self.query_one("#shot-clipboard", Checkbox).value and len(paths) == 1
                                      else ""))
            self.query_one("#shot-info", Label).update(
                f"{os.path.basename(self.last_path)}\n{image.width}x{image.height}  ·  "
                f"{format_size(os.path.getsize(self.last_path))}")
            self.query_one("#shot-open").disabled = False
            self.app.files_changed()
        else:
            state.update("✗ FAILED")
            self.note(f"Screenshot: {event.worker.error}", "error")
        self.worker = None
        self.countdown = 0
        self.query_one("#shot-capture").disabled = False


class ConvertPane(Pane):
    heading = "AUDIO / VIDEO CONVERTER"
    blurb = "Convert, compress, trim, resize, extract audio or make GIFs"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.worker = None
        self.probe_worker = None
        self.info = None
        self.progress = (0.0, 0.0, 0.0)
        self._probe_timer = None

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Vertical(classes="box") as box:
            box.border_title = "SOURCE"
            with Horizontal(classes="field"):
                yield Label("Input file")
                yield Input(placeholder="path to an audio or video file", compact=True, id="conv-input")
                yield Button("Browse…", id="conv-browse", compact=True)
            yield Label("no file selected", id="conv-info", classes="muted")
        with Horizontal(classes="columns"):
            with Vertical(classes="box settings") as box:
                box.border_title = "OUTPUT"
                yield field("Format", Select([(f.upper(), f) for f in CONVERT_OUTPUTS], value="mp3",
                                             allow_blank=False, compact=True, id="conv-format"))
                yield field("Quality", Select([("Low (small file)", "low"), ("Medium", "medium"),
                                               ("High", "high")], value="medium", allow_blank=False,
                                              compact=True, id="conv-quality"))
                yield field("Save as", Input(placeholder="automatic (next to the input)", compact=True,
                                             id="conv-output"))
            with Vertical(classes="box monitor") as box:
                box.border_title = "EDIT"
                yield field("Start", Input(placeholder="beginning  (e.g. 0:05)", compact=True, id="conv-start"))
                yield field("End", Input(placeholder="end of file  (e.g. 1:30)", compact=True, id="conv-end"))
                yield field("Width", Input(placeholder="original", type="integer", compact=True, id="conv-width"))
                yield field("Frame rate", Input(placeholder="original", type="number", compact=True, id="conv-fps"))
                yield field("Audio", Checkbox("Remove audio track", id="conv-mute"))
        with Vertical(classes="box") as box:
            box.border_title = "PROGRESS"
            yield ProgressBar(total=100, show_eta=False, id="conv-progress")
            yield Label("idle", id="conv-status", classes="muted")
        with Horizontal(classes="actions"):
            yield Button("⇄ Convert  \\[R]", id="conv-start-btn", compact=True)
            yield Button("↗ Open result", id="conv-open", compact=True, disabled=True)

    def on_mount(self):
        self.result_path = None
        self.set_interval(0.2, self.refresh_progress)

    @property
    def busy(self):
        return self.worker is not None

    def set_source(self, path):
        self.query_one("#conv-input", Input).value = path
        self.probe()

    @on(Input.Changed, "#conv-input")
    def input_changed(self):
        if self._probe_timer:
            self._probe_timer.stop()
        self._probe_timer = self.set_timer(0.6, self.probe)

    @on(Input.Submitted, "#conv-input")
    def input_submitted(self):
        self.probe()

    def probe(self):
        path = self.query_one("#conv-input", Input).value.strip().strip('"')
        info_label = self.query_one("#conv-info", Label)
        self.info = None
        if not path:
            info_label.update("no file selected")
            return
        if not os.path.isfile(path):
            info_label.update("✗ file not found")
            return
        info_label.update("reading file…")
        from converter import probe
        self.probe_worker = self.run_worker(partial(probe, path), thread=True, name="probe", exclusive=True,
                                            group="probe")

    def describe(self, info):
        parts = [format_size(info["size"])]
        if info["duration"] is not None:
            parts.append(format_duration(info["duration"]))
        if info["video"]:
            v = info["video"]
            parts.append(f"video {v['codec']} {v['width']}x{v['height']}" + (f" @ {v['fps']:g} fps" if v["fps"] else ""))
        if info["audio"]:
            a = info["audio"]
            parts.append(f"audio {a['codec']} {a['samplerate']} Hz {a['channels']}")
        return "  ·  ".join(parts)

    @on(Button.Pressed, "#conv-browse")
    def browse(self):
        current = self.query_one("#conv-input", Input).value.strip().strip('"')
        start = Path(current).parent if current and Path(current).parent.is_dir() else Path(OUTPUT_DIR)
        start.mkdir(exist_ok=True)

        def chosen(path):
            if path:
                self.set_source(path)
        self.app.push_screen(FilePicker(start.resolve()), chosen)

    def refresh_progress(self):
        if not self.busy:
            return
        fraction, done, total = self.progress
        self.query_one("#conv-progress", ProgressBar).update(progress=fraction * 100)
        if total:
            self.query_one("#conv-status", Label).update(
                f"converting…  {format_duration(done)} / {format_duration(total)}")

    def primary(self):
        if self.busy:
            return
        source = self.query_one("#conv-input", Input).value.strip().strip('"')
        if not os.path.isfile(source):
            self.note("Choose an input file first.", "warn")
            return
        fmt = self.query_one("#conv-format", Select).value
        target = self.query_one("#conv-output", Input).value.strip().strip('"')
        if target:
            if not os.path.splitext(target)[1]:
                target += f".{fmt}"
        else:
            target = unique_path(os.path.splitext(source)[0] + f".{fmt}")
        width = self.query_one("#conv-width", Input).value.strip()
        fps = self.query_one("#conv-fps", Input).value.strip()
        options = dict(
            start=self.query_one("#conv-start", Input).value.strip() or None,
            end=self.query_one("#conv-end", Input).value.strip() or None,
            quality=self.query_one("#conv-quality", Select).value,
            width=int(width) if width else None,
            fps=float(fps) if fps else None,
            mute=self.query_one("#conv-mute", Checkbox).value,
        )
        from converter import convert
        self.progress = (0.0, 0.0, 0.0)
        self.query_one("#conv-progress", ProgressBar).update(progress=0)
        self.query_one("#conv-status", Label).update("starting…")
        self.worker = self.run_worker(partial(convert, source, target, overwrite=True, quiet=True,
                                              progress=self.on_progress, **options),
                                      thread=True, name="convert")
        self.note(f"Converting {source} → {target}")
        self.query_one("#conv-start-btn").disabled = True

    def on_progress(self, fraction, done, total):
        self.progress = (fraction, done, total)

    @on(Button.Pressed, "#conv-start-btn")
    def _convert(self):
        self.primary()

    @on(Button.Pressed, "#conv-open")
    def _open(self):
        if self.result_path and os.path.exists(self.result_path):
            open_path(self.result_path)

    def on_worker_state_changed(self, event: Worker.StateChanged):
        if event.state not in (WorkerState.SUCCESS, WorkerState.ERROR):
            return
        if event.worker is self.probe_worker:
            label = self.query_one("#conv-info", Label)
            if event.state == WorkerState.SUCCESS:
                self.info = event.worker.result
                label.update(self.describe(self.info))
                if not self.info["video"] and self.query_one("#conv-format", Select).value in \
                        ("mp4", "mkv", "mov", "webm", "gif", "avi"):
                    self.query_one("#conv-format", Select).value = "mp3"
            else:
                label.update(f"✗ {event.worker.error}")
            return
        if event.worker is not self.worker:
            return
        status = self.query_one("#conv-status", Label)
        if event.state == WorkerState.SUCCESS:
            self.result_path = event.worker.result
            size = format_size(os.path.getsize(self.result_path))
            self.query_one("#conv-progress", ProgressBar).update(progress=100)
            status.update(f"✓ done  ·  {os.path.basename(self.result_path)}  ·  {size}")
            self.note(f"Converted → {self.result_path}  ({size})", "ok")
            self.query_one("#conv-open").disabled = False
            self.app.files_changed()
        else:
            status.update("✗ failed")
            self.note(f"Converter: {str(event.worker.error).strip()}", "error")
        self.worker = None
        self.query_one("#conv-start-btn").disabled = False


class FilesPane(Pane):
    heading = "FILES"
    blurb = f"Everything saved in {OUTPUT_DIR}/  ·  Enter opens a file"

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Vertical(classes="box files-box") as box:
            box.border_title = "OUTPUT"
            yield DataTable(cursor_type="row", zebra_stripes=True, id="files-table")
        with Horizontal(classes="actions"):
            yield Button("↻ Refresh", id="files-refresh", compact=True)
            yield Button("↗ Open", id="files-open", compact=True)
            yield Button("⇄ Convert…", id="files-convert", compact=True)
            yield Button("✗ Delete", id="files-delete", compact=True)
            yield Button("▣ Open folder  \\[O]", id="files-folder", compact=True)

    def on_mount(self):
        table = self.query_one(DataTable)
        table.add_column("Name", key="name")
        table.add_column("Type", key="type")
        table.add_column("Size", key="size")
        table.add_column("Modified", key="modified")
        self.reload()

    def primary(self):
        self.open_selected()

    def reload(self):
        table = self.query_one(DataTable)
        table.clear()
        if not os.path.isdir(OUTPUT_DIR):
            return
        entries = []
        for name in os.listdir(OUTPUT_DIR):
            path = os.path.join(OUTPUT_DIR, name)
            if os.path.isfile(path):
                entries.append((os.path.getmtime(path), name, path))
        for mtime, name, path in sorted(entries, reverse=True):
            ext = os.path.splitext(name)[1].lower()
            kind = "image" if ext in IMAGE_EXTENSIONS else (
                "video" if ext in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".gif"} else
                "audio" if ext in MEDIA_EXTENSIONS else "file")
            table.add_row(name, kind, format_size(os.path.getsize(path)),
                          datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"), key=path)

    def selected_path(self):
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def open_selected(self):
        path = self.selected_path()
        if path and os.path.exists(path):
            open_path(path)

    @on(DataTable.RowSelected)
    def _row_selected(self):
        self.open_selected()

    @on(Button.Pressed, "#files-refresh")
    def _refresh(self):
        self.reload()

    @on(Button.Pressed, "#files-open")
    def _open(self):
        self.open_selected()

    @on(Button.Pressed, "#files-folder")
    def _folder(self):
        self.app.action_open_output()

    @on(Button.Pressed, "#files-convert")
    def _convert(self):
        path = self.selected_path()
        if not path:
            return
        if os.path.splitext(path)[1].lower() not in MEDIA_EXTENSIONS:
            self.note("Only audio and video files can be converted.", "warn")
            return
        self.app.show_tool("convert")
        self.app.query_one(ConvertPane).set_source(path)

    @on(Button.Pressed, "#files-delete")
    def _delete(self):
        path = self.selected_path()
        if not path:
            return

        def confirmed(yes):
            if yes and os.path.exists(path):
                os.remove(path)
                self.note(f"Deleted {path}")
                self.reload()
        self.app.push_screen(ConfirmDialog(f"Delete {os.path.basename(path)}?"), confirmed)


# ─────────────────────────────────────────────────────────────── app

TOOLS = [
    ("voice", "1  Voice Recorder", VoicePane),
    ("screen", "2  Screen Recorder", ScreenPane),
    ("shot", "3  Screenshot", ScreenshotPane),
    ("convert", "4  Converter", ConvertPane),
    ("files", "5  Files", FilesPane),
]


class ToolkitApp(App):
    TITLE = "Multimedia Toolkit"
    CSS = """
    Screen { background: #1a1a1a; color: #c9c9c9; }

    #topbar { height: 1; background: #3b3b3b; color: #e8e8e8; }
    #topbar-title { width: 1fr; padding: 0 1; text-style: bold; }
    #topbar-status { width: auto; padding: 0 1; color: #b0b0b0; }

    #body { height: 1fr; }
    #sidebar { width: 26; height: 100%; background: #1f1f1f; border: solid #444444; border-title-color: #9a9a9a; }
    #tool-list { background: #1f1f1f; border: none; height: auto; padding: 0; }
    #tool-list > .option-list--option-highlighted { background: #4a4a4a; color: #f0f0f0; text-style: bold; }
    #tool-list:focus > .option-list--option-highlighted { background: #5c5c5c; }
    #sidebar-help { color: #666666; padding: 1 1 0 1; height: auto; }
    #main { width: 1fr; }
    ContentSwitcher { height: 1fr; }

    .pane-title { color: #eeeeee; text-style: bold; margin: 1 0 0 1; }
    .pane-sub { color: #777777; margin: 0 0 1 1; }
    .muted { color: #808080; }
    .columns { height: auto; }
    .box { height: auto; border: solid #444444; border-title-color: #a8a8a8; border-title-style: bold;
           background: #1e1e1e; padding: 0 1; margin: 0 1 0 0; }
    .settings { width: 1fr; min-width: 44; }
    .monitor { width: 1fr; }

    .field { height: 1; margin: 1 0 0 0; }
    .field > Label { width: 13; color: #8c8c8c; }
    .field > Select, .field > Input { width: 1fr; }
    .field > Button { margin-left: 1; }
    .checks { height: 1; }
    Checkbox { border: none; padding: 0; height: 1; background: transparent; margin-right: 2; }
    Checkbox > .toggle--button { background: #3a3a3a; color: #3a3a3a; }
    Checkbox.-on > .toggle--button { color: #e0e0e0; background: #5a5a5a; }
    Checkbox:focus > .toggle--label { text-style: bold underline; background: transparent; }

    Input { background: #2a2a2a; color: #dddddd; }
    Input:focus { background: #333333; }
    Input > .input--placeholder { color: #666666; }
    Select > SelectCurrent { background: #2a2a2a; }
    Select:focus > SelectCurrent { background: #333333; }

    Digits { color: #e6e6e6; width: auto; margin: 1 0 0 0; }
    .state { text-style: bold; color: #bdbdbd; margin: 1 0 0 0; }
    .state.-rec { color: #d08a8a; }
    Sparkline { height: 3; margin: 1 0 1 0; }
    Sparkline > .sparkline--min-color { color: #4a4a4a; }
    Sparkline > .sparkline--max-color { color: #e0e0e0; }
    #voice-level { margin: 1 0 0 0; height: 1; }

    #shot-preview { height: 18; }
    #shot-info { margin: 1 0 1 0; height: 2; }
    #conv-info { margin: 1 0 1 0; }
    ProgressBar { margin: 1 0 0 0; width: 100%; }
    ProgressBar > Bar { width: 1fr; }
    Bar > .bar--bar { color: #d0d0d0; background: #333333; }
    Bar > .bar--complete { color: #a6b39c; background: #333333; }
    #conv-status { margin: 0 0 1 0; }

    .actions { height: 1; margin: 1 0 1 1; }
    .actions > Button { margin-right: 2; min-width: 12; }
    Button { background: #3a3a3a; color: #e6e6e6; }
    Button:hover { background: #4d4d4d; }
    Button:focus { background: #5e5e5e; text-style: bold; }
    Button:disabled { background: #262626; color: #555555; }

    .files-box { height: 1fr; min-height: 12; }
    DataTable { background: #1e1e1e; height: 1fr; }
    DataTable > .datatable--header { background: #2e2e2e; color: #bdbdbd; text-style: bold; }
    DataTable > .datatable--cursor { background: #555555; color: #f2f2f2; }
    DataTable > .datatable--even-row { background: #222222; }

    #log { height: 9; background: #161616; border: solid #444444; border-title-color: #9a9a9a;
           scrollbar-size-vertical: 1; }

    #picker, #confirm { width: 80%; max-width: 100; height: 80%; margin: 2 4; background: #1e1e1e; }
    #confirm { height: auto; width: 50; }
    FilePicker, ConfirmDialog { align: center middle; background: #000000 60%; }
    #picker-tree { height: 1fr; background: #1a1a1a; margin: 1 0 0 0; }

    Footer { background: #2a2a2a; }
    """

    BINDINGS = [
        Binding("1", "tool('voice')", "Voice", show=False),
        Binding("2", "tool('screen')", "Screen", show=False),
        Binding("3", "tool('shot')", "Shot", show=False),
        Binding("4", "tool('convert')", "Convert", show=False),
        Binding("5", "tool('files')", "Files", show=False),
        Binding("r", "primary", "Start"),
        Binding("p", "pause", "Pause"),
        Binding("s", "stop", "Stop"),
        Binding("o", "open_output", "Output folder"),
        Binding("c", "clear_log", "Clear log"),
        Binding("q", "quit_app", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Label("▌ MULTIMEDIA TOOLKIT", id="topbar-title")
            yield Label("", id="topbar-status")
        with Horizontal(id="body"):
            with Vertical(id="sidebar") as sidebar:
                sidebar.border_title = "TOOLS"
                yield OptionList(*[Option(label, id=key) for key, label, _ in TOOLS], id="tool-list")
                yield Static("1-5  switch tool\n R   start\n P   pause\n S   stop\n O   output folder\n"
                             " C   clear log\n Q   quit", id="sidebar-help")
            with Vertical(id="main"):
                with ContentSwitcher(initial="voice", id="panes"):
                    for key, _, pane in TOOLS:
                        yield pane(id=key)
                yield RichLog(id="log", markup=False, wrap=True)
        yield Footer()

    def on_mount(self):
        self.register_theme(GREY_THEME)
        self.theme = "grey-terminal"
        self.quit_armed = False
        self.query_one("#log").border_title = "LOG"
        self.checks = self.environment_checks()
        self.update_status()
        self.set_interval(1, self.update_status)
        self.write_log("Multimedia Toolkit ready. Pick a tool on the left or press 1-5.")
        self.query_one("#tool-list", OptionList).highlighted = 0

    def environment_checks(self):
        ffmpeg = "ffmpeg ✓" if find_ffmpeg() else "ffmpeg ✗"
        try:
            import sounddevice as sd
            sd.query_devices(kind="input")
            mic = "mic ✓"
        except Exception:
            mic = "mic ✗"
        return f"{ffmpeg}  ·  {mic}"

    def update_status(self):
        recording = [pane.heading.lower() for pane in self.query(Pane) if pane.busy]
        activity = f"● busy: {', '.join(recording)}" if recording else "idle"
        if not recording:
            self.quit_armed = False
        self.query_one("#topbar-status", Label).update(
            f"{activity}  ·  {self.checks}  ·  {datetime.now().strftime('%H:%M:%S')}")

    # ---- logging & shared state
    def write_log(self, message, level="info"):
        styles = {"info": "#a8a8a8", "ok": "#c8d4be", "warn": "#d4c79a", "error": "#d49090"}
        marks = {"info": "·", "ok": "✓", "warn": "!", "error": "✗"}
        line = Text()
        line.append(datetime.now().strftime("%H:%M:%S "), style="#5f5f5f")
        line.append(f"{marks[level]} ", style=styles[level])
        line.append(message, style=styles[level])
        self.query_one("#log", RichLog).write(line)

    def recording_elsewhere(self, pane):
        return any(p.busy for p in (self.query_one(VoicePane), self.query_one(ScreenPane)) if p is not pane)

    def files_changed(self):
        self.query_one(FilesPane).reload()

    def current_pane(self):
        switcher = self.query_one("#panes", ContentSwitcher)
        return switcher.query_one(f"#{switcher.current}", Pane)

    def show_tool(self, key):
        self.query_one("#panes", ContentSwitcher).current = key
        keys = [k for k, _, _ in TOOLS]
        tool_list = self.query_one("#tool-list", OptionList)
        if tool_list.highlighted != keys.index(key):
            tool_list.highlighted = keys.index(key)

    @on(OptionList.OptionHighlighted, "#tool-list")
    def tool_highlighted(self, event):
        self.show_tool(event.option.id)

    # ---- actions
    def action_tool(self, key):
        self.show_tool(key)

    def action_primary(self):
        self.current_pane().primary()

    def action_pause(self):
        self.current_pane().pause()

    def action_stop(self):
        pane = self.current_pane()
        if pane.busy:
            pane.stop()
            return
        for other in self.query(Pane):  # stop whatever is recording, even from another tab
            if other.busy and isinstance(other, (VoicePane, ScreenPane)):
                other.stop()

    def action_open_output(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        open_path(OUTPUT_DIR)

    def action_clear_log(self):
        self.query_one("#log", RichLog).clear()

    def action_quit_app(self):
        busy = [pane for pane in self.query(Pane) if pane.busy]
        if busy and not self.quit_armed:
            self.quit_armed = True
            self.write_log("Work is still running. Press S to stop it, or Q again to quit anyway "
                           "(unfinished recordings will be lost).", "warn")
            return
        for pane in busy:
            pane.stop()
        self.exit()


def main():
    enable_dpi_awareness()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ToolkitApp().run()


if __name__ == "__main__":
    main()
