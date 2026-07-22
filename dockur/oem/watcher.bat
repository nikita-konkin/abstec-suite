@echo off
setlocal EnableExtensions
rem abstec job watcher for the Windows XP guest (dockur/windows).
rem Polls \\host.lan\Data\jobs for job folders created by run_absoltec.py
rem (--runner dockur) and executes them.
rem
rem Jobs run in parallel, one per slot. Each slot has its own workdir and its
rem own copy of the executable (absolTEC_sN.exe) because concurrent runs must
rem not share absolTEC.dia, the result folder, or a "taskkill /im absolTEC.exe".
rem Slot count comes from W:\jobs\_slots.cfg (a single number); edit that file
rem on the host and restart the guest to change it.
rem
rem XP-era cmd only: no timeout.exe, no robocopy; ping is used as sleep.

set "SHARE=\\host.lan\Data"
set "DRIVE=W:"
set "WORK=C:\absoltec\work"
set "DEFAULT_SLOTS=2"
set "MAX_SLOTS=8"

if /i "%~1"=="slot" goto slot_worker

echo abstec watcher starting...

:wait_share
if exist %DRIVE%\jobs goto share_ok
net use %DRIVE% /delete /y >nul 2>&1
net use %DRIVE% %SHARE% /persistent:no >nul 2>&1
if exist %DRIVE%\jobs goto share_ok
echo waiting for share %SHARE% ...
ping -n 6 127.0.0.1 >nul
goto wait_share

:share_ok
echo share %SHARE% mapped to %DRIVE%

rem How many jobs to run at once.
set "SLOTS=%DEFAULT_SLOTS%"
if exist "%DRIVE%\jobs\_slots.cfg" (
  for /f "usebackq tokens=1" %%S in ("%DRIVE%\jobs\_slots.cfg") do set "SLOTS=%%S"
)
rem Non-numeric content evaluates to 0 here, so the range check below rejects it.
set /a SLOTS=SLOTS+0 >nul 2>&1
if %SLOTS% LSS 1 set "SLOTS=%DEFAULT_SLOTS%"
if %SLOTS% GTR %MAX_SLOTS% set "SLOTS=%MAX_SLOTS%"

rem Clear stale markers left behind by a guest reboot so those jobs can be
rem picked up again. A claim without job.done never completed.
for /d %%J in (%DRIVE%\jobs\*) do (
  if not exist "%%J\job.done" (
    if exist "%%J\job.running" del "%%J\job.running" >nul 2>&1
    if exist "%%J\claim" rmdir /s /q "%%J\claim" >nul 2>&1
  )
)

rem Sync the application from the shared app folder to the local disk
rem (network-mapped exes are slow and can trip XP zone policy).
if not exist "%WORK%" mkdir "%WORK%"
if exist %DRIVE%\app\absolTEC.exe (
  echo syncing application files...
  xcopy "%DRIVE%\app\*" "%WORK%\" /e /i /d /y >nul
)
if not exist "%WORK%\absolTEC.exe" echo WARNING: absolTEC.exe not found in %WORK%

for /l %%S in (1,1,%SLOTS%) do call :setup_slot %%S

rem Tell the host how much concurrency it can actually expect.
>"%DRIVE%\jobs\_watcher.status" echo slots=%SLOTS%

for /l %%S in (1,1,%SLOTS%) do start "abstec-slot-%%S" /min cmd /c call "%~f0" slot %%S
echo watching %DRIVE%\jobs with %SLOTS% slot(s)

:master_idle
ping -n 60 127.0.0.1 >nul
goto master_idle

rem ── Per-slot setup: an isolated workdir with its own copy of the exe ────────
:setup_slot
set "N=%~1"
set "SLOTDIR=%WORK%\s%N%"
if not exist "%SLOTDIR%" mkdir "%SLOTDIR%"
rem Copy from the share, not from %WORK%, so the slot folders are never
rem recursively copied into themselves.
xcopy "%DRIVE%\app\*" "%SLOTDIR%\" /e /i /d /y >nul
copy /y "%SLOTDIR%\absolTEC.exe" "%SLOTDIR%\absolTEC_s%N%.exe" >nul
echo slot %N% ready at %SLOTDIR%
goto :eof

rem ── Slot worker ────────────────────────────────────────────────────────────
:slot_worker
set "N=%~2"
set "SLOTDIR=%WORK%\s%N%"
set "SLOTEXE=absolTEC_s%N%.exe"
echo slot %N% watching %DRIVE%\jobs

:slot_loop
for /d %%J in (%DRIVE%\jobs\*) do (
  if exist "%%J\job.ready" if not exist "%%J\job.done" if not exist "%%J\job.running" call :try_job "%%J"
)
ping -n 2 127.0.0.1 >nul
goto slot_loop

:try_job
set "JOB=%~1"
rem Claim the job atomically: mkdir fails when another slot already made it.
mkdir "%JOB%\claim" 2>nul
if errorlevel 1 goto :eof
if not exist "%JOB%\job.ready" goto :eof
if exist "%JOB%\job.done" goto :eof
echo [%date% %time%] slot %N% starting job %JOB%
>"%JOB%\job.running" echo slot %N%
start "abstec-job-%N%" /min cmd /c call "%JOB%\job.bat" "%SLOTDIR%" "%SLOTEXE%"

:wait_job
rem If the host removed the whole job folder (cleanup race), stop waiting
rem instead of polling a nonexistent path forever.
if not exist "%JOB%\" (
  echo [%date% %time%] job folder gone, resuming watch: %JOB%
  goto :eof
)
if exist "%JOB%\job.done" goto job_done
if exist "%JOB%\job.kill" (
  echo [%date% %time%] kill requested for %JOB%
  rem Per-slot image name, so this never kills another slot's run.
  taskkill /f /im %SLOTEXE% >nul 2>&1
  ping -n 3 127.0.0.1 >nul
  if not exist "%JOB%\job.done" >"%JOB%\job.done" echo 124
  goto job_done
)
ping -n 2 127.0.0.1 >nul
goto wait_job

:job_done
echo [%date% %time%] slot %N% finished job %JOB%
del "%JOB%\job.running" >nul 2>&1
goto :eof
