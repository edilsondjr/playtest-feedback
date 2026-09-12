@echo off
REM Playtest feedback watcher - entry point for the Windows scheduled task
REM "PlaytestFeedbackWatch" (installed by tools\install_watch_task.cmd).
REM Archives every new report from the destination topic and pings the team on
REM Telegram. Prints nothing when there is nothing new (watchdog style).
setlocal
set "REPO=%~dp0.."
set "LOGDIR=%USERPROFILE%\PlaytestFeedback"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if defined PLAYTEST_PYTHON (set "PY=%PLAYTEST_PYTHON%") else (set "PY=py -3")
%PY% "%REPO%\tools\collect_feedback.py" --notify telegram >> "%LOGDIR%\watch.log" 2>&1
set RC=%ERRORLEVEL%
if not "%RC%"=="0" echo [%DATE% %TIME%] collect_feedback.py exit=%RC% >> "%LOGDIR%\watch.log"
endlocal
