@echo off
REM ===========================================================================
REM  ADS-B Airspace Monitor - desktop launcher
REM
REM  Passive 1090 MHz receive. NO TRANSMITTER. Receiving ADS-B is unlicensed in
REM  every jurisdiction, so this is legal to run anywhere, including Morocco.
REM
REM  Default: synthetic demo, so it works with no hardware attached.
REM  For real airspace, plug in an RTL-SDR dongle and use:  launch.bat rtl
REM ===========================================================================

setlocal EnableDelayedExpansion
title ADS-B Airspace Monitor

set "HERE=%~dp0"
cd /d "%HERE%adsb"

REM --- pick an interpreter that has numpy + cv2 + tkinter ---------------------
set "PY="
for %%P in (python.exe) do (
    where %%P >nul 2>nul && set "PY=%%P"
)
if not defined PY (
    echo Python not found on PATH.
    echo Install Python 3.10+ from https://python.org and tick "Add to PATH".
    pause
    exit /b 1
)

REM --- check the one hard dependency -----------------------------------------
"%PY%" -c "import numpy" >nul 2>nul
if errorlevel 1 (
    echo.
    echo numpy is required but not installed.
    echo   "%PY%" -m pip install numpy
    echo.
    pause
    exit /b 1
)

REM --- argument handling ------------------------------------------------------
set "MODE=%~1"
if "%MODE%"=="" goto :demo
if /i "%MODE%"=="demo"      goto :demo
if /i "%MODE%"=="rtl"       goto :rtl
if /i "%MODE%"=="file"      goto :file
if /i "%MODE%"=="tests"     goto :tests
if /i "%MODE%"=="mlat"      goto :mlat
if /i "%MODE%"=="pps"       goto :pps
if /i "%MODE%"=="help"      goto :help
goto :help

:demo
echo Starting ADS-B monitor with SYNTHETIC traffic (no hardware)...
"%PY%" desktop.py --synthetic
goto :end

:rtl
echo.
echo Live receive via RTL-SDR. Requires rtl-sdr installed and a 1090 MHz antenna.
echo.
"%PY%" desktop.py --rtl
goto :end

:file
if "%~2"=="" (
    echo Usage: launch.bat file ^<capture.txt^>
    pause
    exit /b 1
)
"%PY%" desktop.py --file "%~2"
goto :end

:tests
echo.
echo ===== decoder =====
"%PY%" decoder.py
echo.
echo ===== MLAT solver =====
"%PY%" mlat.py --demo
echo.
echo ===== position fusion =====
"%PY%" position_fusion.py
echo.
echo ===== PPS discipline monitor =====
"%PY%" pps_monitor.py --demo
echo.
echo ===== clock requirements =====
"%PY%" mlat.py --clock
pause
goto :end

:mlat
"%PY%" mlat.py --demo
pause
goto :end

:pps
"%PY%" pps_monitor.py --demo
pause
goto :end

:help
echo.
echo   ADS-B Airspace Monitor
echo   ======================
echo.
echo   launch.bat              synthetic demo, no hardware needed
echo   launch.bat rtl          live receive from an RTL-SDR dongle
echo   launch.bat file F.txt   replay a recorded capture
echo   launch.bat tests        run every self-test
echo   launch.bat mlat         MLAT solver validation
echo   launch.bat pps          GPSDO clock discipline monitor
echo.
echo   Hardware: see ..\hardware\A_adsb_receiver.md
echo   MLAT:     see ..\hardware\A_plus_mlat_network.md
echo.
pause
goto :end

:end
endlocal
