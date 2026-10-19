"""Importazione di file karaoke già pronti: audio, video, LRC, ASS, SRT, VTT, JSON, KAR/MIDI, CDG e ZIP."""
import html
import json
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

from . import library
from .exporter import clean_lines
from .store import LIB

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".wma", ".aif", ".aiff", ".ape", ".wv", ".mka", ".amr"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".3gp", ".ts", ".vob", ".divx"}
MEDIA_EXT = AUDIO_EXT | VIDEO_EXT
LYRIC_EXT = {".lrc", ".ass", ".ssa", ".srt", ".vtt", ".json", ".kar", ".mid", ".midi"}
KNOWN_EXT = MEDIA_EXT | LYRIC_EXT | {".cdg", ".txt"}

_TS = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_WT = re.compile(r"<(\d+):(\d+(?:[.:]\d+)?)>")
_CUE = re.compile(r"(?:(\d+):)?(\d+):(\d+)[,.](\d+)\s*-->\s*(?:(\d+):)?(\d+):(\d+)[,.](\d+)")
_VWT = re.compile(r"<(?:(\d+):)?(\d+):(\d+)[.,](\d+)>")
_ASS_TOK = re.compile(r"\{[^}]*\}|[^{]+")
_K = re.compile(r"\\k[fo]?(\d+)", re.I)


def _t(m):
    return int(m.group(1)) * 60 + float(m.group(2).replace(":", "."))


def _spread(words, start, end):
    tot = sum(len(w) + 1 for w in words) or 1
    out, t = [], start
    for w in words:
        d = (end - start) * (len(w) + 1) / tot
        out.append({"w": w, "start": round(t, 2), "end": round(t + d, 2)})
        t += d
    return out


def _mk(words):
    return {"text": " ".join(w["w"] for w in words), "start": words[0]["start"], "end": words[-1]["end"], "words": words}


def _est_end(start, text, nxt):
    est = max(0.8, 0.075 * len(text) + 0.4)
    cap = (nxt - start - 0.15) if nxt else est
    return start + max(0.5, min(est, cap))


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", "replace")


def parse_lrc(text):
    entries = []
    for raw in text.splitlines():
        stamps = list(_TS.finditer(raw))
        if not stamps:
            continue
        body = _TS.sub("", raw).strip()
        entries += [(_t(m), body) for m in stamps]
    entries.sort(key=lambda e: e[0])
    lines = []
    for i, (t, body) in enumerate(entries):
        nxt = entries[i + 1][0] if i + 1 < len(entries) else None
        wts = list(_WT.finditer(body))
        if wts:
            words = []
            for k, m in enumerate(wts):
                ws = _t(m)
                after = _t(wts[k + 1]) if k + 1 < len(wts) else None
                chunk = body[m.end(): wts[k + 1].start() if k + 1 < len(wts) else len(body)].strip()
                toks = chunk.split()
                if not toks:
                    continue
                end = after if after is not None else (min(nxt - 0.05, ws + 1.2) if nxt else ws + 1.2)
                words += _spread(toks, ws, max(end, ws + 0.05))
            if words:
                lines.append(_mk(words))
        else:
            toks = body.split()
            if toks:
                lines.append(_mk(_spread(toks, t, _est_end(t, body, nxt))))
    return lines


def _cue_time(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_cues(text):
    """SRT e VTT (anche con i tempi delle singole parole)."""
    lines, last = [], None
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        rows = [r for r in block.split("\n") if r.strip()]
        idx = next((i for i, r in enumerate(rows) if _CUE.search(r)), None)
        if idx is None:
            continue
        m = _CUE.search(rows[idx])
        a, b = _cue_time(*m.group(1, 2, 3, 4)), _cue_time(*m.group(5, 6, 7, 8))
        body = html.unescape(re.sub(r"\{[^}]*\}", "", " ".join(rows[idx + 1:])))
        wts = list(_VWT.finditer(body))
        clean = lambda s: re.sub(r"<[^>]+>", "", s).strip()
        words = []
        if wts:
            marks = [(a, body[:wts[0].start()])] + [
                (_cue_time(*w.group(1, 2, 3, 4)), body[w.end(): wts[k + 1].start() if k + 1 < len(wts) else len(body)])
                for k, w in enumerate(wts)]
            for k, (s, chunk) in enumerate(marks):
                toks = clean(chunk).split()
                e = marks[k + 1][0] if k + 1 < len(marks) else b
                if toks:
                    words += _spread(toks, s, max(e, s + 0.05))
        else:
            toks = clean(body).split()
            if toks:
                words = _spread(toks, a, max(b, a + 0.2))
        if not words:
            continue
        ln = _mk(words)
        if last is not None and ln["text"] == last:  # sottotitoli "a scorrimento" ripetuti
            continue
        last = ln["text"]
        lines.append(ln)
    return lines


def _ass_time(s):
    h, m, sec = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def parse_ass(text):
    fmt, lines = None, []
    for raw in text.splitlines():
        r = raw.strip()
        low = r.lower()
        if low.startswith("format:") and fmt is None and "start" in low and "text" in low:
            fmt = [c.strip().lower() for c in r.split(":", 1)[1].split(",")]
        elif low.startswith("dialogue:"):
            cols = fmt or ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
            parts = r.split(":", 1)[1].lstrip().split(",", len(cols) - 1)
            if len(parts) < len(cols):
                continue
            d = dict(zip(cols, parts))
            if d.get("style", "").strip().lower() in {"count", "bar", "brand"} or "\\p1" in d["text"]:
                continue
            s0, s1 = _ass_time(d["start"]), _ass_time(d["end"])
            ts, td, timed, pieces = 0, 0, False, []
            for tok in _ASS_TOK.findall(d["text"]):
                if tok.startswith("{"):
                    for m in _K.finditer(tok):
                        ts += td
                        td = int(m.group(1))
                        timed = True
                else:
                    txt = tok.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
                    if txt:
                        pieces.append((txt, s0 + ts / 100, s0 + (ts + td) / 100))
            words = []
            if timed:
                cur, prev_space = None, True
                for txt, a, b in pieces:
                    core = txt.strip()
                    if not core:
                        prev_space = True
                        continue
                    toks = core.split()
                    if len(toks) > 1:
                        words += _spread(toks, a, max(b, a + 0.05))
                        cur, prev_space = None, txt[-1].isspace()
                        continue
                    if cur is None or txt[0].isspace() or prev_space:
                        cur = {"w": core, "start": round(a, 2), "end": round(b, 2)}
                        words.append(cur)
                    else:
                        cur["w"] += core
                        cur["end"] = round(b, 2)
                    prev_space = txt[-1].isspace()
            else:
                toks = " ".join(p[0] for p in pieces).split()
                if toks:
                    words = _spread(toks, s0, max(s1, s0 + 0.2))
            if words:
                lines.append(_mk(words))
    lines.sort(key=lambda l: l["start"])
    return lines


def _varlen(b, pos):
    v = 0
    while True:
        c = b[pos]
        pos += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, pos


def parse_midi(data: bytes):
    """Testi e tempi da un file KAR/MIDI (eventi 'lyric' o 'text')."""
    if data[:4] != b"MThd":
        raise ValueError("File MIDI/KAR non valido.")
    hl = struct.unpack(">I", data[4:8])[0]
    _, ntr, div = struct.unpack(">HHH", data[8:14])
    if div & 0x8000:
        raise ValueError("Temporizzazione SMPTE non supportata.")
    pos, tempos, texts, lyr = 8 + hl, [], [], []
    for _ in range(ntr):
        if data[pos:pos + 4] != b"MTrk":
            break
        ln = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        p, end, tick, run = pos + 8, pos + 8 + ln, 0, 0
        pos = end
        while p < end:
            d, p = _varlen(data, p)
            tick += d
            st = data[p]
            if st == 0xFF:
                typ = data[p + 1]
                l, p2 = _varlen(data, p + 2)
                payload = data[p2:p2 + l]
                p = p2 + l
                if typ == 0x51 and l == 3:
                    tempos.append((tick, int.from_bytes(payload, "big")))
                elif typ == 5:
                    lyr.append((tick, payload))
                elif typ == 1:
                    texts.append((tick, payload))
                elif typ == 0x2F:
                    break
            elif st in (0xF0, 0xF7):
                l, p2 = _varlen(data, p + 1)
                p = p2 + l
            else:
                if st & 0x80:
                    run = st
                    p += 1
                p += 1 if (run & 0xF0) in (0xC0, 0xD0) else 2
    tempos = sorted(tempos)
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0, 500000))

    def tsec(tick):
        s, (lt, lu) = 0.0, tempos[0]
        for t, us in tempos[1:]:
            if t >= tick:
                break
            s += (t - lt) * lu / 1e6 / div
            lt, lu = t, us
        return s + (tick - lt) * lu / 1e6 / div

    ev = sorted(lyr or [e for e in texts if not e[1].startswith(b"@")], key=lambda e: e[0])
    lines, cur, prev_trail = [], [], True

    def flush():
        nonlocal cur
        if cur:
            for i, w in enumerate(cur):
                w["end"] = cur[i + 1]["start"] if i + 1 < len(cur) else w["last"] + 0.5
                w["end"] = max(round(w["end"], 2), round(w["start"] + 0.05, 2))
                w["start"] = round(w["start"], 2)
            lines.append(_mk([{"w": w["w"], "start": w["start"], "end": w["end"]} for w in cur]))
        cur = []

    for tick, pl in ev:
        txt = _decode(pl)
        nb = txt[:1] in ("/", "\\")
        if nb:
            txt = txt[1:]
        na = txt.endswith(("\n", "\r"))
        txt = txt.replace("\r", "").replace("\n", "")
        if nb:
            flush()
            prev_trail = True
        core = txt.strip()
        if core:
            s = tsec(tick)
            if not cur or txt[:1] == " " or prev_trail:
                cur.append({"w": core, "start": s, "last": s})
            else:
                cur[-1]["w"] += core
                cur[-1]["last"] = s
        prev_trail = txt[-1:] == " " if core else True
        if na:
            flush()
            prev_trail = True
    flush()
    return lines


def parse_file(path: Path):
    """Restituisce le righe sincronizzate di un file karaoke, in base all'estensione."""
    ext = path.suffix.lower()
    raw = path.read_bytes()
    if ext in (".kar", ".mid", ".midi"):
        lines = parse_midi(raw)
    elif ext == ".lrc":
        lines = parse_lrc(_decode(raw))
    elif ext in (".ass", ".ssa"):
        lines = parse_ass(_decode(raw))
    elif ext in (".srt", ".vtt"):
        lines = parse_cues(_decode(raw))
    elif ext == ".json":
        obj = json.loads(_decode(raw))
        lines = clean_lines(obj.get("lines") if isinstance(obj, dict) else obj)
    else:
        raise ValueError("Formato non riconosciuto.")
    if not lines:
        raise ValueError("Nel file non ci sono righe con i tempi.")
    return lines


def _soundfont():
    cands = [os.getenv("SIRJOE_SOUNDFONT", ""), "/usr/share/sounds/sf2/FluidR3_GM.sf2", "/usr/share/soundfonts/default.sf2"]
    return next((c for c in cands if c and Path(c).exists()), None)


def render_midi(src: Path, dst: Path) -> bool:
    exe, sf = shutil.which("fluidsynth"), _soundfont()
    if not exe or not sf:
        return False
    r = subprocess.run([exe, "-ni", sf, str(src), "-F", str(dst), "-r", "44100"], capture_output=True)
    return r.returncode == 0 and dst.exists()


def _title_artist(stem):
    stem = re.sub(r"^\s*\d{1,3}\s*[-_.]\s*", "", stem).replace("_", " ").strip()
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return t.strip(), a.strip()
    return stem, ""


def _expand_zips(paths, workdir):
    out = []
    for p in paths:
        if p.suffix.lower() != ".zip":
            out.append(p)
            continue
        dest = workdir / f"zip_{uuid.uuid4().hex[:6]}"
        dest.mkdir()
        try:
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    name = Path(info.filename).name
                    if info.is_dir() or Path(name).suffix.lower() not in KNOWN_EXT or info.file_size > 800 * 1024 * 1024:
                        continue
                    target = dest / name
                    with z.open(info) as fi, open(target, "wb") as fo:
                        shutil.copyfileobj(fi, fo)
                    out.append(target)
        except zipfile.BadZipFile:
            continue
    return out



BROWSER_V = {"h264", "vp8", "vp9", "av1"}
BROWSER_A = {"aac", "mp3", "opus", "vorbis", ""}


def _probe(path: Path):
    """(codec video, codec audio) del file, o ('', '') se ffprobe non risponde."""
    def one(sel):
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", sel, "-show_entries", "stream=codec_name",
                                "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
            return (r.stdout.split() or [""])[0].strip().lower()
        except Exception:
            return ""
    return one("v:0"), one("a:0")


def make_playable_video(src: Path, d: Path) -> Path:
    """Prepara il video da riprodurre nel browser: se formato e codec vanno bene lo copia, altrimenti lo converte in MP4 (H.264)."""
    vc, ac = _probe(src)
    ext = src.suffix.lower()
    if ext in (".mp4", ".m4v", ".webm") and vc in BROWSER_V and ac in BROWSER_A:
        dst = d / ("video" + (".mp4" if ext == ".m4v" else ext))
        shutil.copy2(src, dst)
        return dst
    dst = d / "video.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-vf", "scale='min(1280,iw)':-2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not dst.exists():
        raise ValueError("Non riesco a convertire il video per la riproduzione: " + (r.stderr or b"").decode(errors="ignore")[-200:])
    return dst


def render_cdg(cdg: Path, audio: Path, dst: Path) -> bool:
    """Karaoke CDG + audio -> MP4 (ffmpeg legge la grafica CDG). Restituisce False se non riesce."""
    r = subprocess.run(["ffmpeg", "-y", "-i", str(cdg), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "20", "-vf", "scale=960:672:flags=neighbor,format=yuv420p", "-c:a", "aac",
                        "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(dst)], capture_output=True)
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 1000


def import_files(paths, workdir: Path) -> dict:
    """Crea brani in libreria con audio/video e, se ci sono, tempi karaoke o grafica CDG con lo stesso nome."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg non trovato. Installalo (winget install ffmpeg) oppure copia ffmpeg.exe nella cartella del progetto.")
    paths = _expand_zips(paths, workdir)
    groups = {}
    for p in paths:
        if p.suffix.lower() in KNOWN_EXT:
            groups.setdefault(p.stem.lower(), []).append(p)
    tracks, lyrics, texts, warnings = [], [], [], []
    for stem, files in groups.items():
        media = next((f for f in files if f.suffix.lower() in MEDIA_EXT), None)
        cdg = next((f for f in files if f.suffix.lower() == ".cdg"), None)
        lyr_files = [f for f in files if f.suffix.lower() in LYRIC_EXT]
        txt_files = [f for f in files if f.suffix.lower() == ".txt"]
        lines, meta_title, meta_artist, perr = [], "", "", ""
        for f in lyr_files:
            try:
                lines = parse_file(f)
                if f.suffix.lower() == ".json":
                    obj = json.loads(_decode(f.read_bytes()))
                    if isinstance(obj, dict):
                        meta_title, meta_artist = obj.get("title") or "", obj.get("artist") or ""
                break
            except (ValueError, struct.error, IndexError) as exc:
                perr = f"{f.name}: {exc}"
        if media is None and lyr_files and lines:
            rendered = None
            mid = next((f for f in lyr_files if f.suffix.lower() in (".kar", ".mid", ".midi")), None)
            if mid is not None:
                rendered = workdir / f"{mid.stem}_render.wav"
                if not render_midi(mid, rendered):
                    rendered = None
            if rendered is None:
                lyrics.append({"name": files[0].name, "lines": lines})
                continue
            media = rendered
        if media is None:
            if txt_files:
                texts.append({"name": txt_files[0].name, "text": _decode(txt_files[0].read_bytes())})
            elif perr:
                warnings.append(perr)
            else:
                warnings.append(f"{files[0].name}: serve anche un file audio o video con lo stesso nome.")
            continue
        jid = uuid.uuid4().hex[:12]
        d = LIB / jid
        d.mkdir(parents=True)
        try:
            library.to_flac(media, d / "audio.flac")
            is_video = media.suffix.lower() in VIDEO_EXT
            if is_video and lines:  # video + testi sincronizzati: il video fa da sfondo ai karaoke che l'app crea
                shutil.copy2(media, d / f"clip{media.suffix.lower()}")
            elif is_video:  # video karaoke già pronto (testo incorporato): si riproduce così com'è
                make_playable_video(media, d)
            if cdg is not None:
                shutil.copy2(cdg, d / "graphics.cdg")
                if not lines and not is_video and not render_cdg(cdg, media, d / "video.mp4"):
                    warnings.append(f"{cdg.name}: la grafica CDG non si riesce a convertire; importato solo l'audio.")
            title, artist = _title_artist(media.stem.replace("_render", ""))
            kind = "sync" if lines else ("cdg" if cdg is not None else ("video" if is_video else "media"))
            library.write_track(d, {"id": jid, "title": meta_title or title, "artist": meta_artist or artist,
                                    "kind": kind, "lines": lines, "created": time.time()})
            tracks.append(library.summary(d))
        except Exception as exc:
            shutil.rmtree(d, ignore_errors=True)
            warnings.append(f"{media.name}: {exc}")
        if perr and not lines:
            warnings.append(perr)
    return {"tracks": tracks, "lyrics": lyrics, "texts": texts, "warnings": warnings}
