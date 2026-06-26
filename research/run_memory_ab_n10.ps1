# Detached runner for the with_memory N=10 RETEST (+5 pooled onto the finished 5).
# Launched by a Windows Scheduled Task so it survives Claude session/app resets and
# rides ChatGPT quota-window resets (the process is owned by Task Scheduler, not Claude).
#
# Scope (user decision 2026-06-25): with_memory ONLY, N=10. with_session is SKIPPED and
# left untouched at its finished N=5 (1/5-taxed) bar. The harness resumes with_memory runs
# 1-5 (cached on disk) and runs ONLY 6-10 into the SAME out_dir (research/data/chain_results_v2/
# codex/chain_long), so this is a ~65-turn top-up, not a 260-turn full re-run. Valid pooling:
# codex still 0.140 (same as batch1) and the tax forensic is apply-to-baseline (cache-independent).
#
# Instrumentation (this retest): chain_test_v2 now LOG-captures the exact memory/guard prefix
# injected each turn (prepared["memory_prefix"] -> per-turn record), so the forensic can attribute
# outcome to WHAT was surfaced (closes the "guard text uncapturable in codex --json" gap from N=5).
#
# Pre-flight done before first launch: httpx reset clean @ d764bfc; summary.json (N=5 both arms)
# backed up to summary_n5_both.json (this run regenerates summary.json as memory-only N=10);
# service_tier commented in ~/.codex/config.toml (codex CLI rejects priority/default -> rc=1/0).
# DO NOT open the desktop Codex app during the run (it re-injects service_tier).
$ErrorActionPreference = "Continue"
$root = "B:\LLM\.claude\worktrees\epic-jennings-5e7b22"
Set-Location $root
# codex (npm global) on PATH in the scheduled-task environment.
$env:PATH = "C:\Users\magicQ\AppData\Roaming\npm;" + $env:PATH
# Per-turn cap 1200s (read once at module import) so slow in-regime turns finish instead of
# censoring; turns still over 1200s are recovered by the post-run reparse pass.
$env:CODEX_TIMEOUT_SEC = "1200"
$runs = 10
$py  = "C:\Users\magicQ\AppData\Local\Programs\Python\Python311\python.exe"
$dataDir = Join-Path $root "research\data"
if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Force -Path $dataDir | Out-Null }
$out = "$root\research\data\memory_ab_n10.log"
$err = "$root\research\data\memory_ab_n10.err"
$pa  = "-u research\chain_test_v2.py --chain long --tool codex --runs $runs --skip-no-session --skip-with-session --include-memory --normalizer slm-openai-v2"
$p = Start-Process -FilePath $py -ArgumentList $pa -WorkingDirectory $root `
        -RedirectStandardOutput $out -RedirectStandardError $err -NoNewWindow -PassThru -Wait
"[detached] python exited $($p.ExitCode) at $(Get-Date -Format o)" | Out-File -FilePath $out -Append -Encoding utf8
# Propagate the harness exit code so Task Scheduler's Last Run Result reflects reality
# (ErrorActionPreference=Continue would otherwise report green even on a quota/guard abort —
# the project's phantom-success footgun). $p is null only if Start-Process itself failed.
if ($p) { exit $p.ExitCode } else { exit 1 }
