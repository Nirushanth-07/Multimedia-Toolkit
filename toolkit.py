"""Multimedia Toolkit - interactive menu for all tools.

Run `python toolkit.py` and follow the prompts. Every tool can also be used
directly from the command line (see README.md).
"""

import os
import sys

MENU = """
========================================
          Multimedia Toolkit
========================================
  1. Voice Recorder
  2. Screen Recorder
  3. Screenshot
  4. Audio/Video Converter
  5. Media File Info
  0. Exit
----------------------------------------"""


def ask(prompt, default=None, cast=str, choices=None):
    hint = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input(f"{prompt}{hint}: ").strip().strip('"')
        if not value:
            if default is not None:
                return default
            print("  Please enter a value.")
            continue
        try:
            value = cast(value)
        except ValueError:
            print("  Invalid value, try again.")
            continue
        if choices and value not in choices:
            print(f"  Choose one of: {', '.join(map(str, choices))}")
            continue
        return value


def ask_yes(prompt, default=False):
    answer = ask(f"{prompt} (y/n)", "y" if default else "n").lower()
    return answer.startswith("y")


def optional_float(value):
    return float(value) if value else None


def voice_recorder_menu():
    from voice_recorder import record
    minutes = ask("Duration in minutes (leave empty to record until you press Q)", "", optional_float)
    fmt = ask("Format (wav/mp3/flac/ogg/m4a)", "wav", str.lower, ["wav", "mp3", "flac", "ogg", "m4a"])
    stereo = ask_yes("Stereo?")
    record(output=_default_output("voice", fmt), duration=minutes * 60 if minutes else None,
           channels=2 if stereo else 1)


def screen_recorder_menu():
    from screen_recorder import record_screen
    area = ask("Area: 1 = full screen, 2 = select with mouse", "1", str, ["1", "2"])
    minutes = ask("Duration in minutes (leave empty to record until you press Q)", "", optional_float)
    fps = ask("Frames per second", 24, int)
    audio = ask_yes("Record microphone audio?")
    record_screen(duration=minutes * 60 if minutes else None, fps=fps, select=area == "2", audio=audio)


def screenshot_menu():
    from screenshot import take_screenshot
    mode = ask("Capture: 1 = full screen, 2 = select with mouse, 3 = each monitor", "1", str, ["1", "2", "3"])
    delay = ask("Delay in seconds", 0, int) if mode != "2" else 0
    fmt = ask("Format (png/jpg/webp)", "png", str.lower, ["png", "jpg", "webp"])
    take_screenshot(select=mode == "2", each=mode == "3", delay=delay, image_format=fmt,
                    clipboard=mode != "3" and sys.platform == "win32")


def converter_menu():
    from converter import convert
    source = ask("Input file")
    target = ask("Output file (the extension decides the format, e.g. song.mp3, clip.gif)")
    quality = ask("Quality (low/medium/high)", "medium", str.lower, ["low", "medium", "high"])
    start = ask("Start time (e.g. 00:00:05, empty = beginning)", "") or None
    end = ask("End time (empty = end of file)", "") or None
    overwrite = os.path.exists(target) and ask_yes(f"'{target}' exists. Overwrite?")
    convert(source, target, start=start, end=end, quality=quality, overwrite=overwrite)


def info_menu():
    from converter import print_info
    print_info(ask("Media file"))


def _default_output(prefix, extension):
    from common import timestamped_path
    return timestamped_path(prefix, extension)


ACTIONS = {
    "1": voice_recorder_menu,
    "2": screen_recorder_menu,
    "3": screenshot_menu,
    "4": converter_menu,
    "5": info_menu,
}


def main():
    while True:
        print(MENU)
        try:
            choice = input("Choose an option: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if choice in ("0", "q", "exit"):
            break
        action = ACTIONS.get(choice)
        if not action:
            print("Unknown option.")
            continue
        print()
        try:
            action()
        except KeyboardInterrupt:
            print("\nCancelled.")
        except Exception as error:
            print(f"❌ Error: {error}")
        input("\nPress Enter to return to the menu...")
    print("Goodbye! 👋")


if __name__ == "__main__":
    main()
