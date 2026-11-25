@echo off
rem Starts Demon's Souls (DemonsSoulsLauncher.exe runs this with the choices from its window).
rem The controls and every setting (DES_FPS, DES_NAME, DES_GOD, ...) are described in windows\PLAY.bat,
rem which does the work (the Windows-only files are in windows\); this starts it.
call "%~dp0windows\PLAY.bat" %*
exit /b %errorlevel%
