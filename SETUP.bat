@echo off
rem Demon's Souls PC port -- one-click setup: drag your disc image (.iso, Redump .zip, or dumped disc
rem folder) onto this file, or double-click it and it will ask. Safe to run again.
rem The setup itself is windows\SETUP.bat (the Windows-only files are in windows\); this starts it.
call "%~dp0windows\SETUP.bat" %*
exit /b %errorlevel%
