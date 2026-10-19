"""Analisi musicale leggera (solo numpy): accordi dalla base e nota cantata di ogni parola."""
import math
import subprocess
import unicodedata

import numpy as np

NOTE_IT = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]
NOTE_EN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _pcm(path, sr):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def chord_name(root, minor, it=True):
    n = (NOTE_IT if it else NOTE_EN)[root]
    return n + ("m" if minor else "")


def detect_chords(path, it=True, step=0.35):
    """Accordi maggiori/minori stimati dal profilo delle note (chroma) a finestre di ~0,75 s. Restituisce [(tempo, nome)] ai cambi di accordo."""
    sr = 11025
    x = _pcm(path, sr)
    if x.size < sr * 3:
        return []
    n_fft, hop = 4096, 1024
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (freqs >= 65) & (freqs <= 2000)
    midi = 69 + 12 * np.log2(freqs[sel] / 440.0)
    pc = np.mod(np.round(midi), 12).astype(int)
    nfr = 1 + (x.size - n_fft) // hop
    chroma = np.zeros((nfr, 12), dtype=np.float32)
    for i in range(nfr):
        mag = np.abs(np.fft.rfft(x[i * hop: i * hop + n_fft] * win))[sel]
        mag = np.log1p(10 * mag)
        np.add.at(chroma[i], pc, mag)
    # modelli di triadi
    tmpl, names = [], []
    for root in range(12):
        for minor in (False, True):
            v = np.zeros(12, dtype=np.float32)
            v[root] = 1.0
            v[(root + (3 if minor else 4)) % 12] = 0.8
            v[(root + 7) % 12] = 0.8
            tmpl.append(v / np.linalg.norm(v))
            names.append((root, minor))
    tmpl = np.array(tmpl)
    per = max(1, int(step * sr / hop))
    out, last, energy_ref = [], None, np.percentile(chroma.sum(axis=1), 90) + 1e-9
    labels = []
    for a in range(0, nfr, per):
        c = chroma[a:a + per].mean(axis=0)
        if c.sum() < 0.12 * energy_ref:
            labels.append(None)
            continue
        c = c - c.mean()
        nrm = np.linalg.norm(c)
        if nrm == 0:
            labels.append(None)
            continue
        labels.append(int(np.argmax(tmpl @ (c / nrm))))
    # smussa: ogni finestra prende l'accordo più frequente tra le 5 vicine (evita cambi spuri di breve durata)
    sm = []
    for i in range(len(labels)):
        nb = [l for l in labels[max(0, i - 2): i + 3] if l is not None]
        sm.append(max(set(nb), key=nb.count) if nb and labels[i] is not None else None)
    last, start = None, 0.0
    for i, lab in enumerate(sm):
        if lab is None or lab == last:
            continue
        t = round(i * per * hop / sr, 2)
        if out and t - out[-1][0] < 0.8:  # accordi troppo brevi: si tiene il precedente
            continue
        out.append((t, chord_name(*names[lab], it)))
        last = lab
    return out


def note_name(freq, it=True):
    m = int(round(69 + 12 * math.log2(freq / 440.0)))
    return (NOTE_IT if it else NOTE_EN)[m % 12] + str(m // 12 - 1)


def _f0(frame, sr, fmin=80, fmax=1000):
    """Frequenza fondamentale di un frame con autocorrelazione normalizzata; None se non è voce intonata."""
    frame = frame - frame.mean()
    if np.sqrt((frame ** 2).mean()) < 0.01:
        return None
    n = frame.size
    f = np.fft.rfft(frame, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    lo, hi = int(sr / fmax), min(n - 1, int(sr / fmin))
    if hi <= lo + 2:
        return None
    seg = ac[lo:hi]
    k = int(np.argmax(seg)) + lo
    if ac[k] < 0.45:
        return None
    # interpolazione parabolica
    if 1 <= k < n - 1:
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        d = (a - 2 * b + c)
        if d != 0:
            k = k + 0.5 * (a - c) / d
    return sr / k


def word_notes(vocal_path, lines, it=True):
    """Per ogni parola, la nota cantata (mediana delle altezze nella parte centrale). Lista di liste parallela a lines[i]['words']."""
    sr = 16000
    x = _pcm(vocal_path, sr)
    res = []
    fl = 1024
    for ln in lines:
        row = []
        for w in ln["words"]:
            a, b = w["start"], max(w["end"], w["start"] + 0.12)
            a2, b2 = a + (b - a) * 0.15, b - (b - a) * 0.1
            i0, i1 = int(a2 * sr), int(b2 * sr)
            fs = []
            for i in range(i0, max(i0 + 1, i1 - fl), 256):
                fr = x[i:i + fl]
                if fr.size < fl:
                    break
                f = _f0(fr, sr)
                if f:
                    fs.append(f)
            row.append(note_name(float(np.median(fs)), it) if len(fs) >= 2 else "")
        res.append(row)
    return res


# larghezze approssimate dei caratteri di Arial Bold (millesimi di em), per centrare accordi e note sopra le parole
_W = {**{c: 556 for c in "0123456789"}, " ": 278, "i": 278, "j": 278, "l": 278, "f": 333, "t": 333, "r": 389, "m": 889, "w": 778,
      "a": 556, "b": 611, "c": 556, "d": 611, "e": 556, "g": 611, "h": 611, "k": 556, "n": 611, "o": 611, "p": 611, "q": 611,
      "s": 556, "u": 611, "v": 556, "x": 556, "y": 556, "z": 500,
      "A": 722, "B": 722, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278, "J": 556, "K": 722, "L": 611,
      "M": 833, "N": 722, "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944, "X": 667,
      "Y": 667, "Z": 611, ".": 278, ",": 278, "'": 238, "!": 333, "?": 611, ":": 333, ";": 333, "-": 333, "(": 333, ")": 333}


def text_width(s, fs):
    base = unicodedata.normalize("NFD", s)
    return sum(_W.get(c, 556) for c in base if not unicodedata.combining(c)) * fs / 1000.0
