@echo off
setlocal
pushd "%~dp0\.." || exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0repair_claude_code_auth.ps1" -EnablePlannerAfterSuccess %*
if errorlevel 1 (
  echo.
  echo Claude Code auth repair failed. See the error above.
  pause
  popd
  exit /b 1
)
echo.
echo Claude Code auth repair finished.
echo Run with -CheckOnly to inspect status without starting login.
if /I "%~1"=="-CheckOnly" (
  popd
  exit /b 0
)
pause
popd
