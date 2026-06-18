@echo off
REM ---------------------------------------------------------------------------
REM One-shot Task-Scheduler launcher for the CODEX session-isolation experiment
REM (WITH_SESSION vs slm_native vs BUILTIN) — same kill-proof chain as the claude run,
REM parameterised for codex via PROMPTPILOT_ISO_TOOL. Gives GOLD captured end-state for
REM codex too (capture_end_state reads httpx git-diff + pytest, tool-agnostic), unlike the
REM original 2026-06-10 codex run which scored via transcript mining.
REM
REM Register + run:
REM   schtasks /create /tn prpt_codex_isolation /tr "B:\LLM\.worktrees\gate-measure\research\run_codex_isolation.cmd" /sc once /st 23:58 /f
REM   schtasks /run    /tn prpt_codex_isolation
REM NOTE: set codex model_reasoning_effort = "high" in ~/.codex/config.toml to match the
REM 4.47x baseline (restore "xhigh" after). SLM is slm-openai (OpenAI key, off the ChatGPT quota).
REM ---------------------------------------------------------------------------
cd /d "B:\LLM\.worktrees\gate-measure"
set "PYEXE=C:\Users\magicQ\AppData\Local\Programs\Python\Python311\python.exe"
set "OUT=B:\LLM\_session_retest_2026-06-07\codex_isolation"
set "PROMPTPILOT_OUT_DIR=%OUT%"
set "PROMPTPILOT_ISO_TOOL=codex"
set "PROMPTPILOT_ISO_NORMALIZER=slm-openai"
set "CODEX_TIMEOUT_SEC=600"
if not exist "%OUT%" mkdir "%OUT%"
echo [%DATE% %TIME%] task fired: spawning detached codex supervisor >> "%OUT%\runner.log"
"%PYEXE%" research\detach_run.py >> "%OUT%\runner.log" 2>&1
echo [%DATE% %TIME%] launcher exited rc=%ERRORLEVEL% (supervisor continues detached) >> "%OUT%\runner.log"
