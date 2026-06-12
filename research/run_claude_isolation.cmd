@echo off
REM ---------------------------------------------------------------------------
REM One-shot Task-Scheduler launcher for the claude session-isolation experiment.
REM
REM Launch chain + why each layer exists (all three deaths of 2026-06-11 mapped):
REM   schtasks -> THIS .cmd          : runs under svchost, OUTSIDE Claude Code's process
REM                                    tree AND job objects (death #1 reaper-cascade and
REM                                    death #3 job-teardown immune)
REM   .cmd -> detach_run.py          : spawns the supervisor DETACHED + CONSOLE-LESS
REM                                    (death #2 CTRL_CLOSE immune; the .cmd's own hidden
REM                                    console exits immediately afterward)
REM   detach_run -> supervise_isolation.py : restart-on-death loop (unknown killers self-heal)
REM   supervisor -> session_isolation_experiment.py : resume-aware runner (saved runs skipped)
REM   PYTHONIOENCODING=utf-8 (set in detach_run) : prevents the chain_test_v2 stdout rewrap
REM                                    from discarding -u, so logs are live and survive kills.
REM
REM Register + run:
REM   schtasks /create /tn prpt_claude_isolation /tr "B:\LLM\.worktrees\gate-measure\research\run_claude_isolation.cmd" /sc once /st 23:59 /f
REM   schtasks /run    /tn prpt_claude_isolation
REM ---------------------------------------------------------------------------
cd /d "B:\LLM\.worktrees\gate-measure"
set "PYEXE=C:\Users\magicQ\AppData\Local\Programs\Python\Python311\python.exe"
set "OUT=B:\LLM\_session_retest_2026-06-07\claude_isolation"
if not exist "%OUT%" mkdir "%OUT%"
echo [%DATE% %TIME%] task fired: spawning detached supervisor >> "%OUT%\runner.log"
"%PYEXE%" research\detach_run.py >> "%OUT%\runner.log" 2>&1
echo [%DATE% %TIME%] launcher exited rc=%ERRORLEVEL% (supervisor continues detached) >> "%OUT%\runner.log"
