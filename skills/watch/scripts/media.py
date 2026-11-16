#!/usr/bin/env python3
"""media.py: the private tool environment, captions and speech-to-text, shared by the listen and watch skills.

Both skills carry an identical copy. Tools live in ~/.cache/learning-skills (LEARNING_SKILLS_CACHE overrides it),
in a virtual environment made with the newest Python from 3.10 to 3.14, because faster-whisper needs
onnxruntime, which has no wheels yet for the newest Python. Nothing is installed system wide.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from library import log  # noqa: E402

CACHE = Path(os.environ.get("LEARNING_SKILLS_CACHE") or Path.home() / ".cache" / "learning-skills")
VENV = CACHE / "venv"
SPEED_FILE = CACHE / "speed.json"
PY_OK = [(3, 14), (3, 13), (3, 12), (3, 11), (3, 10)]
DEFAULT_MODEL = "small"


def hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} hours" if seconds >= 3600 else f"{max(1, round(seconds / 60))} minutes"


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- private environment

def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def candidate_pythons():
    """Interpreters to try, newest supported first."""
    if os.name == "nt":
        for major, minor in PY_OK:
            yield ["py", f"-{major}.{minor}"]
    else:
        for major, minor in PY_OK:
            yield [f"python{major}.{minor}"]
        yield ["python3"]
    if sys.version_info[:2] in PY_OK:
        yield [sys.executable]


def python_version(cmd) -> tuple | None:
    try:
        r = run([*cmd, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        major, minor = map(int, r.stdout.split())
    except ValueError:
        return None
    return (major, minor) if r.returncode == 0 else None


def pip_install(*pkgs) -> None:
    r = run([venv_python(), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *pkgs])
    if r.returncode:
        raise RuntimeError(f"could not install {' '.join(pkgs)}: {r.stderr.strip()[-400:]}")


def has_module(name: str) -> bool:
    return venv_python().exists() and run([venv_python(), "-c", f"import {name}"]).returncode == 0


def ensure_venv() -> None:
    if not venv_python().exists():
        log("First run: setting up a private environment for the tools (about a minute).")
        CACHE.mkdir(parents=True, exist_ok=True)
        for cand in candidate_pythons():
            if python_version(cand) not in PY_OK:
                continue
            r = run([*cand, "-m", "venv", VENV])
            if r.returncode == 0 and venv_python().exists():
                break
        else:
            have = f"{sys.version_info[0]}.{sys.version_info[1]}"
            raise RuntimeError(
                f"the speech tool needs Python 3.10 to 3.14, and this computer only has {have}. Install Python 3.13 "
                "alongside it (Mac: `brew install python@3.13`, Windows: `winget install -e --id Python.Python.3.13`, "
                "or https://www.python.org/downloads/) and run this again")
    if not (has_module("yt_dlp") and has_module("certifi")):
        # yt-dlp stays at its newest on purpose: YouTube changes often and older versions stop working.
        # [default] brings certifi, so downloads work on a Python that has no certificates of its own.
        pip_install("yt-dlp[default]")


def ensure_speech() -> None:
    ensure_venv()
    if not has_module("faster_whisper"):
        log("Installing the free speech tool once (about 250 MB, a minute or two).")
        # PyAV 16 dropped an argument faster-whisper 1.2.1 still passes, so it stays below 16.
        pip_install("faster-whisper==1.2.1", "av<16")


def ytdlp(*args):
    return run([venv_python(), "-m", "yt_dlp", "--no-warnings", *args])


# ---------------------------------------------------------------- captions and speech

def parse_vtt(text: str) -> list:
    """Caption cues as (start seconds, text), with the rolling repeats of auto captions removed."""
    out, recent, start = [], [], None
    for raw in text.splitlines():
        m = re.match(r"(?:(\d+):)?(\d+):(\d+)[.,]\d+\s*-->", raw)
        if m:
            start = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            continue
        if start is None or not raw.strip() or raw.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        line = " ".join(html.unescape(re.sub(r"<[^>]+>", "", raw)).split())
        if line and line not in recent:
            recent = (recent + [line])[-3:]
            out.append((start, line))
    return out


def captions(url: str, work: Path):
    """Captions in the spoken language, made by people first, then automatic ones. None if there are none."""
    r = ytdlp("--dump-single-json", "--skip-download", "--", url)
    if r.returncode:
        reason = (r.stderr.strip().splitlines() or ["unknown error"])[-1]
        if "not a bot" in reason.lower() or "sign in" in reason.lower():
            reason += (". YouTube wants a sign in, usually after many requests in a row. Wait an hour and run "
                       "the same command again")
        raise RuntimeError(reason)
    info = json.loads(r.stdout)
    lang = (info.get("language") or "en").split("-")[0]
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    for table, code in [(manual, lang), (manual, "en"), (auto, f"{lang}-orig"), (auto, lang), (auto, "en")]:
        if code not in table:
            continue
        flag = "--write-subs" if table is manual else "--write-auto-subs"
        r = ytdlp("--skip-download", flag, "--sub-langs", code, "--sub-format", "vtt",
                  "-o", str(work / "cap.%(ext)s"), "--", url)
        files = list(work.glob("cap*.vtt"))
        if r.returncode == 0 and files:
            rows = parse_vtt(files[0].read_text(encoding="utf-8", errors="replace"))
            if rows:
                return rows, info, ("captions" if table is manual else "automatic captions")
    return None, info, ""


SPEECH = r"""
import json, sys
from faster_whisper import WhisperModel
m = WhisperModel(sys.argv[2], device="cpu", compute_type="int8", cpu_threads=int(sys.argv[3]))
segs, info = m.transcribe(sys.argv[1], vad_filter=True)
print(json.dumps({"duration": info.duration, "language": info.language}), flush=True)
for s in segs:
    print(json.dumps([round(s.start, 1), s.text.strip()]), flush=True)
"""


def cpu_threads() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


def transcribe(path: Path, model: str) -> tuple[list, float | None]:
    ensure_speech()
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(SPEECH)
        script = f.name
    t0, rows, duration, next_report = time.time(), [], None, 600
    try:
        proc = subprocess.Popen([str(venv_python()), script, str(path), model, str(cpu_threads())],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace")
        for line in proc.stdout:
            try:
                val = json.loads(line)
            except ValueError:
                continue
            if isinstance(val, dict):
                duration = val.get("duration")
                if duration and duration > 120:
                    log(f"  {hours(duration)} of sound; {estimate_text(duration, model)}. Progress every 10 minutes of sound.")
                continue
            if val[1]:
                rows.append((val[0], val[1]))
            if val[0] >= next_report and duration:
                log(f"  ...{stamp(val[0])} of {stamp(duration)}")
                next_report += 600
        err = proc.stderr.read()
        if proc.wait():
            raise RuntimeError("transcription failed: " + err.strip()[-400:])
    finally:
        os.unlink(script)
    if duration and duration >= 120:  # a short clip is mostly the model loading, which would skew the speed
        save_speed(duration, time.time() - t0, model)
    return rows, duration


def save_speed(audio: float, took: float, model: str) -> None:
    try:
        speeds = json.loads(SPEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        speeds = {}
    runs = speeds.get(model) if isinstance(speeds.get(model), list) else []
    runs = (runs + [{"audio_seconds": round(audio, 1), "took_seconds": round(took, 1),
                     "when": time.strftime("%Y-%m-%d %H:%M")}])[-5:]
    speeds[model] = runs
    SPEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPEED_FILE.write_text(json.dumps(speeds, indent=1), encoding="utf-8")


def estimate_text(audio_seconds: float, model: str = DEFAULT_MODEL) -> str:
    try:
        runs = json.loads(SPEED_FILE.read_text(encoding="utf-8")).get(model)
    except (OSError, ValueError):
        runs = None
    if isinstance(runs, dict):
        runs = [runs]
    if not runs:
        return f"time on this computer unknown until the first transcription with the {model} model"
    ratios = sorted(r["took_seconds"] / r["audio_seconds"] for r in runs)
    ratio = ratios[len(ratios) // 2]
    return (f"roughly {hours(audio_seconds * ratio)} on this computer (from the last {len(runs)} "
            f"run{'s' if len(runs) != 1 else ''}; slower if something else is busy)")


def ensure_frames() -> None:
    """PyAV reads video and Pillow writes the pictures, for the watch skill."""
    ensure_venv()
    if not (has_module("av") and has_module("PIL")):
        log("Installing the video tools once (about 60 MB, under a minute).")
        # PyAV stays below 16 for faster-whisper 1.2.1, which shares this environment.
        pip_install("av<16", "pillow")
