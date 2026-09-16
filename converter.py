"""Audio/Video Converter - convert, compress, trim and extract media using ffmpeg.

The output format is picked from the output file extension. ffmpeg is bundled
through the imageio-ffmpeg package, so no separate install is required.

Examples:
    python converter.py recording.wav recording.mp3
    python converter.py screen.mp4 screen.webm --quality high
    python converter.py video.mp4 audio.mp3 --bitrate 320k        # extract audio
    python converter.py video.mp4 clip.mp4 --start 00:01:10 --end 00:01:40
    python converter.py video.mp4 small.mp4 --width 1280 --fps 30 --quality low
    python converter.py video.mp4 demo.gif --width 640 --fps 12
    python converter.py --batch output/ --to mp3                  # convert a whole folder
    python converter.py --info video.mp4
"""

import argparse
import glob
import os
import re
import subprocess
import sys

from common import find_ffmpeg, format_duration, format_size

AUDIO_FORMATS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"}
VIDEO_FORMATS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".gif", ".flv", ".wmv"}
MEDIA_FORMATS = AUDIO_FORMATS | VIDEO_FORMATS

AUDIO_CODECS = {
    ".mp3": ["-c:a", "libmp3lame"],
    ".m4a": ["-c:a", "aac"],
    ".aac": ["-c:a", "aac"],
    ".ogg": ["-c:a", "libvorbis"],
    ".opus": ["-c:a", "libopus"],
    ".flac": ["-c:a", "flac"],
    ".wav": ["-c:a", "pcm_s16le"],
}

# Constant Rate Factor per quality preset: lower = better quality / bigger file
QUALITY_CRF = {
    "x264": {"low": 30, "medium": 23, "high": 18},
    "vp9": {"low": 40, "medium": 32, "high": 24},
}
QUALITY_AUDIO_BITRATE = {"low": "96k", "medium": "160k", "high": "256k"}
QUALITY_VORBIS = {"low": 3, "medium": 5, "high": 8}


class ConversionError(RuntimeError):
    pass


def require_ffmpeg():
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ConversionError("ffmpeg not found. Install it with: pip install imageio-ffmpeg")
    return ffmpeg


def parse_time(value):
    """Accept seconds ('90', '12.5') or timestamps ('1:30', '00:01:30.5'). Returns seconds."""
    if value is None:
        return None
    try:
        parts = [float(p) for p in str(value).split(":")]
    except ValueError:
        raise ConversionError(f"Invalid time '{value}'. Use seconds or HH:MM:SS.")
    if len(parts) > 3:
        raise ConversionError(f"Invalid time '{value}'. Use seconds or HH:MM:SS.")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def probe(path):
    """Return basic information about a media file by parsing ffmpeg's banner output."""
    if not os.path.isfile(path):
        raise ConversionError(f"File not found: {path}")
    result = subprocess.run([require_ffmpeg(), "-hide_banner", "-i", path],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = result.stderr
    info = {"path": path, "size": os.path.getsize(path), "duration": None,
            "video": None, "audio": None, "bitrate": None}

    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        h, m, s = match.groups()
        info["duration"] = int(h) * 3600 + int(m) * 60 + float(s)
    match = re.search(r"bitrate: (\d+ kb/s)", text)
    if match:
        info["bitrate"] = match.group(1)

    video = re.search(r"Stream #.*?Video: (\w+).*?, (\d{2,5})x(\d{2,5})(?:.*?, ([\d.]+) fps)?", text)
    if video:
        codec, width, height, fps = video.groups()
        info["video"] = {"codec": codec, "width": int(width), "height": int(height),
                         "fps": float(fps) if fps else None}
    audio = re.search(r"Stream #.*?Audio: (\w+).*?, (\d+) Hz, ([\w.()]+)", text)
    if audio:
        codec, rate, layout = audio.groups()
        info["audio"] = {"codec": codec, "samplerate": int(rate), "channels": layout}

    if info["duration"] is None and not info["video"] and not info["audio"]:
        raise ConversionError(f"Not a readable media file: {path}")
    return info


def print_info(path):
    info = probe(path)
    print(f"File:      {info['path']}")
    print(f"Size:      {format_size(info['size'])}")
    if info["duration"] is not None:
        print(f"Duration:  {format_duration(info['duration'])} ({info['duration']:.2f}s)")
    if info["bitrate"]:
        print(f"Bitrate:   {info['bitrate']}")
    if info["video"]:
        v = info["video"]
        fps = f" @ {v['fps']:g} fps" if v["fps"] else ""
        print(f"Video:     {v['codec']}  {v['width']}x{v['height']}{fps}")
    if info["audio"]:
        a = info["audio"]
        print(f"Audio:     {a['codec']}  {a['samplerate']} Hz  {a['channels']}")


def build_command(ffmpeg, source, target, info, start=None, end=None, bitrate=None,
                  quality="medium", width=None, height=None, fps=None, mute=False,
                  audio_only=False, samplerate=None, channels=None, overwrite=False):
    src_ext = os.path.splitext(source)[1].lower()
    ext = os.path.splitext(target)[1].lower()
    if ext not in MEDIA_FORMATS:
        raise ConversionError(f"Unsupported output format '{ext or target}'. "
                              f"Supported: {', '.join(sorted(MEDIA_FORMATS))}")
    to_audio = ext in AUDIO_FORMATS or audio_only
    if to_audio and not info["audio"]:
        raise ConversionError(f"'{source}' has no audio stream to convert.")
    if not to_audio and not info["video"]:
        raise ConversionError(f"'{source}' has no video stream - choose an audio output format.")
    if src_ext == ext and not any([start, end, bitrate, width, height, fps, mute, samplerate, channels]) \
            and quality == "medium":
        print("Note: input and output formats match and no options were given - re-encoding anyway.")

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    cmd.append("-y" if overwrite else "-n")
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", source]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0):.3f}"]

    filters = []
    if width or height:
        # -2 keeps the aspect ratio and an even dimension (required by most codecs)
        filters.append(f"scale={width or -2}:{height or -2}:flags=lanczos")
    if fps:
        filters.append(f"fps={fps}")

    if to_audio:
        cmd += ["-vn"]
        cmd += AUDIO_CODECS.get(ext, [])
        if ext == ".ogg" and not bitrate:
            # Vorbis rejects high fixed bitrates on mono input; quality-based VBR always works
            cmd += ["-q:a", str(QUALITY_VORBIS[quality])]
        elif ext not in (".wav", ".flac"):
            cmd += ["-b:a", bitrate or QUALITY_AUDIO_BITRATE[quality]]
        if samplerate:
            cmd += ["-ar", str(samplerate)]
        if channels:
            cmd += ["-ac", str(channels)]
    elif ext == ".gif":
        # two-pass palette in a single filter graph gives far better colours than the default
        chain = ",".join(filters + ["split[a][b]", "[a]palettegen=stats_mode=diff[p]",
                                    "[b][p]paletteuse=dither=bayer:bayer_scale=5"])
        cmd += ["-filter_complex", chain, "-loop", "0"]
    else:
        if filters:
            cmd += ["-vf", ",".join(filters)]
        if ext == ".webm":
            cmd += ["-c:v", "libvpx-vp9", "-crf", str(QUALITY_CRF["vp9"][quality]), "-b:v", "0",
                    "-row-mt", "1", "-deadline", "good", "-cpu-used", "4"]
            audio_codec = ["-c:a", "libopus"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(QUALITY_CRF["x264"][quality]),
                    "-pix_fmt", "yuv420p"]
            if bitrate:
                cmd += ["-maxrate", bitrate, "-bufsize", bitrate]
            audio_codec = ["-c:a", "aac"]
            if ext in (".mp4", ".mov"):
                cmd += ["-movflags", "+faststart"]
        if mute or not info["audio"]:
            cmd += ["-an"]
        else:
            cmd += audio_codec + ["-b:a", QUALITY_AUDIO_BITRATE[quality]]
            if samplerate:
                cmd += ["-ar", str(samplerate)]
            if channels:
                cmd += ["-ac", str(channels)]

    cmd.append(target)
    return cmd


def convert(source, target, start=None, end=None, bitrate=None, quality="medium", width=None,
            height=None, fps=None, mute=False, samplerate=None, channels=None,
            overwrite=False, quiet=False, progress=None):
    """Convert `source` to `target` (format taken from the extension). Returns the target path.

    progress: optional callback(fraction 0-1, seconds_done, seconds_total) called during encoding.
    """
    ffmpeg = require_ffmpeg()
    if os.path.abspath(source) == os.path.abspath(target):
        raise ConversionError("Input and output must be different files.")
    if os.path.exists(target) and not overwrite:
        raise ConversionError(f"'{target}' already exists (use --overwrite to replace it).")

    info = probe(source)
    start, end = parse_time(start), parse_time(end)
    if start is not None and start < 0 or end is not None and end <= (start or 0):
        raise ConversionError("--end must be after --start, and times must be positive.")
    if info["duration"] and start is not None and start >= info["duration"]:
        raise ConversionError(f"--start is beyond the end of the file ({format_duration(info['duration'])}).")

    total = info["duration"] or 0
    if end is not None:
        total = min(total, end) if total else end
    if start is not None:
        total -= start

    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    cmd = build_command(ffmpeg, source, target, info, start, end, bitrate, quality, width, height,
                        fps, mute, samplerate=samplerate, channels=channels, overwrite=True)

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
    try:
        for line in process.stdout:
            if not line.startswith("out_time_us=") or not total:
                continue
            try:
                done = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            pct = max(0.0, min(done / total, 1.0))
            if progress:
                progress(pct, done, total)
            if quiet:
                continue
            bar = "█" * int(pct * 30) + "░" * (30 - int(pct * 30))
            print(f"\r  {bar} {pct * 100:5.1f}%  {format_duration(done)} / {format_duration(total)}",
                  end="", flush=True)
        stderr = process.stderr.read()
        process.wait()
    except KeyboardInterrupt:
        process.kill()
        process.wait()
        if os.path.exists(target):
            os.remove(target)
        raise

    if process.returncode != 0:
        if os.path.exists(target):
            os.remove(target)
        raise ConversionError(f"ffmpeg failed:\n{stderr.strip()}")
    if not quiet:
        print(f"\r  {'█' * 30} 100.0%  {' ' * 20}")
        before, after = info["size"], os.path.getsize(target)
        change = (after - before) / before * 100 if before else 0
        print(f"✅ {target}  ({format_size(before)} → {format_size(after)}, {change:+.0f}%)")
    return target


def batch_convert(folder, to_ext, recursive=False, overwrite=False, **options):
    to_ext = "." + to_ext.lstrip(".").lower()
    if to_ext not in MEDIA_FORMATS:
        raise ConversionError(f"Unsupported output format '{to_ext}'.")
    pattern = os.path.join(folder, "**", "*") if recursive else os.path.join(folder, "*")
    files = sorted(f for f in glob.glob(pattern, recursive=recursive)
                   if os.path.isfile(f) and os.path.splitext(f)[1].lower() in MEDIA_FORMATS
                   and os.path.splitext(f)[1].lower() != to_ext)
    if not files:
        raise ConversionError(f"No convertible media files found in '{folder}'.")

    failures = 0
    stems = [os.path.splitext(f)[0] for f in files]
    for index, source in enumerate(files, 1):
        stem, src_ext = os.path.splitext(source)
        # e.g. song.wav + song.ogg -> song_wav.mp3 and song_ogg.mp3 instead of clashing
        target = (f"{stem}_{src_ext.lstrip('.').lower()}" if stems.count(stem) > 1 else stem) + to_ext
        print(f"[{index}/{len(files)}] {source}")
        try:
            convert(source, target, overwrite=overwrite, **options)
        except ConversionError as error:
            failures += 1
            print(f"  ✗ {str(error).splitlines()[0]}")
    print(f"\nDone: {len(files) - failures} converted, {failures} failed.")
    return failures


def build_parser():
    parser = argparse.ArgumentParser(
        description="Convert audio and video files. The output format comes from the file extension.",
        epilog=f"Formats: {', '.join(sorted(MEDIA_FORMATS))}")
    parser.add_argument("input", nargs="?", help="source file")
    parser.add_argument("output", nargs="?", help="destination file (e.g. song.mp3, clip.webm, demo.gif)")
    parser.add_argument("--info", action="store_true", help="show details about the input file and exit")
    parser.add_argument("--batch", metavar="FOLDER", help="convert every media file in a folder")
    parser.add_argument("--to", metavar="EXT", help="target extension for --batch (e.g. mp3)")
    parser.add_argument("--recursive", action="store_true", help="include sub-folders in --batch")

    edit = parser.add_argument_group("editing")
    edit.add_argument("--start", help="start time (seconds or HH:MM:SS)")
    edit.add_argument("--end", help="end time (seconds or HH:MM:SS)")
    edit.add_argument("--width", type=int, help="resize video width (keeps aspect ratio)")
    edit.add_argument("--height", type=int, help="resize video height (keeps aspect ratio)")
    edit.add_argument("--fps", type=float, help="change video frame rate")
    edit.add_argument("--mute", action="store_true", help="remove the audio track from a video")

    enc = parser.add_argument_group("encoding")
    enc.add_argument("-q", "--quality", choices=("low", "medium", "high"), default="medium",
                     help="quality/size trade-off (default: medium)")
    enc.add_argument("-b", "--bitrate", help="audio bitrate for audio outputs, e.g. 192k; "
                                             "max video bitrate for video outputs, e.g. 2M")
    enc.add_argument("--samplerate", type=int, help="audio sample rate, e.g. 44100")
    enc.add_argument("--channels", type=int, choices=(1, 2), help="audio channels")
    parser.add_argument("-y", "--overwrite", action="store_true", help="overwrite existing files")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    options = dict(start=args.start, end=args.end, bitrate=args.bitrate, quality=args.quality,
                   width=args.width, height=args.height, fps=args.fps, mute=args.mute,
                   samplerate=args.samplerate, channels=args.channels)
    try:
        if args.info:
            if not args.input:
                parser.error("--info needs an input file")
            print_info(args.input)
        elif args.batch:
            if not args.to:
                parser.error("--batch requires --to, e.g. --to mp3")
            failures = batch_convert(args.batch, args.to, args.recursive, args.overwrite, **options)
            sys.exit(1 if failures else 0)
        else:
            if not args.input or not args.output:
                parser.error("provide an input and an output file (or use --batch / --info)")
            convert(args.input, args.output, overwrite=args.overwrite, **options)
    except ConversionError as error:
        sys.exit(f"Error: {error}")
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")


if __name__ == "__main__":
    main()
