"""Avvio desktop: parte il server locale e apre il browser. Usato anche come punto d'ingresso dell'EXE."""
import os
import sys
import threading
import webbrowser

# ffmpeg.exe messo accanto all'EXE viene trovato da Whisper
_here = os.path.dirname(os.path.abspath(sys.argv[0]))
os.environ["PATH"] = _here + os.pathsep + os.path.join(_here, "bin") + os.pathsep + os.environ.get("PATH", "")
if sys.platform == "darwin":  # il Mac non ha sempre /opt/homebrew nel PATH quando si apre con doppio clic
    os.environ["PATH"] += os.pathsep + "/opt/homebrew/bin:/usr/local/bin"
os.environ.setdefault("WHISPER_MODEL", "base")

import logging

import uvicorn
from app.main import app


class _NoReset(logging.Filter):
    """Su Windows il browser che chiude una connessione genera 'WinError 10054': è innocuo, lo nascondiamo."""
    def filter(self, record):
        return "10054" not in record.getMessage() and "ConnectionResetError" not in record.getMessage() \
            and not (record.exc_info and isinstance(record.exc_info[1], ConnectionResetError))


def _free_port(start=8000):
    import socket
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            try:
                sk.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def main():
    logging.getLogger("asyncio").addFilter(_NoReset())
    port = _free_port()
    if port != 8000:
        print("ATTENZIONE: la porta 8000 è occupata (probabilmente una copia precedente del programma è ancora aperta).")
        print("Chiudi le altre finestre nere di SirJoe per evitare confusione. Uso la porta", port)
    threading.Timer(2.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"SirJoe · Syncro Song avviato su http://127.0.0.1:{port}  (chiudi questa finestra per fermarlo)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
