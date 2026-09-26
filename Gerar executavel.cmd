@echo off
rem Gera o CurvaGD.exe a partir deste codigo e o coloca na pasta de cima.
setlocal
cd /d "%~dp0"
title Gerando CurvaGD.exe
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath "build\dist" --workpath "build\work" CurvaGD.spec
if errorlevel 1 goto falhou
copy /y "build\dist\CurvaGD.exe" "..\CurvaGD.exe" >nul
if errorlevel 1 goto falhou
rmdir /s /q build
echo.
echo Pronto: CurvaGD.exe atualizado na pasta de cima (ao lado da pasta codigo).
if not "%1"=="--sem-pausa" pause
exit /b 0
:falhou
echo.
echo Falha na compilacao. Confira as mensagens acima.
if not "%1"=="--sem-pausa" pause
exit /b 1
