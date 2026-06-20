# Auto-resume for the compaction N=5 NATIVE run after the quota-window refresh.
# Fired ONCE by Scheduled Task `prpt_compaction_resume` (~5:20 AM, just after the 5:17 refresh).
# GUARDED: only resumes if the run actually stalled — never double-fires or re-runs a finished job.
# Resume is free of redone work: run_compaction_detached.ps1 -> chain_test_v2 load_run() skips
# every completed run (with_session 1-5 + any banked builtin runs) and executes only what's left.
$ErrorActionPreference = "Continue"
$root = "B:\LLM\.claude\worktrees\epic-jennings-5e7b22"
$cl   = "$root\research\data\chain_results_v2\codex\chain_long"
$log  = "$root\research\data\compaction_resume.log"
function L($m){ "$((Get-Date).ToString('o'))  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

L "=== resume task fired ==="

# Guard 1: harness already running -> do NOT double-fire (run didn't stall, or already resumed)
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -like '*chain_test_v2.py*' }
if ($running) { L "harness already running (PID $($running.ProcessId)) -> NO ACTION."; exit 0 }

# Guard 2: run already complete
if (Test-Path "$cl\builtin_run5.json") { L "builtin_run5.json present -> run COMPLETE, no action."; exit 0 }

# Re-fix config: comment any UNcommented service_tier / default-service-tier the desktop app re-clobbered.
# (Commented lines start with '#', so the regex skips them -> idempotent. UTF-8 no-BOM preserves encoding.)
$cfg = "C:\Users\magicQ\.codex\config.toml"
try {
  $lines = [System.IO.File]::ReadAllLines($cfg)
  $changed = $false
  for ($i=0; $i -lt $lines.Length; $i++) {
    if ($lines[$i] -match '^\s*(service_tier|default-service-tier)\s*=') {
      $lines[$i] = "# " + $lines[$i] + "  # auto-commented by resume (CLI rejects priority)"
      $changed = $true
    }
  }
  if ($changed) {
    [System.IO.File]::WriteAllLines($cfg, $lines, (New-Object System.Text.UTF8Encoding($false)))
    L "config: re-commented clobbered service_tier line(s)."
  } else { L "config: already clean." }
} catch { L "config re-fix ERROR: $($_.Exception.Message)" }

# Resume the native run (detached launcher sets env + Start-Process python -Wait).
$bdone = (Get-ChildItem "$cl\builtin_run*.json" -ErrorAction SilentlyContinue |
          Where-Object { $_.Name -notlike 'endstate_*' } | Measure-Object).Count
L "resuming: $bdone builtin run(s) already banked; launching detached harness..."
& "$root\research\run_compaction_detached.ps1"
L "=== resume finished (python exited) ==="
