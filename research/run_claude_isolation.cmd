@echo off
REM ---------------------------------------------------------------------------
REM Detached one-shot launcher for the claude-code session-isolation experiment
REM (WITH_SESSION vs slm_native vs BUILTIN). Run via Task Scheduler so it lives in
REM its OWN process tree -- NOT inside the Claude Code app's claude.exe tree -- so
REM the orphan reaper can't cascade into the app, and it survives Claude Code
REM restarts / suspension (the operational lesson from HANDOFF).
REM
REM Register + run (no //rl highest -> no elevation error):
REM   schtasks /create /tn prpt_claude_isolation /tr "B:\LLM\.worktrees\gate-measure\research\run_claude_isolation.cmd" /sc once /st 23:59 /f
REM   schtasks /run    /tn prpt_claude_isolation
REM Resume-aware: completed runs are loaded, so re-running continues where it died.
REM ---------------------------------------------------------------------------
cd /d "B:\LLM\.worktrees\gate-measure"
set "PROMPTPILOT_OUT_DIR=B:\LLM\_session_retest_2026-06-07\claude_isolation"
set "CLAUDE_MODEL=claude-opus-4-8"
set "CLAUDE_TIMEOUT_SEC=1800"
set "PYEXE=C:\Users\magicQ\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PROMPTPILOT_OUT_DIR%" mkdir "%PROMPTPILOT_OUT_DIR%"
echo [%DATE% %TIME%] starting claude isolation (opus-4.8, N=5) >> "%PROMPTPILOT_OUT_DIR%\runner.log"
"%PYEXE%" -u research\session_isolation_experiment.py --runs 5 --tool claude-code --normalizer slm-openai >> "%PROMPTPILOT_OUT_DIR%\runner.log" 2>&1
echo [%DATE% %TIME%] exited rc=%ERRORLEVEL% >> "%PROMPTPILOT_OUT_DIR%\runner.log"
