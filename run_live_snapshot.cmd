@echo off
rem Unit a-23 - the Live page snapshot loop. See docs/runbooks/live-snapshot.md.
rem Output goes to <STORAGE_DIR>\live\live_snapshot.log (--log). One instance only (OS lock).
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" -m jobs.live_snapshot --loop --log
exit /b %ERRORLEVEL%
