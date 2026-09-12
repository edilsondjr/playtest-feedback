@echo off
REM One command to (re)create the 15-minute watcher on a clean machine:
REM   tools\install_watch_task.cmd
REM Harmless to re-run: /F overwrites the existing task. See README.md ("The routine").
setlocal
set "TASK=PlaytestFeedbackWatch"
set "ENTRY=%~dp0watch_feedback.cmd"
schtasks /Create /F /TN "%TASK%" /SC MINUTE /MO 15 /ST 00:00 /TR "\"%ENTRY%\""
if errorlevel 1 (
  echo.
  echo FAILED to create the scheduled task. Install it by hand instead:
  echo   Task Scheduler -^> Create Basic Task -^> Daily -^> repeat every 15 minutes
  echo   -^> Start a program: "%ENTRY%"
  exit /b 1
)
echo OK: task "%TASK%" installed, every 15 minutes.
schtasks /Run /TN "%TASK%" >nul 2>&1 && echo OK: first run triggered. Log: "%USERPROFILE%\PlaytestFeedback\watch.log"
endlocal
