@echo off
rem a-31 - the Board's cadence. Scheduled every 5 minutes (config.BOARD_TICK_MIN) from the
rem PRODUCTION clone code\prod\calibrated-sports, never a unit's working tree. Each tick reads
rem only the weeks that are due (hourly from Tuesday 12:00 ET, every 15 minutes in the two hours
rem before each kickoff slot, hourly grading while any row is live) and uploads the Board's own
rem tree (BOARD_EXPORT_DIR). The upload deletes nothing. See docs/runbooks/board-cadence.md.
rem Output, any traceback and the exit code go to <STORAGE_DIR>\logs\board_tick.log (--log).
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" -m jobs.board_read --tick --upload --log
exit /b %ERRORLEVEL%
