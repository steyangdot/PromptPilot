# Detached runner for the N=2 compaction-regime run.
# Launched by a Windows Scheduled Task (prpt_compaction_n2) so it survives Claude
# session/app resets — the prior N=3 run died when the Claude session reset overnight
# and killed its background task. This process is owned by Task Scheduler, not Claude.
$ErrorActionPreference = "Continue"
$root = "B:\LLM\.claude\worktrees\epic-jennings-5e7b22"
Set-Location $root
# Ensure codex (npm global) is on PATH in the scheduled-task environment.
$env:PATH = "C:\Users\magicQ\AppData\Roaming\npm;" + $env:PATH
$py  = "C:\Users\magicQ\AppData\Local\Programs\Python\Python311\python.exe"
$out = "$root\research\data\compaction_n2.log"
$err = "$root\research\data\compaction_n2.err"
$pa  = "-u research\chain_test_v2.py --chain long --tool codex --runs 2 --skip-no-session --include-builtin --normalizer slm-openai-v2"
$p = Start-Process -FilePath $py -ArgumentList $pa -WorkingDirectory $root `
        -RedirectStandardOutput $out -RedirectStandardError $err -NoNewWindow -PassThru -Wait
"[detached] python exited $($p.ExitCode) at $(Get-Date -Format o)" | Out-File -FilePath $out -Append -Encoding utf8
