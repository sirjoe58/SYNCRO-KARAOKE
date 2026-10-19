"""Server SirJoe · Syncro Song: brano/video/link/file karaoke + testo -> karaoke sincronizzato, libreria e playlist."""
import json
import os
import re
import shutil
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import importer, library
from . import status as appstatus
from .aligner import align_lyrics, ensure_stems, to_wav
from .exporter import EXT, MIME, clean_lines, export
from .fetcher import cleanup, download_file, fetch
from .search import search_lyrics, search_media
from .store import ID_RE, JOBS, LIB, VERSION, find, find_bg, find_clip, find_video, locate

MAX_MB = int(os.getenv("MAX_MB", "500"))
AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".wma"}
VIDEO = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".3gp"}
MEDIA_TYPES = {".wav": "audio/wav", ".flac": "audio/flac", ".mp4": "video/mp4", ".webm": "video/webm",
               ".mkv": "video/x-matroska", ".mov": "video/quicktime", ".avi": "video/x-msvideo"}

app = FastAPI(title="SirJoe · Syncro Song")


@app.exception_handler(Exception)
async def _any_error(request, exc):
    """Qualsiasi errore imprevisto torna al browser come messaggio leggibile (e resta scritto nella finestra nera)."""
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    return JSONResponse({"detail": f"Errore interno ({type(exc).__name__}): {str(exc)[:300]}"}, status_code=500)


@app.get("/api/selftest")
def api_selftest():
    """Prova guidata di libreria e playlist: scrive un brano di prova, lo mette in una playlist, lo rilegge e lo cancella."""
    steps = []

    def step(name, fn):
        try:
            r = fn()
            steps.append({"step": name, "ok": True, "info": r if isinstance(r, str) else ""})
            return True
        except Exception as exc:
            steps.append({"step": name, "ok": False, "info": f"{type(exc).__name__}: {exc}"})
            return False

    jid = uuid.uuid4().hex[:12]
    d = LIB / jid
    ok = step("Cartella libreria scrivibile: " + str(LIB), lambda: (LIB.mkdir(parents=True, exist_ok=True), (LIB / ".t").write_text("ok"), (LIB / ".t").unlink())[0] or "")
    ok = ok and step("ffmpeg disponibile", lambda: "" if shutil.which("ffmpeg") else (_ for _ in ()).throw(RuntimeError("ffmpeg non trovato")))
    if ok:
        def mk():
            d.mkdir()
            (d / "audio.flac").write_bytes(b"x")
            library.write_track(d, {"id": jid, "title": "Prova automatica", "artist": "", "kind": "media", "lines": [], "created": 0})
        ok = step("Salvataggio di un brano di prova in libreria", mk)
    if ok:
        ok = step("Il brano compare nell'elenco", lambda: "" if any(t["id"] == jid for t in library.list_tracks()) else (_ for _ in ()).throw(RuntimeError("brano non trovato nell'elenco")))
    if ok:
        def pl():
            cur = library.read_playlists()
            cur["playlists"].setdefault("Prova automatica", []).append(jid)
            out = library.write_playlists(cur)
            if jid not in out["playlists"].get("Prova automatica", []):
                raise RuntimeError("il brano non è rimasto nella playlist")
            return ""
        ok = step("Aggiunta alla playlist e rilettura", pl)
    def cleanup_t():
        library.delete_track(jid)
        cur = library.read_playlists()
        cur["playlists"].pop("Prova automatica", None)
        library.write_playlists(cur)
        return ""
    step("Pulizia della prova", cleanup_t)
    return {"ok": all(x["ok"] for x in steps), "version": VERSION, "folder": str(Path(__file__).resolve().parent.parent), "steps": steps}


@app.on_event("startup")
def _warm_model():
    """Carica il modello Whisper in background all'avvio: la prima sincronizzazione non perde tempo."""
    import threading

    def run():
        try:
            from .aligner import get_model
            get_model("")
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def _job(jid: str) -> Path:
    d = locate(jid)
    if d is None or find(d, "audio") is None:
        raise HTTPException(404, "File scaduto: carica di nuovo il brano o riscarica il link.")
    return d


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", (name or "karaoke")).strip() or "karaoke"


def _msg(exc) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "")[-300:]
    if re.search(r"resolve|Name or service|timed out|Connection|unreachable|raggiungibile", msg, re.I):
        return "Non riesco a collegarmi a internet o al sito: controlla la connessione. (" + msg[-120:] + ")"
    return msg


def _save_upload(up: UploadFile, dest: Path):
    size = 0
    with open(dest, "wb") as f:
        while chunk := up.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_MB * 1024 * 1024:
                raise HTTPException(413, f"File troppo grande (max {MAX_MB} MB).")
            f.write(chunk)


# ---------------------------------------------------------------- stato e uso offline
@app.get("/api/diag")
def api_diag():
    """Controllo rapido: versione del server, cartella della libreria e se vi si può scrivere."""
    from .store import _writable
    return {"version": VERSION, "folder": str(Path(__file__).resolve().parent.parent), "library": str(LIB), "writable": _writable(LIB),
            "tracks": len(library.list_tracks()), "ffmpeg": bool(shutil.which("ffmpeg"))}


@app.get("/api/status")
def api_status():
    return appstatus.status()


@app.post("/api/prepare")
def api_prepare(models: str = Form("small"), demucs: str = Form("high")):
    ms = [m for m in models.split(",") if m in appstatus.WHISPER_MODELS]
    lv = [x for x in demucs.split(",") if x in ("standard", "high", "max")]
    return {"started": appstatus.prepare(ms, lv)}


@app.get("/api/prepare/status")
def api_prepare_status():
    return appstatus.PREP


# ---------------------------------------------------------------- ricerca e download
@app.get("/api/search/media")
def api_search_media(q: str, source: str = "youtube"):
    try:
        return search_media(q, source)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(502, "Ricerca non riuscita: " + _msg(exc))


@app.get("/api/search/lyrics")
def api_search_lyrics(q: str):
    try:
        return search_lyrics(q)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, _msg(exc))


@app.post("/api/fetch")
def api_fetch(url: str = Form(...), language: str = Form("it"), clip: bool = Form(False)):
    try:
        return fetch(url.strip(), language, clip)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(500, f"Errore durante il download: {exc}")


@app.post("/api/download")
def api_download(url: str = Form(...), kind: str = Form("mp3")):
    if kind not in {"mp3", "mp4"}:
        raise HTTPException(400, "Formato non valido.")
    try:
        path, title = download_file(url.strip(), kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    return FileResponse(path, media_type=MIME[kind], filename=f"{_safe(title)}.{kind}")


# ---------------------------------------------------------------- media di un lavoro o di un brano in libreria
@app.get("/api/media/{jid}")
@app.get("/api/media/{jid}/{stem}")
def api_media(jid: str, stem: str = "audio"):
    d = _job(jid)
    if stem in ("audio", "vocals", "instrumental"):
        p = find(d, stem)
    elif stem == "clip":
        p = find_clip(d)
    elif stem == "video":
        p = find_video(d)
    elif stem == "bg":
        p = find_bg(d)
    elif stem == "cdg":
        p = d / "graphics.cdg" if (d / "graphics.cdg").exists() else None
    else:
        raise HTTPException(404)
    if p is None:
        raise HTTPException(404, "Traccia non disponibile.")
    return FileResponse(p, media_type=MEDIA_TYPES.get(p.suffix.lower(), "application/octet-stream"))


@app.post("/api/separate")
def api_separate(media_id: str = Form(...), quality: str = Form("high")):
    d = _job(media_id)
    try:
        ensure_stems(d, quality)
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(500, f"Errore durante la separazione: {exc}")
    return {"stems": True}


BG_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".gif", ".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@app.post("/api/background")
def api_background(media_id: str = Form(...), file: UploadFile = File(...)):
    """Sfondo personale (video o immagine) per i video salvati di questo brano."""
    d = _job(media_id)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in BG_EXT:
        raise HTTPException(400, "Formato non supportato per lo sfondo: usa un video (MP4, MOV, MKV, AVI, WEBM…) o un'immagine (JPG, PNG…).")
    for old in d.glob("userbg.*"):
        old.unlink(missing_ok=True)
    _save_upload(file, d / f"userbg{ext}")
    return {"ok": True, "name": Path(file.filename).name, "image": ext in {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}}


@app.post("/api/background/delete")
def api_background_delete(media_id: str = Form(...)):
    d = _job(media_id)
    for old in d.glob("userbg.*"):
        old.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/delete")
def api_delete(media_id: str = Form(...)):
    if ID_RE.fullmatch(media_id):
        shutil.rmtree(JOBS / media_id, ignore_errors=True)  # la libreria si cancella solo da /api/library/delete
    return {"ok": True}


# ---------------------------------------------------------------- sincronizzazione
@app.post("/api/align")
def api_align(
    audio: Optional[UploadFile] = File(None),
    media_id: str = Form(""),
    lyrics: str = Form(...),
    language: str = Form("it"),
    separate: bool = Form(False),
    title: str = Form(""),
    artist: str = Form(""),
    model: str = Form(""),
    quality: str = Form("high"),
):
    if media_id:
        d = _job(media_id)
    elif audio is not None and audio.filename:
        ext = Path(audio.filename).suffix.lower()
        if ext in {".mid", ".midi", ".kar"}:
            raise HTTPException(400, "Il MIDI non contiene la voce: usa una registrazione cantata.")
        if ext not in AUDIO | VIDEO:
            raise HTTPException(400, "Formato non supportato. Usa un file audio o video comune (MP3, WAV, MP4, MKV…).")
        JOBS.mkdir(parents=True, exist_ok=True)
        cleanup()
        d = JOBS / uuid.uuid4().hex[:12]
        d.mkdir()
        try:
            raw = d / f"input{ext}"
            _save_upload(audio, raw)
            to_wav(raw, d / "audio.wav")  # estrae l'audio anche dai video
            if ext in VIDEO:
                raw.rename(d / f"clip{ext}")  # il videoclip serve come sfondo dei video salvati
            else:
                raw.unlink(missing_ok=True)
        except ValueError as exc:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(501, str(exc))
        except HTTPException:
            shutil.rmtree(d, ignore_errors=True)
            raise
    else:
        raise HTTPException(400, "Carica un file oppure scarica l'audio da un link.")
    try:
        res = align_lyrics(find(d, "audio"), lyrics, language, separate, title, artist, model, quality)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(500, f"Errore durante la sincronizzazione: {exc}")
    res["media_id"] = d.name
    res["stems"] = find(d, "instrumental") is not None
    res["clip"] = find_clip(d) is not None
    res["kind"] = "sync"
    return res


# ---------------------------------------------------------------- salvataggio in vari formati
@app.post("/api/export")
def api_export(media_id: str = Form(...), fmt: str = Form(...), lines: str = Form("[]"), title: str = Form(""),
               artist: str = Form(""), vocal: int = Form(0), bg: str = Form("auto"),
               quality: str = Form("standard"), res: int = Form(720),
               overlay: str = Form("none"), notation: str = Form("it")):
    d = _job(media_id)
    # la base in alta qualità serve solo ai file audio: nei video l'audio è AAC e rifare Demucs sarebbe una perdita di tempo
    if fmt in ("mp3", "wav", "flac") and quality in ("high", "max") and find(d, "instrumental"):
        try:  # base senza voce alla qualità richiesta (solo se serve: altrimenti riusa quella già pronta)
            ensure_stems(d, quality)
        except Exception:
            traceback.print_exc()
    try:
        raw = json.loads(lines)
    except ValueError:
        raise HTTPException(400, "Dati di sincronizzazione non validi.")
    out_dir = Path(tempfile.mkdtemp(prefix="sirjoe_exp_"))
    try:
        path = export(d, fmt, raw, title, artist, vocal, bg, out_dir, 480 if res == 480 else 720, overlay, notation)
    except ValueError as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise HTTPException(501, str(exc))
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        traceback.print_exc()
        raise HTTPException(500, f"Errore durante il salvataggio: {exc}")
    ext = EXT.get(fmt, fmt)
    return FileResponse(path, media_type=MIME.get(ext, "application/octet-stream"), filename=f"{_safe(title)}.{ext}",
                        background=BackgroundTask(shutil.rmtree, out_dir, True))


# ---------------------------------------------------------------- libreria e playlist
@app.get("/api/library")
def api_library():
    return library.list_tracks()


@app.get("/api/library/{jid}")
def api_library_track(jid: str):
    t = library.full(jid)
    if t is None:
        raise HTTPException(404, "Brano non trovato nella libreria.")
    return t


@app.post("/api/library/add")
def api_library_add(media_id: str = Form(...), title: str = Form(""), artist: str = Form(""), lines: str = Form("[]")):
    _job(media_id)
    try:
        cl = clean_lines(json.loads(lines))
    except ValueError:
        raise HTTPException(400, "Dati di sincronizzazione non validi.")
    try:
        return library.add_from_job(media_id, title.strip(), artist.strip(), cl, "sync" if cl else "media")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/library/delete")
def api_library_delete(media_id: str = Form(...)):
    library.delete_track(media_id)
    return {"ok": True}


@app.get("/api/playlists")
def api_playlists():
    return library.read_playlists()


@app.post("/api/playlists")
def api_playlists_save(data: str = Form(...)):
    try:
        return library.write_playlists(json.loads(data))
    except ValueError:
        raise HTTPException(400, "Playlist non valide.")


# ---------------------------------------------------------------- importazione di file karaoke già pronti
@app.post("/api/import")
def api_import(files: List[UploadFile] = File(...)):
    work = Path(tempfile.mkdtemp(prefix="sirjoe_imp_"))
    try:
        paths = []
        for up in files:
            name = Path(up.filename or "").name
            if not name:
                continue
            dest = work / name
            if dest.exists():
                dest = work / f"{uuid.uuid4().hex[:4]}_{name}"
            _save_upload(up, dest)
            paths.append(dest)
        if not paths:
            raise HTTPException(400, "Nessun file ricevuto.")
        return importer.import_files(paths, work)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post("/api/parse")
def api_parse(file: UploadFile = File(...)):
    """Legge un file karaoke (LRC, ASS, SRT, VTT, JSON, KAR) e restituisce le righe con i tempi."""
    name = Path(file.filename or "").name
    work = Path(tempfile.mkdtemp(prefix="sirjoe_par_"))
    try:
        dest = work / name
        _save_upload(file, dest)
        if dest.suffix.lower() == ".txt":
            return {"text": importer._decode(dest.read_bytes())}
        return {"lines": importer.parse_file(dest)}
    except (ValueError, IndexError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.middleware("http")
async def _no_cache(request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"  # il browser non deve mostrare una vecchia versione della pagina
    return resp


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "static", html=True), name="static")
