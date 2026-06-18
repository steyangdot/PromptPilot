# Review — Compaction-Regime Test Design

**Reviewer:** Claude (Opus 4.8). **Date:** 2026-06-17.
**Document under review:** [COMPACTION_REGIME_TEST.md](COMPACTION_REGIME_TEST.md) (Status: Design only, not yet run).
**Verdict:** Strong, disciplined design — pre-registration, total-tokens-as-lead, rollout-JSONL instrumentation, and #16033/desktop-config awareness show the project's measurement scars are internalized. But **two conceptual holes could make an expensive run answer the wrong question**, and the H4 quality instrument as specified probably can't detect the finding it pre-registers. Fix the five "before running" items below first.

---

## What's good (keep)
- The "decisive open question" framing is correct: every prior result is sub-threshold; the target deployment is above-threshold. This genuinely is the untested regime.
- Pre-registered hypotheses **and** decision thresholds — directly responsive to the prior N=3-noise lesson.
- Lead with TOTAL tokens, uncached as a warmth range — correctly internalizes the methodology learnings.
- Instrument from rollout JSONL, not the harness — sidesteps the timeout-parse bug that inflated earlier numbers.
- Compaction-event detection by event `type`, not raw-text substring — avoids false positives.
- Config hygiene: no window overrides, #16033 awareness, desktop-config gotcha, quota guard.
- Honest "narrow the pitch" branch — willing to lose.
- Adaptive stop condition (extend until ≥2 compaction events) — robust instinct (but see Critical #1 on sequencing).

---

## Critical — fix before spending quota

### C1. Targeting the wrong quantity for "crosses the threshold"
Compaction fires on per-**call** context occupancy, not cumulative summed input. §3 itself draws the line: cumulative input is 304k→1,438k, but the per-call max is **60,316 — "far under the window," hence zero compaction events.** Yet §4.1 lever 1 reasons in cumulative terms ("~20 turns ≈ ~240k accumulated → crosses the threshold"). 240k accumulated across calls does **not** put any single call near 233k.

**Risk:** build the fixture to that math, hit 240k cumulative at turn 20 with per-call peaks still ~80–100k, get **zero compaction events**, and land in the "Investigate (#16033?)" cell for a pure apparatus reason — after spending the full N=5×2-arm budget.

What must reach ~233k is the re-fed transcript size on the last call of a turn (~60k at turn 5 → ~12k/turn → ~19–20 turns *if* linear). The estimate may be right, but the design should state the target as **per-call context occupancy ≥ ~233k** and track that curve.

**Action:** Add an explicit **single-run pilot** to calibrate turn count before committing N=5. The §4.1 "extend until ≥2 events" stop condition is incompatible with a frozen matched-pairs N=5 (you can't extend mid-flight). Run one cheap builtin-only chain, watch the per-call curve, pad to ≥2 events, then freeze the fixture and run N=5.

### C2. The cumulative 20-turn ratio answers a contaminated question
The §2 threshold (`with_session` total ≤ 0.8× `builtin` total) and H3 ("compresses to 1.5–2.5×") are both stated on **cumulative** total over the whole chain. A 20-turn chain is ~15 sub-threshold turns + ~5 in-regime turns; the cumulative ratio is dominated by the sub-threshold turns where the answer is already known (~4×). It will mechanically "compress" toward the sub-threshold number simply because most turns are sub-threshold — saying almost nothing about steady state.

The long-session pitch is about the **asymptotic per-turn increment**: native's per-turn cost once capped at the compaction ceiling vs prpt's flat per-turn cost — i.e. `Δbuiltin/Δwith_session` over **in-regime turns only**.

**Action:** Make the marginal in-regime per-turn ratio the **primary** metric; cumulative is secondary/illustrative. Note this can run *opposite* to H3: if native caps re-feed at ~233k/turn while prpt holds ~70k, the marginal ratio stabilizes around ~3×, not 1.5× — the thesis could be *stronger* in-regime than the cumulative number shows, and you'd miss it by reading cumulative. H3's range should be derived from the marginal mechanics, not pre-registered as a guess.

---

## High

### H-1. Pre-registration omits the actual refutation branch
§2 stops at "0.8–1.1× → narrow the pitch." There is no committed outcome for **>1.1× (prpt costlier than native once compaction engages)** — native's capped re-feed beating continuous SLM distillation is plausible and is the outcome that most damages the thesis. Add it explicitly (">1.1× in-regime = thesis refuted for long sessions"). The §6 matrix has the same gap.

### H-2. The end-state scorer can't see the H4 finding
H4 pre-registers "a quality gap in either direction is a first-class finding," and §4.5 assigns `score_endstate.py` to detect it. But that scorer ceilings at 1.000 for everything and is evidence-mined from git diff/rg/pytest (codex never persists edit *content*); the per-turn scorer is a file-hash/churn detector, not a judge. A long referential chain is exactly where lossy-memory_record-vs-lossy-compaction continuity loss would surface, and the existing instruments are too coarse to register it — H4 will report "parity 5/5 both arms" by construction.

**Action:** H4 needs a sharper instrument — an LLM judge over the final diff + the back-reference turns, and/or a manual transcript-forensics pass (like the 10-cell diverging-cell analysis on chain_auth).

### H-3. "prpt stays bounded at length" is a 5-turn extrapolation the thesis rests on
The flat ~70k/turn is measured only sub-threshold. Over 20 referential turns the SLM `memory_record` may itself creep (accumulating back-references). If prpt also grows, the ratio compresses for a reason unrelated to native compaction — and you'd misattribute it.

**Action:** The pilot (C1) should plot the **prpt** per-turn curve to 20 turns and confirm it stays bounded, not just the builtin curve.

---

## Medium

### M-1. Rewrite/session confound — state it and spot-check it
Same product comparison as the 4.19× (builtin = raw+native; with_session = SLM-rewrite+bounded). On codex you have a prior finding that the rewrite is token-neutral (`slm_native ≈ builtin`), so the 2-arm design is acceptable as a session-mechanism proxy — but that was measured sub-threshold; longer rewritten prompts could behave differently near the window. Add one `slm_native` (SLM-rewrite + native resume) spot-check at high turn count to confirm token-neutrality in-regime. The confound bites **quality/H4** harder than tokens — a parity result can't separate rewrite-help from session-help.

### M-2. Compaction is stochastic across N=5 — don't blend firing and non-firing runs
Whether/when compaction fires depends on the agent's tool-call count and output volume, which vary run-to-run. Define "in-regime" **per run** relative to that run's first compaction event; require firing in ≥k of 5 runs as a validity gate; report non-firing runs separately rather than averaging them in. The §4.1 "≥2 events" should be per-run, not aggregate.

### M-3. Quota exhaustion truncates exactly the turns you care about
The builtin arm in-regime is the expensive one (re-feeding ~233k/call), and you've hit ChatGPT limits mid-N=5 before. A mid-run abort biases surviving data toward sub-threshold — the worst truncation for this question.

**Correction (the obvious mitigation is unsafe as first stated).** My initial suggestion — run the builtin arm on the `--bare` API path because token counts are auth-independent — is wrong without a guard. Token counts are auth-independent; **the context window is not.** §1.2 and §7 of the design itself say the raw-API gpt-5.5 window is **1,050,000** vs Codex's product-configured **258,400**, and §7 treats the API as a *separate surface where compaction engages ~4× later*. If `--bare` inherits the larger window, the compaction threshold moves ~233k → ~900k and the builtin arm **measures a different regime** (may never compact within a 20-turn chain) — defeating the test. So M-3-via-auth-switch contradicts the doc's own premise.

Two safe paths instead:
- **Option A — verify, then maybe switch.** The window is a product/account-level config. Before relying on the API path, parse a `--bare` smoke-run rollout's `model_context_window`. Use the API path **only if it reads 258,400** (identical regime). If it reads 1,050,000 (or anything else), do not use it for this test.
- **Option B — survive quota without touching auth (preferred default).** Keep subscription auth and make the run **checkpoint/resumable**: on `QuotaExhausted`, abort cleanly and resume the same thread (`codex exec resume <thread_id>`) after quota refresh, so quota *pauses* the run rather than truncating it. Preserves the in-regime turns with the window held constant.

### M-4. Report variance/CI; pre-commit the straddle rule
The audit flagged no variance/CI, and there are bright-line thresholds (0.8/1.1). With N=5 over 20 turns a near-boundary ratio won't be separable from noise. Commit now to how you'll rule when the CI straddles a threshold (report the band, don't call it).

---

## Minor / nits
- **H2 wording.** "Native total-token growth per turn flattens" is imprecise — cumulative keeps climbing post-compaction; what changes is the per-turn *increment* stops accelerating and becomes *bounded* (sawtooth in per-call context, not a flat line). Restate as "per-turn increment caps."
- **Compaction busts the prefix cache** (summary replaces history), so builtin's uncached behavior changes qualitatively at the boundary — another reason to lead with total and treat uncached as a range; worth a sentence so the curve discontinuity isn't misread.
- **Confirm the summarization call's tokens land in `total_token_usage`.** Native compaction is an expensive-agent-model output cost — part of what you're measuring. Verify §4.4 captures the compaction call, not just surrounding turn calls.
- **Proxy-validity claim** (§3, "shared codex-core → exec resume faithful proxy for TUI") is load-bearing — add a one-line source cite (file/commit) so it's checkable later.

---

## Before-running checklist
1. Pilot run to calibrate per-call-occupancy → turn count, plotting **both** builtin and prpt curves (C1, H-3).
2. Switch primary metric to marginal/in-regime per-turn ratio; cumulative secondary (C2).
3. Add the ">1.1× = refuted" branch to §2 and §6 (H-1).
4. Add a real quality instrument for H4 — judge + forensics, not just `score_endstate.py` (H-2).
5. Add one `slm_native` in-regime spot-check (M-1).

---

## Open items the reviewer flagged to verify against artifacts (not yet checked)
- That per-call peak really tops out ~60k in `chain_auth_v2total_codex` (load-bearing for C1).
- That `score_endstate.py` ceilings at 1.000 (load-bearing for H-2).
