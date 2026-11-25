@echo off
rem Rebuilds DemonsSoulsRecomp.exe after you change code (incremental; SETUP.bat must have run once).
rem   BUILD.bat [--jobs N] [ninja targets...]
rem The build itself is windows\BUILD.bat (the Windows-only files are in windows\); this starts it.
call "%~dp0windows\BUILD.bat" %*
exit /b %errorlevel%
