# Detached runner for the compaction-regime run.
# Launched by a Windows Scheduled Task so it survives Claude session/app resets —
# the prior N=3 run died when the Claude session reset overnight and killed its
# background task. This process is owned by Task Scheduler, not Claude.
$ErrorActionPreference = "Continue"
$root = "B:\LLM\.claude\worktrees\epic-jennings-5e7b22"
Set-Location $root
# Ensure codex (npm global) is on PATH in the scheduled-task environment.
$env:PATH = "C:\Users\magicQ\AppData\Roaming\npm;" + $env:PATH
# Raise the codex per-turn cap 300 -> 1200s so slow-but-completing in-regime turns
# finish under the cap instead of censoring (bi2 T12 ~636s, ws1 T13 ~1099s blew past
# 600s). CODEX_TIMEOUT_SEC is read once at module import, so it MUST be set here in
# the launcher env before python starts (Task Scheduler does not inherit a shell's
# env). Turns that still exceed 1200s are recovered by the post-run reparse pass.
# See docs/COMPACTION_TIMEOUT_FIX_PLAN.md.
$env:CODEX_TIMEOUT_SEC = "1200"
# Clean run target: N>=5 (the censoring fix lands the honest ~5.5x cumulative).
$runs = 5
$py  = "C:\Users\magicQ\AppData\Local\Programs\Python\Python311\python.exe"
$out = "$root\research\data\compaction_n$runs.log"
$err = "$root\research\data\compaction_n$runs.err"
$pa  = "-u research\chain_test_v2.py --chain long --tool codex --runs $runs --skip-no-session --include-builtin --normalizer slm-openai-v2"
$p = Start-Process -FilePath $py -ArgumentList $pa -WorkingDirectory $root `
        -RedirectStandardOutput $out -RedirectStandardError $err -NoNewWindow -PassThru -Wait
"[detached] python exited $($p.ExitCode) at $(Get-Date -Format o)" | Out-File -FilePath $out -Append -Encoding utf8
