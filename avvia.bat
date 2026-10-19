@echo off
rem Avvio rapido: sceglie una versione di Python compatibile, installa cio' che manca, poi parte.
cd /d "%~dp0"
set PYV=
for %%V in (3.12 3.11 3.10) do (
  if not defined PYV (
    py -%%V-64 -c "import sys" >nul 2>nul && set PYV=%%V
  )
)
if not defined PYV (
  echo Serve Python 3.12 o 3.11 a 64 bit. Scaricalo da https://www.python.org/downloads/windows/
  echo ^(spunta "Add Python to PATH" durante l'installazione^) e poi rilancia questo file.
  pause
  exit /b 1
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) and sys.maxsize>2**32 else 1)" || rmdir /s /q .venv
)
if not exist ".venv\Scripts\python.exe" py -%PYV%-64 -m venv .venv
call ".venv\Scripts\activate"
python -c "import uvicorn, torch, stable_whisper, demucs_infer, yt_dlp" 2>nul
if errorlevel 1 (
  echo Installo i componenti necessari: ci vorranno alcuni minuti...
  python -m pip install --upgrade pip
  python -m pip install torch --index-url https://download.pytorch.org/whl/cpu || (echo Installazione di PyTorch non riuscita. & pause & exit /b 1)
  python -m pip install -r requirements.txt || (echo Installazione non riuscita: controlla la connessione e riprova. & pause & exit /b 1)
)
python -m pip install -U -q "yt-dlp[default]" >nul 2>nul
where deno >nul 2>nul || echo Nota: per scaricare da YouTube serve Deno ^(winget install DenoLand.Deno^).
python run_app.py
pause
