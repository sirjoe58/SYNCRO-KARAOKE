"""Esportazione: formati karaoke/testo (LRC, SRT, VTT, ASS, TXT, JSON), audio (MP3, WAV, FLAC) e video (MP4, AVI)."""
import functools
import json
import random
import shutil
import subprocess
from pathlib import Path

from . import music
from .store import find, find_bg, find_clip

STATIC = Path(__file__).resolve().parent.parent / "static"
AUDIO_FMT = {"mp3", "wav", "flac"}
VIDEO_FMT = {"mp4", "avi"}
TEXT_FMT = {"lrc", "lrc-line", "srt", "vtt", "ass", "txt", "json"}
EXT = {"lrc-line": "lrc"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac", "mp4": "video/mp4", "avi": "video/x-msvideo",
        "lrc": "text/plain", "srt": "text/plain", "vtt": "text/vtt", "ass": "text/plain", "txt": "text/plain",
        "json": "application/json"}


def clean_lines(raw):
    """Valida e normalizza le righe ricevute dal browser."""
    out = []
    for ln in raw if isinstance(raw, list) else []:
        try:
            words = [{"w": str(w["w"]), "start": float(w["start"]), "end": float(w["end"])} for w in ln["words"]]
        except (KeyError, TypeError, ValueError):
            continue
        if not words:
            continue
        out.append({"text": " ".join(w["w"] for w in words), "words": words})
    # tempi coerenti: parole sempre in ordine, ognuna con una durata, riga che va dalla prima all'ultima parola
    prev = 0.0
    for ln in out:
        for w in ln["words"]:
            w["start"] = max(w["start"], prev + 0.03) if prev else max(0.0, w["start"])
            w["end"] = max(w["end"], w["start"] + 0.08)
            prev = w["start"]
        ws = ln["words"]
        ln["start"] = ws[0]["start"]
        ln["end"] = max(w["end"] for w in ws)
    return out


def _cs(t):
    return round(max(0.0, t) * 100)


def ts_lrc(t):
    c = _cs(t)
    return f"{c // 6000:02d}:{(c % 6000) / 100:05.2f}"


def ts_srt(t):
    ms = round(max(0.0, t) * 1000)
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_vtt(t):
    return ts_srt(t).replace(",", ".")


def ts_ass(t):
    c = _cs(t)
    h, r = divmod(c, 360000)
    m, r = divmod(r, 6000)
    s, cs = divmod(r, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def to_lrc(lines, title="", artist="", words=True):
    out = []
    if title:
        out.append(f"[ti:{title}]")
    if artist:
        out.append(f"[ar:{artist}]")
    for ln in lines:
        body = " ".join(f"<{ts_lrc(w['start'])}>{w['w']}" for w in ln["words"]) if words else ln["text"]
        out.append(f"[{ts_lrc(ln['start'])}] {body}")
    return "\n".join(out) + "\n"


def _cue_end(lines, i):
    ln = lines[i]
    end = max(ln["end"], ln["start"] + 0.5)
    if i + 1 < len(lines) and lines[i + 1]["start"] > ln["start"] + 0.3:
        end = min(end, lines[i + 1]["start"] - 0.02)
    return end


def to_srt(lines):
    return "\n".join(f"{i + 1}\n{ts_srt(ln['start'])} --> {ts_srt(_cue_end(lines, i))}\n{ln['text']}\n"
                     for i, ln in enumerate(lines))


def to_vtt(lines):
    cues = []
    for i, ln in enumerate(lines):
        body = " ".join((w["w"] if k == 0 else f"<{ts_vtt(w['start'])}>{w['w']}") for k, w in enumerate(ln["words"]))
        cues.append(f"{ts_vtt(ln['start'])} --> {ts_vtt(_cue_end(lines, i))}\n{body}\n")
    return "WEBVTT\n\n" + "\n".join(cues)


def _atxt(s):
    return s.replace("{", "(").replace("}", ")").replace("\\", "")


def _fit(text, base=84, lo=40, width=1180):
    """Dimensione del carattere: la più grande possibile che tiene la riga dentro lo schermo."""
    return max(lo, min(base, int(width / (0.56 * max(1, len(text))))))


def _overlay_events(i, ln, ws, show0, show1, y, w, overlay):
    """Accordi e/o note che accompagnano le parole: stanno sopra la riga e si accendono quando arriva il loro momento."""
    fs = _fit(ln["text"])
    sp = music.text_width(" ", fs)
    left = w / 2 - music.text_width(ln["text"], fs) / 2
    xs, acc = [], 0.0
    for wd in ws:
        ww = music.text_width(wd["w"], fs)
        xs.append((left + acc, ww))
        acc += ww + sp
    out, yy = [], y - 72

    def item(style, text, xc, t, size, col):
        a = max(0, int(round((t - show0) * 1000)))
        half = music.text_width(text, size) / 2
        x = min(max(xc, half + 20), w - half - 20)
        out.append("Dialogue: 1,%s,%s,%s,,0,0,0,,{\\an5\\pos(%d,%d)\\1c&H00FFFFFF&\\alpha&H55&"
                   "\\t(%d,%d,\\1c%s\\alpha&H00&\\fscx118\\fscy118)\\t(%d,%d,\\fscx100\\fscy100)}%s"
                   % (ts_ass(show0), ts_ass(show1), style, int(x), yy, a, a + 120, col, a + 120, a + 320, _atxt(text)))

    if overlay.get("chords"):
        used = set()
        cs = sorted(overlay["chords"])
        act = [c for c in cs if c[0] <= ln["start"] + 0.15]
        todo = ([(ln["start"], act[-1][1])] if act and ln["start"] - act[-1][0] < 15 else []) + \
               [c for c in cs if ln["start"] + 0.15 < c[0] <= ln["end"] + 0.05]  # l'accordo in corso a inizio riga + i cambi durante la riga
        for t, name in todo:
            k = min(range(len(ws)), key=lambda j: abs(ws[j]["start"] - t))
            if k in used:
                continue
            used.add(k)
            x0, ww = xs[k]
            item("Chord", name, x0 + max(ww, music.text_width(name, 46)) / 2, t, 46, "&H0049B9E9&")
    if overlay.get("notes"):
        row = overlay["notes"][i] if i < len(overlay["notes"]) else []
        for k, nm in enumerate(row):
            if nm:
                x0, ww = xs[k]
                item("Note", nm, x0 + ww / 2, ws[k]["start"], 38, "&H00FFE08A&")
    return out


def to_ass(lines, title="", artist="", w=1280, h=720, overlay=None):
    """Sottotitoli ASS karaoke: caratteri grandi, riga corrente + righe successive visibili, conto alla rovescia
    (solo per pause oltre 5 s) e scritta «Syncro By SirJoe»."""
    head = (
        "[Script Info]\nTitle: " + (_atxt(title).replace("\n", " ") or "Karaoke") + "\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Karaoke,Arial,84,&H001A1AFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,5,40,40,40,1\n"
        "Style: Count,Arial,150,&H0049B9E9,&H0049B9E9,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,0,8,0,0,0,1\n"
        "Style: Bar,Arial,20,&H0049B9E9,&H0049B9E9,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        "Style: Chord,Arial,46,&H0049B9E9,&H0049B9E9,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,5,0,0,0,1\n"
        "Style: Note,Arial,38,&H00FFE08A,&H00FFE08A,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,5,0,0,0,1\n"
        "Style: Brand,Arial,34,&H0049B9E9,&H0049B9E9,&H00000000,&H80000000,-1,-1,0,0,100,100,1,0,1,3,1,3,30,30,22,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    n = len(lines)
    hold = [ln["end"] + 0.7 for ln in lines]  # la riga resta visibile un po' dopo l'ultima parola
    show0 = [0.0] * n
    for i, ln in enumerate(lines):  # due righe alternate: la riga i compare appena si libera il suo posto
        if i < 2:
            show0[i] = max(0.0, lines[0]["start"] - 8.0)
        else:
            show0[i] = max(0.0, min(hold[i - 2] + 0.05, ln["start"] - 1.5))
        show0[i] = min(show0[i], max(0.0, ln["start"] - 0.2))
    ev = []
    for i, ln in enumerate(lines):
        show1 = min(hold[i], show0[i + 2]) if i + 2 < n else hold[i]
        show1 = max(show1, ln["end"] + 0.4)  # mai prima che l'ultima parola sia completamente evidenziata
        y = 290 if i % 2 == 0 else 480
        ws = ln["words"]
        parts = ["{\\an5\\pos(%d,%d)\\fs%d}" % (w // 2, y, _fit(ln["text"]))]
        cur = 0  # centesimi già "consumati" dall'inizio della riga: i tempi sono assoluti, senza accumulo di arrotondamenti
        for k, wd in enumerate(ws):
            nxt = ws[k + 1]["start"] if k + 1 < len(ws) else None
            a_cs = _cs(wd["start"] - show0[i])
            if a_cs > cur:
                parts.append("{\\k%d}" % (a_cs - cur))
                cur = a_cs
            e = wd["end"] if nxt is None else min(wd["end"], max(nxt, wd["start"] + 0.05))
            d = max(1, _cs(e - show0[i]) - cur)
            parts.append("{\\kf%d}%s%s" % (d, _atxt(wd["w"]), " " if nxt is not None else ""))
            cur += d
        ev.append("Dialogue: 0,%s,%s,Karaoke,,0,0,0,,%s" % (ts_ass(show0[i]), ts_ass(show1), "".join(parts)))
        if overlay:
            ev.extend(_overlay_events(i, ln, ws, show0[i], show1, y, w, overlay))
    # conto alla rovescia: solo se la pausa strumentale dura più di 5 secondi
    prev_end = 0.0
    for wd in [wd for ln in lines for wd in ln["words"]]:
        if wd["start"] - prev_end > 5:
            t0 = wd["start"] - 5.0
            draw = "\\p1}m 0 0 l 680 0 680 22 0 22{\\p0}"
            ev.append("Dialogue: 0,%s,%s,Bar,,0,0,0,,{\\an7\\pos(300,36)\\1c&H00733A1B&%s" % (ts_ass(t0), ts_ass(wd["start"]), draw))
            ev.append("Dialogue: 1,%s,%s,Bar,,0,0,0,,{\\an7\\pos(300,36)\\clip(300,36,980,58)\\t(0,5000,\\clip(300,36,300,58))%s"
                      % (ts_ass(t0), ts_ass(wd["start"]), draw))
            for k in range(5, 0, -1):
                ev.append("Dialogue: 2,%s,%s,Count,,0,0,0,,{\\an8\\pos(%d,66)}%d"
                          % (ts_ass(wd["start"] - k), ts_ass(wd["start"] - k + 1), w // 2, k))
        prev_end = min(wd["end"], wd["start"] + 6)
    ev.append("Dialogue: 3,0:00:00.00,9:59:59.00,Brand,,0,0,0,,Syncro By SirJoe")
    return head + "\n".join(ev) + "\n"


def _run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg non è riuscito a creare il file: " + (r.stderr or b"").decode(errors="ignore")[-300:])


@functools.lru_cache(maxsize=None)
def _has_filter(name: str) -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(len(p) > 1 and p[1] == name for p in (l.split() for l in r.stdout.splitlines()))


def base_audio(d: Path, vocal: int, out_dir: Path) -> Path:
    """Base per l'esportazione: senza voce se disponibile, con la voce originale al volume scelto."""
    inst, voc, orig = find(d, "instrumental"), find(d, "vocals"), find(d, "audio")
    if inst and voc:
        if vocal <= 0:
            return inst
        mix = out_dir / "mix.wav"
        _run(["ffmpeg", "-y", "-i", str(inst), "-i", str(voc), "-filter_complex",
              f"[1:a]volume={vocal / 100:.2f}[v];[0:a][v]amix=inputs=2:duration=first:dropout_transition=0,volume=2[a]",
              "-map", "[a]", "-c:a", "pcm_s24le", str(mix)])
        return mix
    if orig is None:
        raise RuntimeError("Audio del brano non trovato: importa o sincronizza di nuovo il brano.")
    return orig


PALETTES = [("0x02112e", "0x1a3c78", "0x5b2a86", "0xb8841f"), ("0x0b0b2e", "0x3a1c71", "0xd76d77", "0xffaf7b"),
            ("0x001d3d", "0x00509d", "0x1b9aaa", "0xe9b949"), ("0x120a2a", "0x4a1a6b", "0x1f4e8c", "0xc0392b"),
            ("0x03160f", "0x0b6e4f", "0x08a4bd", "0xf4d35e")]


def _animated_source() -> str:
    """Sfondo animato casuale (gradiente in movimento; in mancanza del filtro, plasma generato con geq)."""
    if _has_filter("gradients"):
        c = random.choice(PALETTES)
        return ("gradients=s=640x360:r=25:c0=%s:c1=%s:c2=%s:c3=%s:nb_colors=4:speed=%.4f:type=%s"
                % (*c, random.uniform(0.006, 0.016), random.choice(["linear", "radial", "circular", "spiral"])))
    p = [random.uniform(0.3, 1.1) for _ in range(4)]
    return ("nullsrc=s=320x180:r=25,format=gbrp,geq=r='127+110*sin(X/%d+T*%.2f)*cos(Y/%d-T*%.2f)':g='127+110*sin(X/%d-T*%.2f)'"
            ":b='127+110*cos(Y/%d+T*%.2f+X/80)'" % (random.randint(25, 60), p[0], random.randint(20, 50), p[1],
                                                    random.randint(30, 70), p[2], random.randint(25, 55), p[3]))


def _video_cmd(src, out_name, bg_mode, clip, vc, meta, res=720, userbg=None):
    """Comando ffmpeg del video. res=480 è molto più veloce (854x480); il testo si adatta da solo."""
    H = 480 if res == 480 else 720
    W = 854 if H == 480 else 1280
    if bg_mode in ("clip", "custom"):
        srcf = clip if bg_mode == "clip" else userbg
        if srcf.suffix.lower() in IMG_EXT:  # immagine fissa: si ripete come video
            bg_in = ["-loop", "1", "-framerate", "25", "-i", str(srcf)]
        else:
            bg_in = ["-stream_loop", "-1", "-i", str(srcf)]
        bg_f = (f"[0:v]fps=25,scale={W}:{H}:force_original_aspect_ratio=increase:flags=fast_bilinear,crop={W}:{H},"
                f"eq=brightness=-0.28:saturation=0.9,setsar=1,drawbox=x=0:y={int(H*0.28)}:w={W}:h={int(H*0.53)}:color=black@0.30:t=fill[bg]")
    elif bg_mode == "anim":
        bg_in = ["-f", "lavfi", "-i", _animated_source()]
        bg_f = f"[0:v]scale={W}:{H}:flags=fast_bilinear,format=yuv420p,eq=brightness=-0.10[bg]"
    else:
        bg_in = ["-f", "lavfi", "-i", f"color=c=0x02112e:s={W}x{H}:r=25"]
        bg_f = "[0:v]null[bg]"
    return ["ffmpeg", "-y", "-threads", "0", *bg_in, "-i", str(src), "-i", str(STATIC / "logo.jpg"),
            "-filter_complex", bg_f + f";[2:v]scale=-1:{int(H*170/720)}[lg];[bg][lg]overlay=W-w-24:18,ass=karaoke.ass:original_size=1280x720[v]",
            "-map", "[v]", "-map", "1:a", "-shortest", *vc, *meta, out_name]


def _overlay_data(d: Path, lines, overlay: str, it: bool):
    """Dati per accordi e/o note sopra il testo; se l'analisi non riesce, il video si fa comunque senza."""
    if overlay not in ("chords", "notes", "both"):
        return None
    ov = {}
    try:
        if overlay in ("chords", "both"):
            cache = d / f"chords_{'it' if it else 'en'}.json"
            if cache.exists():
                ov["chords"] = [tuple(x) for x in json.loads(cache.read_text(encoding="utf-8"))]
            else:
                src = find(d, "instrumental") or find(d, "audio")
                ov["chords"] = music.detect_chords(src, it) if src else []
                try:
                    cache.write_text(json.dumps(ov["chords"]), encoding="utf-8")
                except OSError:
                    pass
        if overlay in ("notes", "both"):
            src = find(d, "vocals") or find(d, "audio")
            ov["notes"] = music.word_notes(src, lines, it) if src else []
    except Exception:
        return ov or None
    return ov


def export(d: Path, fmt: str, lines_raw, title: str = "", artist: str = "", vocal: int = 0,
           bg: str = "auto", out_dir: Path = None, res: int = 720,
           overlay: str = "none", notation: str = "it") -> Path:
    out_dir = out_dir or d
    lines = clean_lines(lines_raw)
    ext = EXT.get(fmt, fmt)
    out = out_dir / f"karaoke.{ext}"
    if fmt in TEXT_FMT:
        if not lines:
            raise ValueError("Non c'è ancora una sincronizzazione da salvare.")
        text = {
            "lrc": lambda: to_lrc(lines, title, artist, True),
            "lrc-line": lambda: to_lrc(lines, title, artist, False),
            "srt": lambda: to_srt(lines),
            "vtt": lambda: to_vtt(lines),
            "ass": lambda: to_ass(lines, title, artist),
            "txt": lambda: "\n".join(ln["text"] for ln in lines) + "\n",
            "json": lambda: json.dumps({"title": title, "artist": artist, "lines": lines}, ensure_ascii=False, indent=2),
        }[fmt]()
        out.write_text(text, encoding="utf-8")
        return out
    if fmt not in AUDIO_FMT | VIDEO_FMT:
        raise ValueError("Formato non supportato.")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg non trovato. Installalo (winget install ffmpeg) oppure copia ffmpeg.exe nella cartella del progetto.")
    src = base_audio(d, max(0, min(100, int(vocal))), out_dir)
    meta = []
    if title:
        meta += ["-metadata", f"title={title}"]
    if artist:
        meta += ["-metadata", f"artist={artist}"]
    if fmt in AUDIO_FMT:  # qualità massima: MP3 320 kbps, WAV 24 bit, FLAC livello 8
        codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3"],
                 "wav": ["-c:a", "pcm_s24le"], "flac": ["-c:a", "flac", "-compression_level", "8"]}[fmt]
        if fmt == "mp3" and lines:
            meta += ["-metadata", "lyrics=" + "\n".join(ln["text"] for ln in lines)]
        _run(["ffmpeg", "-y", "-i", str(src), "-vn", *codec, *meta, str(out)])
        return out
    if not lines:
        raise ValueError("Per il video serve prima la sincronizzazione.")
    ov = _overlay_data(d, lines, overlay, notation == "it")
    (out_dir / "karaoke.ass").write_text(to_ass(lines, title, artist, overlay=ov), encoding="utf-8")
    vc = (["-c:v", "libx264", "-preset", "ultrafast", "-tune", "animation", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
           "-movflags", "+faststart"] if fmt == "mp4" else
          ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p", "-c:a", "libmp3lame", "-b:a", "256k"])
    clip, userbg = find_clip(d), find_bg(d)
    if bg == "custom" and userbg:
        modes = ["custom", "anim"]
    else:
        modes = ["clip", "anim"] if (bg == "auto" and clip) else (["anim"] if bg in ("auto", "anim", "custom") else ["plain"])
    err = None
    for mode in modes:  # se il videoclip non si legge, ripiega sull'animazione casuale
        try:
            _run(_video_cmd(src, out.name, mode, clip, vc, meta, res, userbg), cwd=out_dir)
            return out
        except RuntimeError as exc:
            err = exc
    raise err
