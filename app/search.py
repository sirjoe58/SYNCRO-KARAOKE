"""Ricerca di video/audio (yt-dlp: YouTube, SoundCloud, canali e pagine) e di testi (LRCLIB)."""
import json
import re
import urllib.parse
import urllib.request

import yt_dlp


def search_media(q: str, source: str = "youtube", n: int = 10):
    q = q.strip()
    if not q:
        raise ValueError("Scrivi cosa cercare.")
    if source == "url" or re.match(r"^https?://", q):
        target = q
    elif source == "soundcloud":
        target = f"scsearch{n}:{q}"
    else:
        target = f"ytsearch{n}:{q}"
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True,
            "playlistend": n, "socket_timeout": 20}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False) or {}
    entries = info.get("entries") or ([info] if info.get("id") else [])
    for _ in range(2):  # canali: scende dentro le schede (Video, ecc.)
        if entries and entries[0] and entries[0].get("entries"):
            entries = [x for e in entries if e for x in (e.get("entries") or [])]
    out = []
    for e in entries:
        if not e:
            continue
        url = e.get("webpage_url") or e.get("url") or ""
        if not url.startswith("http"):
            if e.get("id") and str(e.get("ie_key") or "").lower().startswith("youtube"):
                url = f"https://www.youtube.com/watch?v={e['id']}"
            else:
                continue
        thumb = ""
        if e.get("thumbnails"):
            thumb = (e["thumbnails"][-1] or {}).get("url", "")
        elif e.get("thumbnail"):
            thumb = e["thumbnail"]
        elif "youtube.com/watch" in url and e.get("id"):
            thumb = f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg"
        out.append({"url": url, "title": e.get("title") or url, "thumb": thumb,
                    "channel": e.get("channel") or e.get("uploader") or "", "duration": e.get("duration") or 0})
    return out[:n]


def strip_lrc(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        l = re.sub(r"\[\d+:\d+(?:[.:]\d+)?\]|<\d+:\d+(?:[.:]\d+)?>", "", raw).strip()
        if l and not re.fullmatch(r"\[[a-z]+:.*\]", l, re.I):
            lines.append(l)
    return "\n".join(lines)


def search_lyrics(q: str, n: int = 10):
    q = q.strip()
    if not q:
        raise ValueError("Scrivi cosa cercare.")
    url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": q})
    req = urllib.request.Request(url, headers={"User-Agent": "SirJoeSyncroSongs/1.0 (app locale)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
    except Exception as exc:
        raise RuntimeError(f"Servizio testi non raggiungibile: {exc}") from exc
    out = []
    for d in data if isinstance(data, list) else []:
        synced = d.get("syncedLyrics") or ""
        plain = d.get("plainLyrics") or (strip_lrc(synced) if synced else "")
        if not plain or d.get("instrumental"):
            continue
        out.append({"title": d.get("trackName") or "", "artist": d.get("artistName") or "", "album": d.get("albumName") or "",
                    "duration": d.get("duration") or 0, "plain": plain, "synced": synced})
        if len(out) >= n:
            break
    return out
