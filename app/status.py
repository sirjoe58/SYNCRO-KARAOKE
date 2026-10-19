"""Stato dell'app (componenti, modelli, rete) e preparazione all'uso offline."""
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

PREP = {"running": False, "log": [], "error": "", "done": False}
WHISPER_MODELS = ("base", "small", "medium")


def online() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=1.5).close()
        return True
    except OSError:
        return False


def _whisper_cache() -> Path:
    return Path(os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache") / "whisper"


def _demucs_models() -> int:
    try:
        import torch
        d = Path(torch.hub.get_dir()) / "checkpoints"
        return len(list(d.glob("*.th"))) if d.exists() else 0
    except Exception:
        return 0


def status() -> dict:
    from .aligner import _find_demucs
    cache = _whisper_cache()
    return {"online": online(), "ffmpeg": bool(shutil.which("ffmpeg")), "demucs": bool(_find_demucs()),
            "deno": bool(shutil.which("deno")),
            "whisper": {m: (cache / f"{m}.pt").exists() for m in WHISPER_MODELS},
            "demucs_models": _demucs_models(), "prepare": dict(PREP)}


def _run_prepare(models, demucs_levels):
    from .aligner import QUALITY, _find_demucs, get_model
    try:
        for m in models:
            PREP["log"].append(f"Scarico il modello di riconoscimento «{m}»…")
            get_model(m)
            PREP["log"].append(f"Modello «{m}» pronto.")
        exe = _find_demucs()
        for lvl in demucs_levels:
            if not exe:
                PREP["log"].append("Demucs non trovato: salto.")
                break
            PREP["log"].append(f"Scarico il modello per separare la voce ({lvl})…")
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "s.wav"
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "3", str(wav)],
                               capture_output=True, check=True)
                subprocess.run([exe, "--two-stems=vocals", *QUALITY[lvl][0], "-o", tmp + "/o", str(wav)],
                               capture_output=True, check=True)
            PREP["log"].append(f"Separazione voce ({lvl}) pronta.")
        PREP["done"] = True
    except Exception as exc:
        PREP["error"] = f"Preparazione non riuscita: {str(exc)[-200:]}"
    finally:
        PREP["running"] = False


def prepare(models, demucs_levels) -> bool:
    if PREP["running"]:
        return False
    PREP.update({"running": True, "log": [], "error": "", "done": False})
    threading.Thread(target=_run_prepare, args=(models, demucs_levels), daemon=True).start()
    return True
