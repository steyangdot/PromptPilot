# Detached runner for the with_memory A/B validation run.
# Launched by a Windows Scheduled Task so it survives Claude session/app resets
# (the process is owned by Task Scheduler, not Claude).
#
# Arms: WITH_SESSION (recency window) vs WITH_MEMORY (ProjectState ledger + refactor
# guard, relevance not recency) on chain_long (compaction regime), N=5, codex.
# This is the clean WITHIN-RUN A/B for the continuity-tax fix
# (docs/SESSION_MEMORY_ARCHITECTURE.md). NO native builtin arm (total tokens are
# cache-independent, so the result is still comparable to the published 9.69x headline).
#
# Pre-flight (2026-06-20): httpx reset to the published baseline d764bfc (clean T1);
# OPENAI_API_KEY present (rewrite v2 + gpt-5.4-nano ledger extractor); codex default
# model gpt-5.5 (gpt-5.4-nano is NOT available on the ChatGPT-auth codex path).
$ErrorActionPreference = "Continue"
$root = "B:\LLM\.claude\worktrees\epic-jennings-5e7b22"
Set-Location $root
# Ensure codex (npm global) is on PATH in the scheduled-task environment.
$env:PATH = "C:\Users\magicQ\AppData\Roaming\npm;" + $env:PATH
# Raise the codex per-turn cap 300 -> 1200s so slow-but-completing in-regime turns
# finish under the cap instead of censoring. CODEX_TIMEOUT_SEC is read once at module
# import, so it MUST be set here before python starts (Task Scheduler does not inherit
# a shell's env). Turns still exceeding 1200s are recovered by the post-run reparse pass.
$env:CODEX_TIMEOUT_SEC = "1200"
$runs = 5
$py  = "C:\Users\magicQ\AppData\Local\Programs\Python\Python311\python.exe"
$out = "$root\research\data\memory_ab_n$runs.log"
$err = "$root\research\data\memory_ab_n$runs.err"
$pa  = "-u research\chain_test_v2.py --chain long --tool codex --runs $runs --skip-no-session --include-memory --normalizer slm-openai-v2"
$p = Start-Process -FilePath $py -ArgumentList $pa -WorkingDirectory $root `
        -RedirectStandardOutput $out -RedirectStandardError $err -NoNewWindow -PassThru -Wait
"[detached] python exited $($p.ExitCode) at $(Get-Date -Format o)" | Out-File -FilePath $out -Append -Encoding utf8
