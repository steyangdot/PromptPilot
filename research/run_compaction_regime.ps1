# =====================================================================
# COMPACTION-REGIME TEST — FULL RUN  (docs/COMPACTION_REGIME_TEST.md)
# DO NOT RUN until explicitly cleared, AND only after the pilot confirms the
# fixture crosses ~233k per-call occupancy and fires compaction.
#
# N=5 of chain_long on codex: with_session vs builtin (native resume).
# NOTE: the harness runs arms BLOCK-sequential (all with_session, then all
# builtin), not turn-interleaved. That is fine for the PRIMARY metric (total
# tokens / marginal in-regime ratio are cache-INDEPENDENT). True interleaving
# only matters for the uncached secondary; treat that as a warmth range.
# Must run from THIS worktree (chain_long fixture lives here).
# =====================================================================
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
Write-Host "[full] repo root: $repo"

if (-not (Test-Path (Join-Path $repo ".env"))) {
    Write-Warning ".env not found at $repo\.env — slm-openai-v2 needs OPENAI_API_KEY (worktree .env trap)."
}

# Quota note: builtin in-regime turns are the expensive ones; if you hit the
# ChatGPT limit mid-run the QuotaExhausted guard aborts cleanly (no phantom 0s).
# Re-run after the window resets, or see the design §5 quota mitigations.
#
# Raise the codex per-turn cap 300 -> 1200s so slow-but-completing in-regime turns
# finish under the cap instead of censoring (bi2 T12 ~636s, ws1 T13 ~1099s blew
# past 600s). CODEX_TIMEOUT_SEC is read once at module import, so it MUST be set
# here before python starts. Turns still exceeding 1200s are recovered by the
# post-run reparse pass. See docs/COMPACTION_TIMEOUT_FIX_PLAN.md.
# NOTE: this launcher runs in the FOREGROUND — a Claude session reset would kill
# it. For unattended runs use research/run_compaction_detached.ps1 (Scheduled Task).
$env:CODEX_TIMEOUT_SEC = "1200"
python research/chain_test_v2.py --chain long --tool codex --runs 5 `
    --skip-no-session --include-builtin --normalizer slm-openai-v2
if ($LASTEXITCODE -ne 0) { Write-Error "[full] harness exited $LASTEXITCODE"; exit $LASTEXITCODE }

Write-Host ""
Write-Host "[full] complete. Analyze:"
Write-Host "    python research/analyze_compaction_regime.py    # tokens: marginal in-regime ratio + verdict"
Write-Host "    python research/judge_continuity.py             # quality: H4 continuity (LLM judge)"
Write-Host ""
Write-Host "Optional rewrite-confound spot-check (one in-regime slm_native run, design M-1):"
Write-Host "    python research/chain_test_v2.py --chain long --tool codex --runs 1 ``"
Write-Host "        --skip-no-session --skip-with-session --include-slm-native --normalizer slm-openai-v2"
