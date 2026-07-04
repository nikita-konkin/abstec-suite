@echo off
setlocal EnableExtensions
rem abstec job watcher for the Windows XP guest (dockur/windows).
rem Polls \\host.lan\Data\jobs for job folders created by run_absoltec.py
rem (--runner dockur) and executes them sequentially. XP-era cmd only:
rem no timeout.exe, no robocopy; ping is used as sleep.

set "SHARE=\\host.lan\Data"
set "DRIVE=W:"
set "WORK=C:\absoltec\work"

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

rem Clear stale running-markers left behind by a guest reboot so those
rem jobs can be picked up again.
for /d %%J in (%DRIVE%\jobs\*) do (
  if exist "%%J\job.running" if not exist "%%J\job.done" del "%%J\job.running" >nul 2>&1
)

rem Sync the application from the shared app folder to the local disk
rem (network-mapped exes are slow and can trip XP zone policy).
if not exist "%WORK%" mkdir "%WORK%"
if exist %DRIVE%\app\absolTEC.exe (
  echo syncing application files...
  xcopy "%DRIVE%\app\*" "%WORK%\" /e /i /d /y >nul
)
if not exist "%WORK%\absolTEC.exe" echo WARNING: absolTEC.exe not found in %WORK%

echo watching %DRIVE%\jobs
:loop
for /d %%J in (%DRIVE%\jobs\*) do (
  if exist "%%J\job.ready" if not exist "%%J\job.done" if not exist "%%J\job.running" call :run_job "%%J"
)
ping -n 3 127.0.0.1 >nul
goto loop

:run_job
set "JOB=%~1"
echo [%date% %time%] starting job %JOB%
>"%JOB%\job.running" echo running
start "abstec-job" /min cmd /c "%JOB%\job.bat"
:wait_job
if exist "%JOB%\job.done" goto job_done
if exist "%JOB%\job.kill" (
  echo [%date% %time%] kill requested for %JOB%
  taskkill /f /im absolTEC.exe >nul 2>&1
  ping -n 3 127.0.0.1 >nul
  if not exist "%JOB%\job.done" >"%JOB%\job.done" echo 124
  goto job_done
)
ping -n 3 127.0.0.1 >nul
goto wait_job
:job_done
echo [%date% %time%] finished job %JOB%
del "%JOB%\job.running" >nul 2>&1
goto :eof
