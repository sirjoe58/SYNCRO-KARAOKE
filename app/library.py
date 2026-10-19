"""Libreria permanente di brani e playlist (funziona anche offline)."""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .store import ID_RE, LIB, find, find_bg, find_clip, find_video, locate

PL_FILE = LIB / "playlists.json"


def _write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def read_track(d: Path) -> dict:
    try:
        return json.loads((d / "track.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_track(d: Path, meta: dict):
    _write_json(d / "track.json", meta)


def summary(d: Path) -> dict:
    t = read_track(d)
    return {"id": d.name, "title": t.get("title") or d.name, "artist": t.get("artist") or "",
            "kind": t.get("kind") or "media", "nlines": len(t.get("lines") or []),
            "stems": bool(find(d, "instrumental")), "clip": bool(find_clip(d)), "cdg": (d / "graphics.cdg").exists(),
            "video": bool(find_video(d)), "bg": bool(find_bg(d))}


def full(jid: str):
    d = locate(jid)
    if d is None or d.parent != LIB:
        return None
    t = read_track(d)
    return {**summary(d), "lines": t.get("lines") or []}


def list_tracks():
    if not LIB.exists():
        return []
    items = []
    for d in LIB.iterdir():
        if d.is_dir() and ID_RE.fullmatch(d.name) and (d / "track.json").exists():
            items.append((read_track(d).get("created", 0), summary(d)))
    return [s for _, s in sorted(items, key=lambda x: x[0])]


def to_flac(src: Path, dst: Path):
    r = subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "flac",
                        "-compression_level", "5", str(dst)], capture_output=True)
    if r.returncode != 0 or not dst.exists():
        raise ValueError("Non riesco a convertire l'audio per la libreria.")


def add_from_job(jid: str, title: str, artist: str, lines: list, kind: str = "sync") -> dict:
    """Copia un lavoro (o aggiorna un brano già in libreria) e salva titolo, artista e tempi."""
    src = locate(jid)
    if src is None:
        raise ValueError("File scaduto: sincronizza di nuovo il brano.")
    LIB.mkdir(parents=True, exist_ok=True)
    if src.parent == LIB:
        dst = src
    else:
        dst = LIB / jid
        dst.mkdir(exist_ok=True)
        for stem in ("audio", "instrumental", "vocals"):
            f = find(src, stem)
            if f and not find(dst, stem, (".flac",)):
                if f.suffix == ".flac":
                    shutil.copy2(f, dst / f.name)
                else:
                    to_flac(f, dst / f"{stem}.flac")
        clip = find_clip(src)
        if clip and not find_clip(dst):
            shutil.copy2(clip, dst / clip.name)
        bgf = find_bg(src)
        if bgf and not find_bg(dst):
            shutil.copy2(bgf, dst / bgf.name)
        if (src / "graphics.cdg").exists():
            shutil.copy2(src / "graphics.cdg", dst / "graphics.cdg")
    old = read_track(dst)
    write_track(dst, {"id": jid, "title": title or old.get("title") or "Senza titolo", "artist": artist,
                      "kind": kind, "lines": lines, "created": old.get("created") or time.time()})
    return summary(dst)


def delete_track(jid: str):
    if not ID_RE.fullmatch(jid):
        return
    shutil.rmtree(LIB / jid, ignore_errors=True)
    pl = read_playlists()
    for name in pl["playlists"]:
        pl["playlists"][name] = [i for i in pl["playlists"][name] if i != jid]
    write_playlists(pl)


def read_playlists() -> dict:
    try:
        obj = json.loads(PL_FILE.read_text(encoding="utf-8"))
        if isinstance(obj.get("playlists"), dict) and obj["playlists"]:
            obj.setdefault("current", next(iter(obj["playlists"])))
            return obj
    except Exception:
        pass
    return {"playlists": {"Playlist 1": []}, "current": "Playlist 1"}


def write_playlists(obj) -> dict:
    pls = {}
    for name, ids in (obj.get("playlists") or {}).items():
        name = re.sub(r"\s+", " ", str(name)).strip()[:60]
        if name:
            pls[name] = [i for i in ids if isinstance(i, str) and ID_RE.fullmatch(i) and (LIB / i / "track.json").exists()]
    if not pls:
        pls = {"Playlist 1": []}
    cur = obj.get("current") if obj.get("current") in pls else next(iter(pls))
    out = {"playlists": pls, "current": cur}
    _write_json(PL_FILE, out)
    return out
