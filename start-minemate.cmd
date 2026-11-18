@echo off
setlocal
set "MINEMATE_LAUNCHER_ROOT=%~dp0"
if not exist "%MINEMATE_LAUNCHER_ROOT%agent\.venv\Scripts\pythonw.exe" (
    echo Missing agent\.venv\Scripts\pythonw.exe.
    echo Create agent\.venv and install agent\requirements.txt first. See minecraft-bot\README.md.
    pause
    exit /b 1
)
start "" "%MINEMATE_LAUNCHER_ROOT%agent\.venv\Scripts\pythonw.exe" "%MINEMATE_LAUNCHER_ROOT%launcher.py"
endlocal
