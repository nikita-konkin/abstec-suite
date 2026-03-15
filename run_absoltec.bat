@echo off
setlocal

rem ===== Configure these values =====
set "PYTHON_EXE=C:\Python\Python312\python.exe"
set "WORKDIR=%~dp0TayAbsTEC_24.04.17"
set "IN_ROOT=%~dp0in"
set "YEAR=2026"
set "DAY_OF_YEAR=001"
set "SITE=alex0010_dat"
set "ELEVATION_CUTOFF=10"
set "TIME_STEP_HOURS=0.05"
set "CORRECTION_COEFFICIENT=0.97"
set "DRY_RUN=0"
rem ================================

set "DAY_OF_YEAR_PADDED=00%DAY_OF_YEAR%"
set "DAY_OF_YEAR_PADDED=%DAY_OF_YEAR_PADDED:~-3%"
set "DAT_PATH=%IN_ROOT%"
set "STATION_PATH=%IN_ROOT%\%YEAR%\%DAY_OF_YEAR_PADDED%\%SITE%"

if not exist "%DAT_PATH%" (
    echo IN root folder not found: %DAT_PATH%
    pause
    exit /b 1
)

if not exist "%STATION_PATH%" (
    echo Station folder not found: %STATION_PATH%
    pause
    exit /b 1
)

dir /b "%STATION_PATH%\*.dat" >nul 2>&1
if errorlevel 1 (
    echo No .dat files found in: %STATION_PATH%
    pause
    exit /b 1
)

set "PYTHON_CMD=%PYTHON_EXE%"
if not exist "%PYTHON_EXE%" (
    where py >nul 2>&1
    if errorlevel 1 (
        echo Python was not found. Update PYTHON_EXE in this file.
        pause
        exit /b 1
    )
    set "PYTHON_CMD=py -3"
)

set "EXTRA_ARGS="
if "%DRY_RUN%"=="1" set "EXTRA_ARGS=--dry-run"

%PYTHON_CMD% "%~dp0run_absoltec.py" ^
    --workdir "%WORKDIR%" ^
    --dat-path "%DAT_PATH%" ^
    --elevation-cutoff "%ELEVATION_CUTOFF%" ^
    --year "%YEAR%" ^
    --day-of-year "%DAY_OF_YEAR%" ^
    --site "%SITE%" ^
    --time-step-hours "%TIME_STEP_HOURS%" ^
    --correction-coefficient "%CORRECTION_COEFFICIENT%" ^
    %EXTRA_ARGS%

if errorlevel 1 (
    echo.
    echo Failed. Check values above and try again.
    pause
    exit /b 1
)

echo.
echo Completed successfully.
pause








