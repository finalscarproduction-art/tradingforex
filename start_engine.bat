@echo off
REM SMC Trading Engine — auto-start launcher
REM This batch file is invoked by Windows Task Scheduler at login.

cd /d "%USERPROFILE%\Desktop\claude\tradingview\trading-engine-v3"

REM Use Python 3.13 from user's local install
set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
set PYTHONUTF8=1

REM Run the engine — output goes to engine.log via Python logging
"%PYTHON_EXE%" main.py
