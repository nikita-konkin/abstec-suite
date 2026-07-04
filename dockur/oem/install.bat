@echo off
rem Runs once by dockur/windows after the XP installation completes (from C:\OEM).
echo Setting up abstec watcher...

if not exist C:\absoltec mkdir C:\absoltec
copy /y C:\OEM\watcher.bat C:\absoltec\watcher.bat

rem Start the watcher at every logon (dockur auto-logs the guest user on).
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v AbstecWatcher /t REG_SZ /d "C:\absoltec\watcher.bat" /f

rem RDP access for debugging: allow blank-password logon, enable Terminal Server,
rem open the XP firewall exception (netsh "firewall" is the XP-era syntax).
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Lsa" /v LimitBlankPasswordUse /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f
netsh firewall set service type=remotedesktop mode=enable

rem Start immediately so the first boot does not need a manual logoff/logon.
start "abstec-watcher" C:\absoltec\watcher.bat
