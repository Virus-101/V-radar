@echo off
REM Mosquito Zapper desktop app launcher.
REM
REM Runs the app on the synthesised swarm by default so it works with no camera
REM and no sensor head attached. Pass arguments through, e.g.
REM     launch_app.bat --video C:\footage\mosquitoes.mp4
REM     launch_app.bat --camera 1

setlocal
set HERE=%~dp0
cd /d "%HERE%"

REM Prefer the interpreter that has cv2 + numpy + tkinter.
set PY=python
where python >nul 2>nul || set PY=py -3

if "%~1"=="" (
    "%PY%" desktop_app.py --mock --autostart
) else (
    "%PY%" desktop_app.py %*
)
endlocal
