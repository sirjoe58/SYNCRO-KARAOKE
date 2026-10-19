@echo off
rem Crea l'EXE: dist\SirJoeSyncroSong\SirJoeSyncroSong.exe  (serve Python 3.10+ e connessione internet)
cd /d "%~dp0"
python -m venv .venv || (echo Installa Python 3.10+ da python.org & pause & exit /b 1)
call .venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onedir --console --name SirJoeSyncroSong ^
  --add-data "static;static" ^
  --collect-data whisper --collect-submodules stable_whisper ^
  --hidden-import tiktoken_ext.openai_public --hidden-import tiktoken_ext ^
  run_app.py
echo.
echo Fatto. Copia ffmpeg.exe dentro dist\SirJoeSyncroSong\ e avvia SirJoeSyncroSong.exe
pause
