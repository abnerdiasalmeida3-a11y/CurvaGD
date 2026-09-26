@echo off
rem Abre o CurvaGD direto do codigo-fonte, sem recompilar.
cd /d "%~dp0"
title CurvaGD (codigo-fonte)
".venv\Scripts\python.exe" main.py --workspace "%~dp0workspace"
if errorlevel 1 (
  echo.
  echo O aplicativo terminou com erro. A mensagem acima indica a causa.
  pause
)
