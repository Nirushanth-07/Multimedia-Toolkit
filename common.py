"""Shared helpers used by every tool in the Multimedia Toolkit."""

import os
import shutil
import sys
import threading
import time
from datetime import datetime

OUTPUT_DIR = "output"

# Progress bars and status icons are Unicode; make sure redirected output on Windows doesn't crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def enable_dpi_awareness():
    """Make Windows report real pixel coordinates (avoids blurry/offset captures on scaled displays)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def screen_capture():
    """Open an mss screen grabber (mss >= 10 renamed `mss.mss` to `mss.MSS`)."""
    import mss
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


def timestamped_path(prefix, extension, output=None):
    """Return `output` if given, otherwise output/<prefix>_YYYYmmdd_HHMMSS.<extension>.

    The parent directory is created if needed.
    """
    if output:
        path = output
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(OUTPUT_DIR, f"{prefix}_{stamp}.{extension.lstrip('.')}")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    return path


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024


def parse_region(text):
    """Parse 'x,y,width,height' into a dict usable by mss."""
    try:
        left, top, width, height = (int(v.strip()) for v in text.split(","))
    except ValueError:
        raise ValueError("Region must look like x,y,width,height (e.g. 100,100,800,600)")
    if width <= 0 or height <= 0:
        raise ValueError("Region width and height must be positive")
    return {"left": left, "top": top, "width": width, "height": height}


def countdown(seconds, message="Starting in"):
    for remaining in range(int(seconds), 0, -1):
        print(f"\r{message} {remaining}...", end="", flush=True)
        time.sleep(1)
    if seconds:
        print("\r" + " " * (len(message) + 10) + "\r", end="", flush=True)


def find_ffmpeg():
    """Locate an ffmpeg executable: bundled imageio-ffmpeg first, then the system PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


class KeyListener:
    """Reads single key presses in a background thread without requiring Enter.

    Usage:
        with KeyListener() as keys:
            key = keys.get()   # returns a lowercase character or None
    """

    def __init__(self):
        self._keys = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._old_term = None

    def __enter__(self):
        if not sys.stdin or not sys.stdin.isatty():
            return self  # non-interactive: no keyboard control, Ctrl+C still works
        if sys.platform == "win32":
            target = self._run_windows
        else:
            import termios
            import tty
            fd = sys.stdin.fileno()
            self._old_term = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            target = self._run_posix
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        if self._old_term is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_term)
        return False

    def get(self):
        with self._lock:
            return self._keys.pop(0) if self._keys else None

    def _push(self, char):
        with self._lock:
            self._keys.append(char.lower())

    def _run_windows(self):
        import msvcrt
        while not self._stop.is_set():
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ("\x00", "\xe0"):  # special key prefix, discard the next code
                    msvcrt.getwch()
                    continue
                self._push(char)
            else:
                time.sleep(0.05)

    def _run_posix(self):
        import select
        while not self._stop.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if ready:
                self._push(sys.stdin.read(1))


def select_region():
    """Freeze the screen and let the user drag a rectangle with the mouse.

    Returns an mss-style region dict, or None if cancelled with Esc / right click.
    """
    import tkinter as tk

    from PIL import Image, ImageEnhance, ImageTk

    enable_dpi_awareness()
    with screen_capture() as sct:
        virtual = sct.monitors[0]
        shot = sct.grab(virtual)
    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    dimmed = ImageEnhance.Brightness(image).enhance(0.6)

    result = {}
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(f"{virtual['width']}x{virtual['height']}{virtual['left']:+d}{virtual['top']:+d}")

    canvas = tk.Canvas(root, width=virtual["width"], height=virtual["height"],
                       highlightthickness=0, cursor="crosshair")
    canvas.pack()
    background = ImageTk.PhotoImage(dimmed)
    canvas.create_image(0, 0, image=background, anchor="nw")
    canvas.create_text(virtual["width"] // 2, 30, fill="white", font=("Segoe UI", 16, "bold"),
                       text="Drag to select a region  •  Esc / right click to cancel")

    state = {"start": None, "rect": None, "label": None, "preview": None, "photo": None}

    def on_press(event):
        state["start"] = (event.x, event.y)

    def on_drag(event):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        left, top = min(x0, event.x), min(y0, event.y)
        right, bottom = max(x0, event.x), max(y0, event.y)
        for key in ("rect", "label", "preview"):
            if state[key]:
                canvas.delete(state[key])
        if right - left > 1 and bottom - top > 1:
            state["photo"] = ImageTk.PhotoImage(image.crop((left, top, right, bottom)))
            state["preview"] = canvas.create_image(left, top, image=state["photo"], anchor="nw")
        state["rect"] = canvas.create_rectangle(left, top, right, bottom, outline="#00b4ff", width=2)
        state["label"] = canvas.create_text(left + 4, max(top - 12, 10), anchor="w", fill="white",
                                            font=("Segoe UI", 10, "bold"),
                                            text=f"{right - left} × {bottom - top}")

    def on_release(event):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        left, top = min(x0, event.x), min(y0, event.y)
        width, height = abs(event.x - x0), abs(event.y - y0)
        if width >= 5 and height >= 5:
            result["region"] = {"left": left + virtual["left"], "top": top + virtual["top"],
                                "width": width, "height": height}
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", lambda _e: root.destroy())
    canvas.bind("<ButtonPress-3>", lambda _e: root.destroy())
    root.focus_force()
    root.mainloop()
    return result.get("region")
