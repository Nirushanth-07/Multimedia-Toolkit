"""Screenshot Utility - capture the full screen, a monitor, a region or a mouse selection.

Examples:
    python screenshot.py                          # all monitors as one image
    python screenshot.py --select                 # drag a rectangle with the mouse
    python screenshot.py --monitor 1 --delay 3    # primary monitor after a 3 s countdown
    python screenshot.py --each                   # one file per monitor
    python screenshot.py --region 0,0,800,600 -o shot.jpg --quality 90
    python screenshot.py --list-monitors
"""

import argparse
import os
import sys

from PIL import Image

from common import (countdown, enable_dpi_awareness, format_size, parse_region, screen_capture,
                    select_region, timestamped_path)

FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP", "webp": "WEBP", "tiff": "TIFF"}


def grab(sct, area):
    shot = sct.grab(area)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def save_image(image, path, quality=95):
    extension = os.path.splitext(path)[1].lstrip(".").lower()
    fmt = FORMATS.get(extension)
    if not fmt:
        raise ValueError(f"Unsupported image format '.{extension}'. Use: {', '.join(FORMATS)}")
    options = {}
    if fmt in ("JPEG", "WEBP"):
        options["quality"] = quality
    if fmt == "PNG":
        options["optimize"] = True
    image.save(path, fmt, **options)
    return path


def copy_to_clipboard(image):
    """Copy an image to the Windows clipboard. Returns True on success."""
    if sys.platform != "win32":
        return False
    import ctypes
    import io
    from ctypes import wintypes

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "BMP")
    data = buffer.getvalue()[14:]  # strip the BMP file header -> CF_DIB payload

    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

    CF_DIB, GMEM_MOVEABLE = 8, 0x0002
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(CF_DIB, handle))
    finally:
        user32.CloseClipboard()


def take_screenshot(output=None, monitor=0, region=None, select=False, each=False, delay=0,
                    image_format="png", quality=95, clipboard=False, quiet=False):
    """Capture the screen and return a list of saved file paths.

    monitor: 0 = all monitors combined, 1 = primary, 2 = second, ...
    region:  dict with left/top/width/height (overrides monitor)
    select:  pick the region interactively with the mouse
    each:    save every monitor to its own file
    """
    enable_dpi_awareness()
    if select:
        region = select_region()
        if region is None:
            raise RuntimeError("Selection cancelled.")
    if delay:
        countdown(delay, "📸 Capturing in")

    saved = []
    with screen_capture() as sct:
        if each:
            targets = [(f"screenshot_monitor{i}", m) for i, m in enumerate(sct.monitors[1:], 1)]
        elif region:
            targets = [("screenshot", region)]
        else:
            if monitor >= len(sct.monitors) or monitor < 0:
                raise ValueError(f"Monitor {monitor} not found. Available: 0-{len(sct.monitors) - 1}")
            targets = [("screenshot", sct.monitors[monitor])]

        for prefix, area in targets:
            image = grab(sct, area)
            if output and each:
                base, ext = os.path.splitext(output)
                path = timestamped_path(prefix, image_format, f"{base}_{prefix.split('_')[-1]}{ext}")
            else:
                path = timestamped_path(prefix, image_format, output)
            save_image(image, path, quality)
            saved.append(path)
            if not quiet:
                print(f"✅ Saved {path}  ({image.width}x{image.height}, {format_size(os.path.getsize(path))})")

        if clipboard and len(saved) == 1:
            if copy_to_clipboard(image):
                if not quiet:
                    print("📋 Copied to clipboard")
            elif not quiet:
                print("⚠  Clipboard copy is only supported on Windows.")
    return saved


def list_monitors():
    enable_dpi_awareness()
    with screen_capture() as sct:
        for index, mon in enumerate(sct.monitors):
            label = "all monitors" if index == 0 else ("primary" if index == 1 else "")
            print(f"  [{index}] {mon['width']}x{mon['height']} at ({mon['left']}, {mon['top']})  {label}")


def build_parser():
    parser = argparse.ArgumentParser(description="Capture screenshots.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--monitor", type=int, default=0,
                        help="0 = all monitors (default), 1 = primary, 2 = second, ...")
    target.add_argument("--region", help="capture x,y,width,height")
    target.add_argument("-s", "--select", action="store_true", help="select a region with the mouse")
    target.add_argument("--each", action="store_true", help="save each monitor as a separate file")
    parser.add_argument("-o", "--output", help="output file. Default: output/screenshot_<timestamp>.png")
    parser.add_argument("-f", "--format", default="png", choices=sorted(FORMATS),
                        help="image format when no --output is given (default: png)")
    parser.add_argument("--quality", type=int, default=95, help="JPEG/WEBP quality 1-100 (default: 95)")
    parser.add_argument("--delay", type=int, default=0, help="seconds to wait before capturing")
    parser.add_argument("-c", "--clipboard", action="store_true", help="also copy the image to the clipboard")
    parser.add_argument("--list-monitors", action="store_true", help="list monitors and exit")
    return parser


def main():
    args = build_parser().parse_args()
    if args.list_monitors:
        list_monitors()
        return
    try:
        region = parse_region(args.region) if args.region else None
        take_screenshot(args.output, args.monitor, region, args.select, args.each, args.delay,
                        args.format, max(1, min(args.quality, 100)), args.clipboard)
    except (ValueError, RuntimeError) as error:
        sys.exit(f"Error: {error}")
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")


if __name__ == "__main__":
    main()
