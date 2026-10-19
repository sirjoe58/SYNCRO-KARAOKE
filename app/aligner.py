"""Allineamento automatico testo <-> audio (forced alignment) con Whisper + stable-ts."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import stable_whisper

from .store import find

_models = {}


def get_model(name: str = ""):
    # base = veloce, small = buon compromesso, medium = più preciso ma molto più lento
    name = name or os.getenv("WHISPER_MODEL", "base")
    if name not in {"tiny", "base", "small", "medium", "large-v3"}:
        name = "base"
    if name not in _models:
        _models[name] = stable_whisper.load_model(name)
    return _models[name]


def _find_demucs():
    exe = shutil.which("demucs-infer")
    if not exe:
        for name in ("demucs-infer.exe", "demucs-infer"):
            cand = Path(sys.executable).with_name(name)
            if cand.exists():
                return str(cand)
    return exe


# qualità della base senza voce: (opzioni Demucs, livello)
QUALITY = {
    "standard": (["-n", "htdemucs"], 1),
    "high": (["-n", "htdemucs_ft"], 2),
    "max": (["-n", "htdemucs_ft", "--shifts", "2", "--overlap", "0.5"], 3),
}


def ensure_stems(jobdir: Path, quality: str = "high"):
    """Separa voce e base strumentale con Demucs (demucs-infer) e le conserva nella cartella del lavoro."""
    quality = quality if quality in QUALITY else "high"
    want = QUALITY[quality][1]
    voc, inst = find(jobdir, "vocals"), find(jobdir, "instrumental")
    qf = jobdir / "stems.q"
    have = int(qf.read_text()) if qf.exists() and qf.read_text().strip().isdigit() else 1
    if voc and inst and have >= want:
        return voc, inst
    exe = _find_demucs()
    if not exe:
        raise RuntimeError("Demucs non trovato: avvia l'app con avvia.bat (nella versione EXE la separazione non è inclusa).")
    audio = find(jobdir, "audio")
    if audio is None:
        raise RuntimeError("Audio del brano non trovato.")
    work = jobdir / "_demucs"
    opts = QUALITY[quality][0]
    err = b""
    for extra in ([*opts, "--int24"], opts, []):  # se un'opzione non esiste, ripiega su impostazioni più semplici
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        try:
            subprocess.run([exe, "--two-stems=vocals", *extra, "-o", str(work), str(audio)], check=True, capture_output=True)
            break
        except subprocess.CalledProcessError as exc:
            err = exc.stderr or b""
    else:
        raise RuntimeError("La separazione della voce non è riuscita. " + err.decode(errors="ignore")[-400:])
    v = next(work.rglob("vocals.*"), None)
    i = next(work.rglob("no_vocals.*"), None)
    if not v or not i:
        raise RuntimeError("Demucs non ha prodotto le tracce attese (voce e base).")
    for old in (voc, inst):
        if old:
            old.unlink(missing_ok=True)
    voc, inst = jobdir / ("vocals" + v.suffix), jobdir / ("instrumental" + i.suffix)
    shutil.move(str(v), voc)
    shutil.move(str(i), inst)
    qf.write_text(str(want))
    shutil.rmtree(work, ignore_errors=True)
    return voc, inst


def to_wav(src: Path, dst: Path):
    """Converte qualsiasi audio/video in WAV stereo 44.1 kHz (estrae la traccia audio dei video)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg non trovato. Installalo (winget install ffmpeg) oppure copia ffmpeg.exe nella cartella del progetto, poi riavvia l'app.")
    r = subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100", str(dst)], capture_output=True)
    if r.returncode != 0 or not dst.exists():
        raise ValueError("Non riesco a leggere l'audio di questo file.")


def fmt(t: float) -> str:
    c = round(max(0.0, t) * 100)
    return f"{c // 6000:02d}:{(c % 6000) / 100:05.2f}"


def _line(text, sl):
    """Costruisce una riga con i tempi delle sue parole (sl = parole riconosciute per quella riga)."""
    toks = text.split()
    n = len(toks)
    if not sl or not n:
        return None
    if len(sl) == n:
        ws = [{"w": t, "start": s[1], "end": s[2]} for t, s in zip(toks, sl)]
    else:  # numero di parole diverso: distribuzione uniforme nell'intervallo riconosciuto
        s0, e0 = sl[0][1], sl[-1][2]
        step = (e0 - s0) / n
        ws = [{"w": t, "start": s0 + i * step, "end": s0 + (i + 1) * step} for i, t in enumerate(toks)]
    return {"text": text, "start": ws[0]["start"], "end": ws[-1]["end"], "words": ws}


def build_lines(lines, words):
    """Mappa le parole riconosciute (lista piatta) sulle righe del testo."""
    counts = [len(l.split()) for l in lines]
    total = sum(counts)
    if not total or not words:
        return []
    out, idx = [], 0
    for text, n in zip(lines, counts):
        if len(words) == total:
            sl = words[idx: idx + n]
        else:
            a = round(idx / total * len(words))
            b = max(a + 1, round((idx + n) / total * len(words)))
            sl = words[a:b]
        idx += n
        ln = _line(text, sl)
        if ln:
            out.append(ln)
    return out


def lines_from_result(lines, result):
    """Usa i segmenti (uno per riga, con original_split) quando coincidono; altrimenti la mappa piatta."""
    segs = [[(w.word.strip(), float(w.start), float(w.end)) for w in seg.words if w.word.strip()]
            for seg in result.segments]
    if len(segs) == len(lines):
        out = [ln for ln in (_line(t, sl) for t, sl in zip(lines, segs)) if ln]
        if out:
            return out
    return build_lines(lines, [w for s in segs for w in s])


def refine_with_vocals(lines, vocal_path: Path):
    """Corregge attacchi e fine delle parole con l'energia della sola voce: la prima parola di ogni frase parte
    quando la voce riparte davvero e l'ultima parola prima di una pausa arriva fino alla fine reale del canto."""
    try:
        import numpy as np
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(vocal_path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                           capture_output=True)
        x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
        n = x.size // 160  # frame da 10 ms
        if n < 200:
            return lines
        env = np.sqrt((x[: n * 160].reshape(n, 160) ** 2).mean(axis=1))
        env = np.convolve(env, np.ones(5) / 5, mode="same")
        voiced = env > 0.10 * np.percentile(env, 95)
        cs = np.concatenate([[0], np.cumsum(voiced)])
        onsets = np.array([i for i in range(1, n) if voiced[i] and cs[i] - cs[max(0, i - 50)] == 0], dtype=float) / 100.0

        def near_onset(t, win=1.0):
            if onsets.size == 0:
                return None
            k = int(np.argmin(np.abs(onsets - t)))
            return float(onsets[k]) if abs(onsets[k] - t) <= win else None

        def real_end(start, limit):
            j, last, gap, lim = int(start * 100), None, 0, min(n, int(limit * 100))
            while j < lim:
                if voiced[j]:
                    last, gap = j, 0
                else:
                    gap += 1
                    if gap > 25:  # silenzio > 0,25 s: la parola è finita
                        break
                j += 1
            return None if last is None else (last + 1) / 100 + 0.06

        for ln in lines:  # 1) attacco della prima parola di ogni frase
            ws = ln["words"]
            o = near_onset(ws[0]["start"])
            if o is not None and abs(o - ws[0]["start"]) > 0.05 and (len(ws) < 2 or o < ws[1]["start"] - 0.05):
                ws[0]["start"], ws[0]["end"] = o, max(ws[0]["end"], o + 0.1)
        for li, ln in enumerate(lines):  # 2) fine reale di ogni parola (anche quella prima di una pausa)
            ws = ln["words"]
            for k, w in enumerate(ws):
                if k + 1 < len(ws):
                    limit = ws[k + 1]["start"] - 0.03
                elif li + 1 < len(lines):
                    limit = lines[li + 1]["words"][0]["start"] - 0.05
                else:
                    limit = w["end"] + 3.0
                limit = min(limit, w["start"] + 8.0)
                if limit <= w["start"] + 0.1:
                    continue
                e = real_end(w["start"], limit)
                if e is not None and (e > w["end"] + 0.1 or e < w["end"] - 0.3):
                    w["end"] = max(w["start"] + 0.1, min(e, limit))
        prev = -1.0
        for ln in lines:  # 3) tempi sempre crescenti
            for w in ln["words"]:
                if w["start"] < prev + 0.03:
                    w["start"] = prev + 0.03
                    w["end"] = max(w["end"], w["start"] + 0.05)
                prev = w["start"]
            ln["start"] = ln["words"][0]["start"]
            ln["end"] = max(w["end"] for w in ln["words"])
    except Exception:
        pass
    return lines


def make_lrc(lines, title="", artist=""):
    out = []
    if title:
        out.append(f"[ti:{title}]")
    if artist:
        out.append(f"[ar:{artist}]")
    for ln in lines:
        words = " ".join(f"<{fmt(w['start'])}>{w['w']}" for w in ln["words"])
        out.append(f"[{fmt(ln['start'])}] {words}")
    return "\n".join(out)


def _align(model, src, text, language):
    try:
        return model.align(str(src), text, language=language, original_split=True, max_word_dur=4.0)
    except TypeError:  # versioni di stable-ts con opzioni diverse
        return model.align(str(src), text, language=language)


def align_lyrics(audio_path: Path, lyrics: str, language: str = "it", separate: bool = False,
                 title: str = "", artist: str = "", model: str = "", quality: str = "high"):
    lines = [l.strip() for l in lyrics.splitlines() if l.strip()]
    if not lines:
        raise ValueError("Il testo è vuoto.")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg non trovato. Installalo (comando: winget install ffmpeg) oppure copia ffmpeg.exe nella cartella del progetto, poi riavvia l'app.")
    # Per sincronizzare basta la separazione veloce (htdemucs): la qualità scelta si applica al salvataggio
    # della base senza voce (vedi exporter / ensure_stems), così la sincronia non aspetta separazioni lente.
    src = ensure_stems(audio_path.parent, "standard")[0] if separate else audio_path
    result = _align(get_model(model), src, "\n".join(lines), language)
    out_lines = lines_from_result(lines, result)
    if separate:
        out_lines = refine_with_vocals(out_lines, src)
    for ln in out_lines:
        ln["start"], ln["end"] = round(ln["start"], 2), round(ln["end"], 2)
        for w in ln["words"]:
            w["start"], w["end"] = round(w["start"], 2), round(w["end"], 2)
    return {"title": title, "artist": artist, "lines": out_lines, "lrc": make_lrc(out_lines, title, artist)}
