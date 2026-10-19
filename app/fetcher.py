"""Scarica l'audio (e, se presenti, i sottotitoli) da un link: YouTube e molti altri siti, tramite yt-dlp."""
import html
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import yt_dlp

from .store import JOBS

MAX_MIN = int(os.getenv("MAX_VIDEO_MIN", "20"))


class _Silent:
    """Logger muto per yt-dlp: gli errori dei sottotitoli (es. 429) non sono fatali e non devono spaventare."""
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def cleanup(max_age: int = 21600):
    if not JOBS.exists():
        return
    now = time.time()
    for d in JOBS.iterdir():
        if d.is_dir() and now - d.stat().st_mtime > max_age:
            shutil.rmtree(d, ignore_errors=True)


def subs_to_text(path: Path) -> str:
    """Trasforma un file VTT/SRT in righe di testo semplici, senza tempi né ripetizioni."""
    out = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.isdigit() or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        line = html.unescape(re.sub(r"<[^>]+>", "", line)).replace("♪", "").strip()
        if not line or re.fullmatch(r"\[.*\]", line):
            continue
        if not out or out[-1] != line:
            out.append(line)
    return "\n".join(out)


def friendly_error(exc, url: str) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "")[-300:]
    hint = ""
    if re.search(r"youtu\.?be", url) and not shutil.which("deno"):
        hint = " Per YouTube serve Deno: apri il prompt, scrivi  winget install DenoLand.Deno  e riavvia l'app."
    return "Download non riuscito: " + msg + hint


def download_file(url: str, kind: str = "mp3"):
    """Scarica audio (mp3) o video (mp4) da un link, per salvarlo su richiesta."""
    if not re.match(r"^https?://", url):
        raise ValueError("Indirizzo non valido.")
    JOBS.mkdir(parents=True, exist_ok=True)
    cleanup()
    d = JOBS / uuid.uuid4().hex[:12]
    d.mkdir()
    opts = {"outtmpl": str(d / "file.%(ext)s"), "noplaylist": True, "quiet": True, "no_warnings": True, "socket_timeout": 30}
    if kind == "mp3":
        opts.update({"format": "bestaudio/best",
                     "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]})
    else:
        opts.update({"format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b", "merge_output_format": "mp4"})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            dur = info.get("duration") or 0
            if dur and dur > MAX_MIN * 60:
                raise ValueError(f"Video troppo lungo (massimo {MAX_MIN} minuti).")
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError(friendly_error(exc, url)) from exc
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    files = [p for p in d.iterdir() if p.name.startswith("file.") and p.suffix.lower() in {".mp3", ".mp4", ".mkv", ".webm", ".m4a"}]
    if not files:
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError("File non creato: controlla che ffmpeg sia installato.")
    return files[0], info.get("title") or "video"


def _grab_clip(url: str, d: Path):
    """Scarica anche il videoclip (max 720p) da usare come sfondo dei video salvati."""
    opts = {"format": "bv*[height<=720]/bv*/b[height<=720]/b", "outtmpl": str(d / "clip.%(ext)s"), "noplaylist": True,
            "quiet": True, "no_warnings": True, "socket_timeout": 30}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception:
        pass


def _grab_subs(url: str, d: Path, language: str):
    """Sottotitoli: tentativo separato e facoltativo. Se YouTube risponde 429 (troppe richieste) si riprova
    una volta dopo una pausa e poi si prosegue senza: l'audio è già scaricato e il testo si può incollare."""
    opts = {"skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
            "subtitleslangs": [language], "subtitlesformat": "vtt/srt/best",
            "outtmpl": str(d / "audio.%(ext)s"), "noplaylist": True, "quiet": True, "no_warnings": True,
            "logger": _Silent(), "ignoreerrors": True, "socket_timeout": 30}
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception:
            pass
        if any(d.glob("audio*.vtt")) or any(d.glob("audio*.srt")):
            return
        time.sleep(4)


def fetch(url: str, language: str = "it", clip: bool = False) -> dict:
    if not re.match(r"^https?://", url):
        raise ValueError("Inserisci un indirizzo che inizi con http:// o https://")
    JOBS.mkdir(parents=True, exist_ok=True)
    cleanup()
    jid = uuid.uuid4().hex[:12]
    d = JOBS / jid
    d.mkdir()
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(d / "audio.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _Silent(),
        "socket_timeout": 30,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            dur = info.get("duration") or 0
            if dur and dur > MAX_MIN * 60:
                raise ValueError(f"Video troppo lungo (massimo {MAX_MIN} minuti).")
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError(friendly_error(exc, url)) from exc
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    if not (d / "audio.wav").exists():
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError("Audio non estratto: controlla che ffmpeg sia installato.")
    if clip:
        _grab_clip(url, d)
    _grab_subs(url, d, language)
    files = sorted(d.glob("audio*.vtt")) + sorted(d.glob("audio*.srt"))
    text = subs_to_text(files[0]) if files else ""
    manual = bool((info.get("subtitles") or {}).get(language))
    return {
        "id": jid,
        "title": info.get("track") or info.get("title") or "",
        "artist": info.get("artist") or info.get("uploader") or info.get("channel") or "",
        "duration": dur,
        "subs": text,
        "subs_auto": bool(text) and not manual,
        "clip": any(d.glob("clip.*")),
    }
