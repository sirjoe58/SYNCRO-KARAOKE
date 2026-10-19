#!/bin/bash
# Avvio da sorgente su Mac (serve Python 3.10-3.12 e ffmpeg: brew install python@3.11 ffmpeg)
cd "$(dirname "$0")"
PY=$(command -v python3.11 || command -v python3.12 || command -v python3.10 || command -v python3)
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
python -c "import uvicorn, torch, stable_whisper, demucs_infer, yt_dlp" 2>/dev/null || { pip install -U pip; pip install torch; pip install -r requirements.txt; }
python run_app.py
