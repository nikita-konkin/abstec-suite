@echo off
rem Runs once by dockur/windows after the XP installation completes (from C:\OEM).
echo Setting up abstec watcher...

if not exist C:\absoltec mkdir C:\absoltec
copy /y C:\OEM\watcher.bat C:\absoltec\watcher.bat

rem Bootstrap launcher: before starting the watcher, pick up a newer copy from
rem the shared jobs folder. That way watcher.bat can be updated from the host
rem (drop it into the jobs directory and reboot the guest) instead of needing a
rem full XP reinstall to re-run this script.
>C:\absoltec\boot.bat echo @echo off
>>C:\absoltec\boot.bat echo net use W: \\host.lan\Data /persistent:no ^>nul 2^>^&1
>>C:\absoltec\boot.bat echo if exist W:\jobs\watcher.bat copy /y W:\jobs\watcher.bat C:\absoltec\watcher.bat ^>nul
>>C:\absoltec\boot.bat echo start "abstec-watcher" C:\absoltec\watcher.bat

rem Start the bootstrap at every logon (dockur auto-logs the guest user on).
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v AbstecWatcher /t REG_SZ /d "C:\absoltec\boot.bat" /f

rem RDP access for debugging: allow blank-password logon, enable Terminal Server,
rem open the XP firewall exception (netsh "firewall" is the XP-era syntax).
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Lsa" /v LimitBlankPasswordUse /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f
netsh firewall set service type=remotedesktop mode=enable

rem Start immediately so the first boot does not need a manual logoff/logon.
start "abstec-watcher" C:\absoltec\boot.bat
