@echo off
rem W07 track C - the weekly CFB refresh, scheduled Tuesday 09:00. See CLAUDE.md.
rem Output, any traceback and the exit code go to <STORAGE_DIR>\cfb\logs\ingest_cfb.log (--log).
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" -m jobs.ingest_cfb --fetch --season 2026 --cfbd-week latest --log
exit /b %ERRORLEVEL%
