@echo off
setlocal EnableExtensions
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python nicht gefunden!
    pause
    exit /b 1
)

set "FABLE5_ARGS=%*"
if "%FABLE5_ARGS%"=="" set "FABLE5_ARGS=run"

echo Starte fable-5-hunter (%FABLE5_ARGS%)...
python fable_hunter.py %FABLE5_ARGS%
if errorlevel 1 pause
