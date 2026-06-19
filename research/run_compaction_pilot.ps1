# =====================================================================
# COMPACTION-REGIME TEST — CALIBRATION PILOT  (docs/COMPACTION_REGIME_TEST.md)
# DO NOT RUN until explicitly cleared.
#
# One run of chain_long on codex: with_session + builtin (native resume).
# Purpose: plot per-call occupancy + compaction timeline to calibrate the turn
# count BEFORE freezing N=5, and confirm prpt stays bounded (H3b).
# Must run from THIS worktree (the chain_long fixture lives here, not in B:\LLM).
# =====================================================================
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot   # research/ -> worktree repo root
Set-Location $repo
Write-Host "[pilot] repo root: $repo"

if (-not (Test-Path (Join-Path $repo ".env"))) {
    Write-Warning ".env not found at $repo\.env"
    Write-Warning "slm-openai-v2 needs OPENAI_API_KEY. Worktrees do NOT inherit .env —"
    Write-Warning "copy it in or set OPENAI_API_KEY, or the harness will loud-fail."
}

# Raise the codex per-turn cap 300 -> 1200s so the pilot calibrates against the SAME
# cap as the full run (run_compaction_regime.ps1 / run_compaction_detached.ps1).
# Otherwise a 300s pilot would censor slow in-regime turns that complete fine at 1200s
# and mis-size the turn count. CODEX_TIMEOUT_SEC is read once at module import, so set
# it before python starts. See docs/COMPACTION_TIMEOUT_FIX_PLAN.md.
$env:CODEX_TIMEOUT_SEC = "1200"
# Calibration: N=1, with_session (default) + builtin; skip no_session.
python research/chain_test_v2.py --chain long --tool codex --runs 1 `
    --skip-no-session --include-builtin --normalizer slm-openai-v2
if ($LASTEXITCODE -ne 0) { Write-Error "[pilot] harness exited $LASTEXITCODE"; exit $LASTEXITCODE }

Write-Host ""
Write-Host "[pilot] complete. Analyze the calibration curves:"
Write-Host "    python research/analyze_compaction_regime.py"
Write-Host "Look for: per-call occupancy crossing ~233k and >=1 compaction event in builtin."
Write-Host "If it never crosses, extend/heavy-up chain_long_fixture.py and re-pilot."
