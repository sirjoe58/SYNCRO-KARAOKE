"""Percorsi e funzioni comuni: lavori temporanei (JOBS) e libreria permanente (LIB)."""
import os
import sys
import re
import tempfile
from pathlib import Path

JOBS = Path(tempfile.gettempdir()) / "sirjoe_jobs"
VERSION = "3.7"


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".write_test"
        t.write_text("ok")
        t.unlink()
        return True
    except Exception:
        return False


def _pick_library() -> Path:
    """Cartella della libreria: accanto al programma; se non è scrivibile (zip aperto, cartella protetta), nella cartella dell'utente."""
    env = os.getenv("SIRJOE_LIBRARY")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent.parent / "library"
    if getattr(sys, "frozen", False):  # versione installabile (EXE / Mac): libreria nella cartella dell'utente
        here = Path.home() / "SirJoe Karaoke Libreria"
    if _writable(here):
        return here
    alt = Path.home() / "SirJoe Karaoke Libreria"
    _writable(alt)
    return alt


LIB = _pick_library()
ID_RE = re.compile(r"[a-f0-9]{12}")


def find(d: Path, stem: str, exts=(".wav", ".flac")):
    """Primo file esistente tra stem.wav / stem.flac (la libreria salva in FLAC per risparmiare spazio)."""
    for e in exts:
        p = d / (stem + e)
        if p.exists():
            return p
    return None


def find_clip(d: Path):
    return next(iter(sorted(d.glob("clip.*"))), None)


def find_video(d: Path):
    """Video già pronto (karaoke con testo incorporato) importato nella libreria."""
    return next(iter(sorted(d.glob("video.*"))), None)


def find_bg(d: Path):
    """Sfondo personale scelto dall'utente per i video salvati."""
    return next(iter(sorted(d.glob("userbg.*"))), None)


def locate(jid: str):
    """Cartella di un lavoro temporaneo o di un brano della libreria."""
    if not ID_RE.fullmatch(jid or ""):
        return None
    for base in (JOBS, LIB):
        d = base / jid
        if d.is_dir():
            return d
    return None
