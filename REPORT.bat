@echo off
rem Packs your last play session into one zip for a GitHub issue.   REPORT.bat [perf\<date_time> folder]
rem The report itself is windows\REPORT.bat (the Windows-only files are in windows\); this starts it.
call "%~dp0windows\REPORT.bat" %*
exit /b %errorlevel%
