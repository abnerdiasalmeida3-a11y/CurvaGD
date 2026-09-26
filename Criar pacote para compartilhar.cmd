@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\criar_pacote.py
if errorlevel 1 (
  echo Falha ao criar o pacote. Gere o executavel e tente novamente.
  pause
  exit /b 1
)
echo O ZIP esta na pasta bdgd_analisador.
if not "%1"=="--sem-pausa" pause
