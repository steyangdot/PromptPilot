# Compaction-Regime Test — implementation & run guide

Design + rationale: [`docs/COMPACTION_REGIME_TEST.md`](../docs/COMPACTION_REGIME_TEST.md).
**Status: implemented, NOT fired.** Do not run until explicitly cleared.

## Files (all in this worktree)
| File | Role |
|---|---|
| `research/chain_long_fixture.py` | The ~24-turn dependent fixture (`CHAIN_LONG`). |
| `research/chain_test_v2.py` | Registers `chain_long` (select with `--chain long`); reuses the proven runner/arms/quota guard. |
| `research/analyze_compaction_regime.py` | PRIMARY measurement: per-call occupancy, compaction timeline, **marginal in-regime ratio + CI + straddle rule**, cumulative secondary, validity gate. Read-only. |
| `research/judge_continuity.py` | H4 QUALITY: LLM judge over end-state diffs for continuity (the default scorers can't see it). |
| `research/run_compaction_pilot.ps1` | Calibration pilot launcher (N=1). |
| `research/run_compaction_regime.ps1` | Full-run launcher (N=5). |

## Prerequisites (verify before firing)
1. **Run from THIS worktree.** `chain_long` is registered only here, not in `B:\LLM\research`. The launchers `Set-Location` to this repo root for you.
2. **`.env` with `OPENAI_API_KEY`** at this worktree root — worktrees do **not** inherit `B:\LLM\.env`, and `--normalizer slm-openai-v2` loud-fails without it (the documented worktree-.env trap).
3. **Default codex config** — `~/.codex/config.toml` must have **no** `model_context_window` / `model_auto_compact_token_limit` (else #16033 may disable compaction). Confirm `service_tier` is CLI-valid (the desktop app can clobber it). Smoke-test: `'reply OK' | codex exec --skip-git-repo-check`.
4. **Target repo** `C:/projects/httpx` present and clean (the harness resets it per run).
5. **Editable install note:** `import prpt` resolves to `B:\LLM\prpt` (editable); the `research/*` files used are this worktree's. That's the intended setup.

## Fire sequence (when cleared)
```powershell
# 1) Calibration pilot — confirm the fixture crosses ~233k per-call occupancy and fires compaction
powershell -File research/run_compaction_pilot.ps1
python research/analyze_compaction_regime.py
#    -> if occupancy never crosses ~233k or no compaction event: extend/heavy-up
#       chain_long_fixture.py and re-pilot BEFORE the full run.

# 2) Full run (N=5) — only after the pilot passes
powershell -File research/run_compaction_regime.ps1

# 3) Analyze
python research/analyze_compaction_regime.py   # tokens: marginal in-regime ratio + verdict
python research/judge_continuity.py            # quality: H4 continuity (LLM judge)
```
Outputs land in `research/data/chain_results_v2/codex/chain_long/`:
`COMPACTION_ANALYSIS.md` (tokens) and `CONTINUITY_JUDGE.md` (quality).

## Reading the result (design §2.3, §6)
- **PRIMARY = marginal in-regime per-turn ratio** (builtin/with_session over in-regime turns only).
  `>=1.5x` thesis holds · `1.1–1.5x` narrows · `<=1.1x` (incl. prpt costlier) refuted for long sessions.
- **Validity gate:** compaction must fire in **>=4/5** builtin runs, else the result is provisional (apparatus issue / #16033).
- **Straddle rule:** if the 95% CI crosses a threshold, report the band — do not call it.
- **Lead with total tokens** (gross input, cache-inclusive — the same metric `BENCHMARKS.md` publishes); censored/timed-out turns are excluded (matching `aggregate_runs`). Uncached is a warmth range and the prefix cache is busted at the compaction boundary — **do not publish an uncached number from this run.**

## Known deviations from the design doc
- **Block-sequential, not turn-interleaved.** The harness runs all `with_session` runs then all `builtin` runs. This is acceptable because the PRIMARY metric is cache-independent (total tokens). Turn-interleaving would only tighten the *uncached secondary*; if that's ever needed, it requires a new interleaved runner (future work).
- **Quota mitigation:** if the builtin in-regime turns exhaust the ChatGPT window mid-run, the `QuotaExhausted` guard aborts cleanly; re-run after reset. (The `--bare` API alternative from the review is **not** used by default — it changes the window to 1M and would measure a different regime; see design §5.)
