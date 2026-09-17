@echo off
rem W07 track C - scheduled Odds API jobs. Arguments pass through to jobs.ingest_cfb:
rem   --odds-forward               every 5 minutes (3 credits per kickoff hour, 45 a CFB week)
rem   --odds-p1 --lock-wait 900    once, Saturday 2026-09-19 09:00 ET (74 credits lifetime)
rem Output, any traceback and the exit code go to <STORAGE_DIR>\cfb\logs\ingest_cfb.log (--log).
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m jobs.ingest_cfb %* --log
exit /b %ERRORLEVEL%
