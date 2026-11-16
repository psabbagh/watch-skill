#!/usr/bin/env python3
"""watch.py: get what a video says and what it shows, so it can be learned.

    watch.py open <link or file> [--detail light|normal|full] [--at 1:23,4:56] [--model small]
    watch.py more <id> --at 9:25,10:10      more pictures from the video kept in the working folder
    watch.py status

It takes the words from captions (or transcribes them on this computer when there are none) and picks
pictures where the scene changes: slides, screens, diagrams, demos. Everything lands in
<library>/_working/<id>/: source.md (the words with each picture's time and file in place), frames/*.jpg,
meta.json, words.json and the downloaded video, kept so `more` can take extra pictures. library.py turns them
into notes and deletes the whole folder. Videos are downloaded from the site they are on; nothing is uploaded.

The tools (yt-dlp, PyAV, Pillow, and faster-whisper when needed) live in a private environment under
~/.cache/learning-skills, shared with the listen skill. That needs Python 3.10 to 3.14 somewhere on the computer.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from library import learned_sources, library, log, make_id, working  # noqa: E402
from media import (DEFAULT_MODEL, captions, ensure_frames, ensure_venv, has_module, hours, stamp,  # noqa: E402
                   transcribe, venv_python, ytdlp)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
# detail: (most pictures, shortest gap between two, longest stretch with no picture), in seconds
DETAIL = {"light": (15, 20.0, 600.0), "normal": (40, 8.0, 180.0), "full": (100, 3.0, 60.0)}
FRAME_WIDTH = 960
THUMB = (64, 36)    # grey thumbnail each keyframe is compared on
PIXEL_STEP = 24     # a thumbnail pixel this much brighter or darker counts as changed
NEW_SCENE = 0.05    # share of changed pixels, against the start of the current scene, that makes a new scene
SAME_SHOT = 0.02    # at most this share changed against an earlier scene: the same shot again, so skipped


def parse_time(text: str) -> float:
    secs = 0.0
    for part in text.strip().split(":"):
        secs = secs * 60 + float(part)
    return secs


# ---------------------------------------------------------------- choosing pictures

def changed(a: bytes, b: bytes) -> float:
    return sum(1 for x, y in zip(a, b) if abs(x - y) > PIXEL_STEP) / max(1, len(a))


def blank(g: bytes) -> bool:
    """A black, white or single-colour screen."""
    mean = sum(g) / max(1, len(g))
    return sum((x - mean) ** 2 for x in g) / max(1, len(g)) < 40


def choose_times(scan: list, duration: float, detail: str, at: list) -> list:
    """From (time, thumbnail) for each keyframe, the times to take pictures at.

    A scene lasts until a keyframe differs enough from the scene's first keyframe, so slow builds (an animation
    drawing itself, bullet points appearing) count once they add up. Each scene's picture is its last keyframe,
    where the build is finished. Blank screens and shots seen before are skipped, long stretches still get a
    picture, and the total stays within the detail level. Times asked for with --at are always added."""
    most, gap, stretch = DETAIL[detail]
    frames = [(t, g) for t, g in scan if not blank(g)]
    if not frames:
        return sorted(set(at))
    scenes = []  # [start time, start thumbnail, picture time, picture thumbnail, how new it was]
    for t, g in frames:
        if scenes:
            cur = scenes[-1]
            if changed(g, cur[1]) < NEW_SCENE or t - cur[0] < gap:
                cur[2], cur[3] = t, g  # still this scene: its picture moves later, to the finished state
                continue
            # A shot seen in the last 20 scenes (a presenter cut back to between slides) is not taken again.
            if any(changed(g, s[3]) < SAME_SHOT or changed(g, s[1]) < SAME_SHOT for s in scenes[-21:-1]):
                continue
        scenes.append([t, g, t, g, changed(g, scenes[-1][3]) if scenes else 1.0])
    picked = {s[2]: s[4] for s in scenes}
    # A long stretch with no new scene (a talking head) still gets a picture now and then.
    times, end = sorted(picked), duration or frames[-1][0]
    edges = [frames[0][0]] + times + [end]
    for a, b in zip(edges, edges[1:]):
        t = a + stretch
        while t < b - gap:
            nearest = min(frames, key=lambda f: abs(f[0] - t))[0]
            picked.setdefault(nearest, 0.0)
            t += stretch
    times = sorted(picked)
    if len(times) > most:  # keep the most new-looking half, and spread the rest evenly
        keep = set(sorted(times, key=lambda x: -picked[x])[: most // 2])
        rest = [x for x in times if x not in keep]
        step = len(rest) / max(1, most - len(keep))
        keep |= {rest[int(i * step)] for i in range(most - len(keep)) if int(i * step) < len(rest)}
        times = sorted(keep)
    return sorted(set(times) | set(at))


def longest_gaps(times: list, duration: float, n: int = 3, least: float = 90.0) -> list:
    """The longest stretches with no picture, for the reader to check against the words."""
    edges = sorted(times) + ([duration] if duration else [])
    gaps = sorted(((b - a, a, b) for a, b in zip(edges, edges[1:]) if b - a >= least), reverse=True)[:n]
    return [f"{stamp(a)} to {stamp(b)}" for _, a, b in sorted(gaps, key=lambda g: g[1])]


SCAN = r"""
import base64, json, sys
import av
c = av.open(sys.argv[1])
v = c.streams.video[0]
v.codec_context.skip_frame = "NONKEY"
out = []
for f in c.decode(v):
    if f.time is None:
        continue
    g = bytes(f.reformat(width=%d, height=%d, format="gray").planes[0])[:%d]
    out.append([round(f.time, 2), base64.b64encode(g).decode()])
d = float(v.duration * v.time_base) if v.duration else (c.duration / 1e6 if c.duration else None)
print(json.dumps({"scan": out, "duration": d}))
""" % (THUMB[0], THUMB[1], THUMB[0] * THUMB[1])
GRAB = r"""
import json, sys
import av
path, folder, width, times = sys.argv[1], sys.argv[2], int(sys.argv[3]), json.loads(sys.argv[4])
c = av.open(path)
v = c.streams.video[0]
saved = []
for t in times:
    c.seek(int(max(0, t) / v.time_base), stream=v, backward=True, any_frame=False)
    for f in c.decode(v):
        if f.time is None or f.time + 0.05 < t:
            continue
        img = f.to_image()
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)))
        name = "%s/%06d.jpg" % (folder, int(round(t)))
        img.convert("RGB").save(name, quality=82)
        saved.append([round(t, 2), name])
        break
print(json.dumps(saved))
"""


def in_venv(code: str, *args, timeout=None):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        script = f.name
    try:
        return subprocess.run([str(venv_python()), script, *map(str, args)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    finally:
        Path(script).unlink(missing_ok=True)


def scan_and_choose(video: Path, detail: str, at: list) -> tuple[list, float | None]:
    r = in_venv(SCAN, video, timeout=3600)
    if r.returncode:
        raise RuntimeError("could not read the video's pictures: " + r.stderr.strip()[-300:])
    info = json.loads(r.stdout.strip().splitlines()[-1])
    scan = [(t, base64.b64decode(g)) for t, g in info["scan"]]
    return choose_times(scan, info["duration"] or 0, detail, at), info["duration"]


def grab(video: Path, folder: Path, times: list) -> list:
    folder.mkdir(parents=True, exist_ok=True)
    r = in_venv(GRAB, video, folder, FRAME_WIDTH, json.dumps(sorted(times)), timeout=3600)
    if r.returncode:
        raise RuntimeError("could not save the pictures: " + r.stderr.strip()[-300:])
    return json.loads(r.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- one video

def download(url: str, work: Path, what: str) -> Path:
    if what == "video":
        # A picture stream on its own, at most 720 lines: no merging, so no ffmpeg is needed.
        fmt = "bv*[height<=720][ext=mp4]/bv*[height<=720]/b[height<=720]/bv*/b"
    else:
        fmt = "bestaudio/best"
    r = ytdlp("-f", fmt, "-o", str(work / f"{what}.%(ext)s"), "--no-playlist", "--", url)
    files = [f for f in work.glob(f"{what}.*") if not f.name.endswith(".part")]
    if r.returncode or not files:
        raise RuntimeError(f"the {what} would not download: " + (r.stderr.strip().splitlines() or ["no reason given"])[-1])
    return files[0]


def write_source(folder: Path, meta: dict, rows: list, shots: list) -> None:
    lines = [f"# {meta['title']}", "", f"Source: {meta['source']}", f"Words from: {meta['how']}"]
    for key in ("show", "date"):
        if meta.get(key):
            lines.append(f"{key.title()}: {meta[key]}")
    lines += ["", "Working copy for reading only. It is deleted when the notes are saved.",
              "Each [picture ...] line is a frame from the video at that time; open the file to see it.", "",
              "## Transcript And Pictures", ""]
    events = [(t, 0, text) for t, text in rows] + [(t, 1, Path(name).name) for t, name in shots]
    bucket, last = [], None
    for t, kind, value in sorted(events, key=lambda e: (e[0], -e[1])):  # a picture before words at the same time
        if kind == 1:
            if bucket:
                lines += [" ".join(bucket), ""]
                bucket, last = [], None
            lines += [f"[picture {stamp(t)}: frames/{value}]", ""]
            continue
        if last is None or t - last >= 30:
            if bucket:
                lines += [" ".join(bucket), ""]
            bucket, last = [f"[{stamp(t)}]"], t
        bucket.append(value)
    if bucket:
        lines.append(" ".join(bucket))
    if not rows:
        lines += ["", "(No spoken words were found: the pictures are all there is.)"]
    (folder / "source.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_at(text: str | None) -> list:
    return [parse_time(x) for x in text.split(",") if x.strip()] if text else []


def report(folder: Path, meta: dict, shots: list, asked: int) -> None:
    write_source(folder, meta, meta.pop("_rows"), shots)
    meta["frames"] = [{"time": stamp(t), "file": str(Path(name))} for t, name in sorted(shots)]
    gaps = longest_gaps([t for t, _ in shots], meta.get("seconds") or 0)
    (folder / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"OK {meta['title']}: {meta['words']:,} words ({meta['how']}), {len(shots)} pictures"
        + (f" ({asked} of them at the times asked for)" if asked else "")
        + (". Note: this is already in the library." if meta["source"] in learned_sources() else "."))
    print(json.dumps({**{k: meta.get(k) for k in ("id", "title", "show", "how", "words", "detail")},
                      "length": hours(meta["seconds"]) if meta.get("seconds") else None,
                      "pictures": len(shots), "pictures_asked_for": asked,
                      "longest_stretches_without_a_picture": gaps,
                      "source_file": str(folder / "source.md"), "frames_folder": str(folder / "frames"),
                      "video_kept_until_save": meta.get("video")}, indent=1, ensure_ascii=False))


def cmd_open(a) -> int:
    raw = a.source.strip()
    try:
        at = parse_at(a.at)
    except ValueError:
        log("FAILED: --at takes times like 1:23,4:56 or 83,296.")
        return 1
    local = Path(raw).expanduser()
    if local.exists() and local.suffix.lower() not in VIDEO_EXT:
        log(f"FAILED: {local.name} is not a video file this reads ({', '.join(sorted(VIDEO_EXT))}). "
            "For audio alone, the listen skill fits better.")
        return 1
    if not local.exists() and "spotify.com" in raw:
        log("FAILED: Spotify locks its media so no other tool can read it.")
        return 1
    if not local.exists() and not re.match(r"https?://", raw):
        log(f"FAILED: not a link and not a file that exists: {raw}.")
        return 1
    try:
        ensure_frames()
    except RuntimeError as exc:
        log(f"FAILED setup: {exc}")
        return 2
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        try:
            if local.exists():
                info, source, rows, how = {"title": local.stem}, str(local.resolve()), None, ""
            else:
                source = raw
                log("Reading the video's details and captions.")
                rows, info, how = captions(raw, work)
                if info.get("_type") == "playlist" or info.get("entries"):
                    log("FAILED: that is a playlist or channel. Give one video at a time; for the words of many "
                        "videos, the listen skill takes a whole playlist.")
                    return 1
            sid = make_id(info.get("title") or source, source)
            folder = working() / sid
            folder.mkdir(parents=True, exist_ok=True)
            # Opening again replaces the pictures and words but keeps any notes already written.
            shutil.rmtree(folder / "frames", ignore_errors=True)
            for old in folder.glob("video.*"):
                old.unlink()
            if local.exists():
                video = local
            else:
                if info.get("duration"):
                    log(f"{info.get('title')}: {hours(info['duration'])}. Downloading the pictures (at most 720p).")
                video = download(raw, folder, "video")
            if not rows:
                log("No captions, so transcribing the sound on this computer.")
                sound = video if local.exists() else download(raw, work, "audio")
                try:
                    rows, _ = transcribe(sound, a.model)
                except RuntimeError as exc:
                    if "no audio" not in str(exc).lower() and "stream" not in str(exc).lower():
                        raise
                    rows = []
                how = f"transcribed on this computer ({a.model} model)" if rows else "no spoken words"
            log("Choosing pictures where the scene changes.")
            times, duration = scan_and_choose(video, a.detail, at)
            shots = grab(video, folder / "frames", times)
        except RuntimeError as exc:
            log(f"FAILED: {exc}.")
            return 2
    rows = rows or []
    (folder / "words.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    meta = {"id": sid, "kind": "video", "medium": "video", "made_by": "watch", "title": info.get("title") or source,
            "show": info.get("channel") or info.get("uploader"), "date": info.get("upload_date"),
            "source": source, "how": how or "captions", "seconds": info.get("duration") or duration,
            "words": sum(len(t.split()) for _, t in rows), "detail": a.detail, "video": str(video),
            "asked_for": sorted(at), "_rows": rows}
    report(folder, meta, shots, len(at))
    return 0


def cmd_more(a) -> int:
    """More pictures for a video already open, from the copy kept in its working folder: nothing is downloaded
    again and notes already written stay."""
    folder = working() / a.id
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        rows = [tuple(r) for r in json.loads((folder / "words.json").read_text(encoding="utf-8"))]
        at = parse_at(a.at)
    except (OSError, ValueError):
        log(f"FAILED: {a.id} is not open in the working folder, or --at is not times like 1:23,4:56. "
            "Run `watch.py open` first.")
        return 1
    video = Path(meta.get("video") or "")
    if not video.is_file():
        log("FAILED: the video is no longer in the working folder. Run `watch.py open` again with --at.")
        return 1
    try:
        ensure_frames()
        new = grab(video, folder / "frames", at)
    except RuntimeError as exc:
        log(f"FAILED: {exc}.")
        return 2
    old = [(parse_time(f["time"]), f["file"]) for f in meta.get("frames", [])]
    shots = sorted({round(t): (t, n) for t, n in old + [tuple(s) for s in new]}.values())
    meta["asked_for"] = sorted(set(meta.get("asked_for", []) + at))
    meta["_rows"] = rows
    report(folder, meta, shots, len(meta["asked_for"]))
    return 0


def cmd_status(a) -> int:
    try:
        sources = len(json.loads((library() / "library.json").read_text(encoding="utf-8")).get("items", {}))
    except (OSError, ValueError):
        sources = 0
    print(json.dumps({
        "private_environment": "ready" if venv_python().exists() else "made on first use",
        "video_tools": "installed" if has_module("av") and has_module("PIL") else "installed on first use",
        "speech_tool": "installed" if has_module("faster_whisper") else "installed when a video has no captions",
        "library": str(library()),
        "sources_in_library": sources,
    }, indent=1, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Get what a video says and what it shows.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("more", help="more pictures for a video already open, without downloading it again")
    p.add_argument("id")
    p.add_argument("--at", required=True, help="times like 1:23,4:56")
    p = sub.add_parser("open")
    p.add_argument("source")
    p.add_argument("--detail", choices=list(DETAIL), default="normal",
                   help="how many pictures: light (up to 15), normal (up to 40), full (up to 100)")
    p.add_argument("--at", help="also take pictures at these times, like 1:23,4:56")
    p.add_argument("--model", default=DEFAULT_MODEL, help="speech model when there are no captions")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    try:
        return {"open": cmd_open, "more": cmd_more, "status": cmd_status}[a.cmd](a)
    except KeyboardInterrupt:
        log("Stopped.")
        return 130
    except Exception as exc:  # a plain sentence, never a stack trace
        log(f"FAILED: something unexpected went wrong ({type(exc).__name__}: {str(exc)[:300]}).")
        return 2


if __name__ == "__main__":
    sys.exit(main())
