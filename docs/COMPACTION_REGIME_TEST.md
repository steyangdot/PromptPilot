# Compaction-Regime Test — Design

**Status:** Design only (not yet run). **Date:** 2026-06-17.
**Revision:** v2 — incorporates the third-party review ([COMPACTION_REGIME_TEST_REVIEW.md](COMPACTION_REGIME_TEST_REVIEW.md)). Change log at the bottom.
**Surface under test:** Codex CLI (`codex exec` / `codex exec resume`), agent model gpt-5.5, SLM slm-openai-v2 (gpt-5.4-nano).
**Companion docs:** [TESTING_STRATEGY.md](TESTING_STRATEGY.md), [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md), [BENCHMARKS.md](BENCHMARKS.md).

---

## 1. Why we want this test (motivation, what we're proving, the underlying goal)

### 1.1 The underlying goal
PromptPilot's core claim is: **a bounded, SLM-distilled session beats the agent's native session on tokens, at equal task quality.** We have proven this on Codex at **4.19× fewer total tokens / 1.97× uncached, end-state parity** (see [BENCHMARKS.md](BENCHMARKS.md)). The strategic bet behind the project is that the world runs **more, longer, unattended** agentic coding over time — and that in *that* regime the native session's unbounded transcript re-feed dominates cost, so bounding wins big.

### 1.2 The threat to the claim
The 4.19× was measured **entirely below Codex's auto-compaction threshold.** Empirically (measured, not assumed — see §3):

- The Codex effective context window for gpt-5.5 is **258,400 tokens** (the value codex writes into every rollout; the raw-API gpt-5.5 window is 1,050,000, but the Codex *product* configures a smaller window — that discrepancy is real, documented, and the Codex number governs our runs).
- Native auto-compaction fires at roughly **90–95% of that window (~233k–245k)**.
- Our `chain_auth` benchmark's **per-call context peaks at ~60k** — about **4× under** the threshold. Native never compacts, re-feeds freely, and prpt's bounding wins cleanly.

But the native agent has its **own** context-management: once a session is long/heavy enough to cross the threshold, **codex auto-compacts** — it summarizes older history and **caps its own re-feed cost.** The 4× depends on native *not* doing this. **If prpt's advantage evaporates once native compaction engages, then prpt only matters for short/mid chains — undercutting the exact use case (long, autonomous, "runs for hours" agent sessions) that is the project's headline pitch.**

### 1.3 What we are trying to prove
**That prpt's bounding still wins — on tokens *and* on quality — in the compaction regime**, because:

- prpt bounds **proactively**, every turn, with a **cheap SLM** (~0.2% of run cost); whereas
- native bounds **reactively**, only near the limit, using the **expensive agent model** to summarize, and the summary is **lossy**.

If that holds, the value proposition is **regime-independent** — it covers the long-autonomous-session market that matters most. If it does **not** hold, we will **honestly narrow the pitch** to short/mid chains and reconsider the long-session story.

### 1.4 Why this is the highest-leverage untested regime
Every prior PromptPilot result is **sub-threshold**; the long-autonomous deployment (CI bots, codemods, multi-hour agents) is **above threshold**. So this is the **highest-leverage untested regime** for the session thesis — *not* the project's only open problem (ambiguity, routing accuracy, preservation recall, and SLM A/B are co-equal; see `ROADMAP.md`), and *not* currently on the committed benchmark queue (proposed as a near-term addition, not roadmap-licensed). What it uniquely does: validate or refute the **implicit assumption** behind the `BENCHMARKS.md` headline ("the multiplier scales with session length") — namely that native *never compacts*.

---

## 2. Hypotheses & decision rules (pre-registered)

Pre-registration matters — a long run is expensive and one turn can swing the verdict ([feedback_n3_chain_noise]). Committed *before* running.

### 2.1 The primary metric is the MARGINAL in-regime per-turn ratio (not cumulative)
> **Review C2 (accepted):** a ~20-turn chain is ~15 sub-threshold turns + ~5 in-regime turns. The **cumulative** ratio is dominated by the sub-threshold turns where the answer is already ~4×, so it mechanically "compresses" toward the known number and says almost nothing about steady state.

- **PRIMARY:** `Δbuiltin / Δwith_session` computed over **in-regime turns only** (turns at/after that run's first compaction event). This is the long-session asymptote: native's per-turn cost *once capped at the compaction ceiling* vs prpt's flat per-turn cost.
- **SECONDARY (illustrative only):** cumulative total ratio over the whole chain.
- Note the marginal ratio can run **opposite** to a naive "compression" guess: if native caps re-feed at ~233k/turn while prpt holds ~70k, the marginal ratio stabilizes near **~3×** — i.e. the thesis could be **stronger** in-regime than the cumulative number shows.

### 2.2 Hypotheses
- **H1 (compaction fires):** in `builtin`, once per-**call** context occupancy crosses ~233k we observe explicit **compaction events** in the rollout JSONL (sawtooth: per-call context climbs, drops after a compaction event).
- **H2 (native per-turn increment caps):** native's *per-turn token increment* stops accelerating and becomes **bounded** after compaction engages. (Cumulative keeps climbing — the change is the increment, not a flat line.)
- **H3 (prpt still cheaper in-regime):** the **marginal in-regime ratio** is > 1 in prpt's favor. The expected magnitude is **derived from the marginal mechanics** (native ceiling ÷ prpt flat ≈ ~233k ÷ ~70k ≈ ~3×), **not** pre-registered as a guess.
- **H3b (prpt stays bounded at length):** prpt's per-turn input does **not** itself creep over 20 turns (the `memory_record` doesn't accumulate back-references unboundedly). If prpt grows too, any ratio change must not be misattributed to native compaction. *(Review H-3 — load-bearing; measured in the pilot, §4.1.)*
- **H4 (quality holds — or a gap appears):** end-state parity holds for prpt despite lossy memory competing against native's *also-lossy* compaction. A gap in **either** direction is a first-class finding — and requires a real quality instrument (§4.5), since the default scorers cannot see it.

### 2.3 Decision thresholds (on the PRIMARY marginal in-regime ratio, with compaction confirmed engaged per H1)
- **Thesis holds (long sessions):** marginal ratio **≥ 1.5×** at end-state parity.
- **Thesis narrows:** **1.1×–1.5×** — real but modest in-regime; pitch emphasizes short/mid chains, hedge long.
- **Thesis refuted for long sessions:** **≤ 1.1×**, i.e. prpt ≈ native **or costlier** once compaction engages (native's capped re-feed beating continuous SLM distillation is plausible — *Review H-1, the branch the v1 doc omitted*). Honestly restrict the pitch to sub-threshold chains.
- **Invalid / investigate:** H1 false (no compaction in a long chain) → larger effective window than logged, or the #16033 bug (§5). Do **not** report a token verdict.
- **Variance rule (Review M-4):** report the ratio with an N=5 confidence band. **If the band straddles a threshold, report the band and decline to call it** — do not round to the nearest verdict.

---

## 3. What we already know (established this session — the baseline)

From parsing the real run artifacts (`_session_retest_2026-06-07/chain_auth_v2total_codex` + `~/.codex/sessions` rollouts):

- **Window = 258,400** (logged by codex in every rollout). Threshold ≈ **233k–245k**.
- **Per-call vs cumulative are different quantities** (the crux of Review C1): `builtin` run 3 (rollout `2026-06-16T17-53-45…`) = **65 calls, each 15k–60k, max 60,316 — far under the window — zero compaction events**, yet cumulative input = **1,601,581**. *Compaction fires on per-call occupancy, not the cumulative sum.*
- **`with_session`** per-turn input stays flat/declining ~314k → 70k (5 turns).
- Cumulative: **builtin 4.47M vs with_session 1.07M = 4.19×** (matches published totals to the token).
- **Proxy validity:** auto-compaction lives in **shared `codex-core`** — `codex-rs/core/src/session/turn.rs` (`auto_compact_token_status` / `token_limit_reached` / `run_auto_compact`), no `exec`/`tui` branch; `codex exec` (`codex-rs/exec/src/lib.rs`) adds no compaction logic. So `codex exec resume` is a **faithful proxy** for the interactive TUI's context behavior. *(Cite added per Review nit.)*

**Implication:** 4.19× is honest *for sub-threshold chains*. This test attacks the regime that data cannot speak to.

---

## 4. Implementation plan

### 4.0 Sequencing — pilot FIRST, then freeze, then N=5 (Review C1)
The v1 "extend until ≥2 compaction events" stop condition is **incompatible** with a frozen matched-pairs N=5 (you can't extend mid-flight). So:

1. **Calibration pilot (single run, builtin-only, cheap):** run a long builtin chain and **plot the per-call context-occupancy curve**. Target is **per-call occupancy ≥ ~233k**, *not* cumulative input. Also **plot the prpt per-turn curve to the same length** (H3b) to confirm prpt stays bounded.
2. **Freeze the fixture** at a turn count that produces **≥2 compaction events per run** with margin.
3. **Run N=5 interleaved** on the frozen fixture.

### 4.1 The fixture — a long dependent chain that pushes per-CALL occupancy past ~233k
Design principles:
- **Dependent / referential** across turns (back-references like "the same fix", "that helper") so session memory is load-bearing.
- **Realistic coding work** on `C:/projects/httpx` (reset to a known commit), not synthetic padding.
- **The re-fed transcript (per-call occupancy) must reach ~233k.** This is the *prefix size on the last call of a turn*, which grew ~12k/turn in `chain_auth` → ~**19–20 turns if linear** — but the pilot measures the real curve rather than trusting the extrapolation. Levers: more turns **and/or** heavier turns (larger files / wider modules).

Deliverable: a new chain in `research/chain_test_v2.py` (e.g. `id: "chain_long"`), same `{raw, expected_files, expected_action}` schema as `chain_auth`/`chain4`.

### 4.2 Arms (N=5, interleaved)
- **`builtin`** — raw prompt + native `codex exec resume`, **default config** (compaction ON; behaves like the TUI). The arm where compaction now engages.
- **`with_session`** — prpt bounded loop (fresh `codex exec` + injected `memory_record`).
- **`slm_native` spot-check (Review M-1):** at **high turn count only** (not full N=5), one run of SLM-rewrite + native resume, to confirm the rewrite stays ~token-neutral *in-regime* (it was sub-threshold). This isolates rewrite-help from session-help — which matters most for the H4 quality read, where the rewrite/session confound bites hardest.

Ideally interleave builtin/with_session turn-by-turn (matched pairs) so cache-warmth cancels in the ratio ([MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md)). **Implementation note:** the existing harness runs arms **block-sequential** (all with_session, then all builtin), and we reuse it rather than write a new interleaved runner. That is acceptable **because the PRIMARY metric is total = gross input tokens, which is cache-independent**; interleaving would only tighten the *uncached* secondary — so **we do not publish an uncached number from this run.** (Doc-vs-code reconciliation per the PR #43 review.)

### 4.3 "In-regime" is defined PER RUN; compaction is stochastic (Review M-2)
Whether/when compaction fires depends on each run's tool-call count and output volume, which vary.
- Define a run's **in-regime turns** relative to **that run's first compaction event**.
- **Validity gate:** require compaction to fire in **≥4 of 5** builtin runs; **report non-firing runs separately**, never averaged into the marginal ratio.
- The "≥2 compaction events" target is **per run**, not aggregate.

### 4.4 Config & instrumentation
Config (verify before running):
- **No `model_context_window` / `model_auto_compact_token_limit` overrides** in `~/.codex/config.toml` (verified absent 2026-06-17) — setting either can trip #16033 (compaction silently fails).
- `service_tier` CLI-valid (desktop app can clobber it — §5). Smoke-test `codex exec` first.

Instrument from the **rollout JSONL** (`~/.codex/sessions/**/rollout-*.jsonl`), identical for both arms:
- Per **call**: `last_token_usage` (input / cached_input / output / total), `model_context_window`.
- Per **turn/session**: `total_token_usage` cumulative.
- **Compaction events:** match on event `type` containing `compact` (not raw-text substring).
- **Capture the compaction summarization call's tokens (Review nit):** native compaction is an *expensive-agent-model output* cost — part of what we measure. Verify the compaction call's usage lands in `total_token_usage` and is attributed to the turn that triggered it.
- Emit per-arm: per-call occupancy trajectory, per-turn increments, cumulative totals, and a **compaction timeline** (turn index + pre/post occupancy).

### 4.5 Metrics & scoring
- **PRIMARY:** marginal in-regime per-turn ratio (§2.1). **SECONDARY:** cumulative total ratio.
- **Lead with TOTAL tokens — defined as gross `input_tokens` (cache-inclusive, output excluded), the *same* metric `BENCHMARKS.md:62` publishes** (`1,066,833 = 865,126 cached + 201,707 uncached`). The analyzer sources this from the harness's saved per-run `usage`, not a re-parse, and **excludes censored/timed-out turns** (matching `aggregate_runs`/`turn_timed_out` — the guard that fixed the 4.47×→2.36× inflation; a censored in-regime turn logs ~0 and would bias the ratio toward REFUTED). Uncached is a **warmth range**, never a single number — and compaction **busts the prefix cache at the boundary**, so builtin's uncached behavior changes qualitatively right where we care. Another reason total leads.
- **Quality (H4) needs a real instrument (Review H-2 — accepted, confirmed):** `score_endstate.py` ceilings at **1.000** (15/15 in prior runs) and `score_turn` is a file-hash/churn detector — **neither can detect continuity loss**, so H4 would report "parity by construction." Replace with: **(a) an LLM judge over the final diff + each back-reference turn** (did the resolved reference match intent?), and **(b) a manual transcript-forensics pass** on diverging cells (as done in the chain_auth 10-cell analysis). Per-turn churn scores are kept only as a cheap sanity signal, not the parity verdict.
- Report whether/when compaction fired (H1) and the per-turn-increment capping (H2).

### 4.6 Outputs
- Data under `_session_retest_2026-06-07/chain_long_codex/`.
- A `COMPARISON.md`: **marginal in-regime ratio (primary, with CI)**, cumulative ratio (secondary), per-call occupancy + per-turn-increment curves, compaction timeline, LLM-judge + forensics quality read, and the verdict against §2.3 — including the straddle rule.

---

## 5. Cost, feasibility, risks

- **Quota / cost & truncation bias (Review M-3, accepted *with a caveat the review missed*):** the `builtin` in-regime turns are the expensive ones, and we've hit ChatGPT limits mid-N=5 before; a mid-run abort biases survivors toward sub-threshold — the worst truncation for this question. Mitigation options:
  - **Checkpoint/resume across quota windows** (preferred default), with the `QuotaExhausted` guard so partial runs don't pollute aggregates; **or**
  - run `builtin` on the **`--bare` API path** so in-regime turns complete (total tokens are auth-independent). **Caveat the review overlooked:** the API gpt-5.5 window is **1,050,000**, not Codex's 258,400 — switching auth could switch the **window** and move the compaction threshold to ~900k, measuring a *different regime*. **Only use `--bare` after verifying (parse a `--bare` rollout's `model_context_window`) that the effective window is identical to the subscription path.** Otherwise checkpoint instead.
- **Fixture-design effort:** authoring a genuinely *dependent* 20-turn coding chain is the main human cost.
- **Desktop-config gotcha:** the codex desktop app can rewrite `~/.codex/config.toml` (e.g. `service_tier="default"`) → every `codex exec` fails rc=1 / 0 tokens. Check config first if a run zeros out. (memory: codex-desktop-config-gotcha.)
- **Cache-warmth confound:** mitigated by interleaving; never report a single cross-run uncached number.
- **#16033 risk:** no compaction despite a long default-config chain ⇒ apparatus issue, not a token result — investigate first.
- **Stochastic compaction:** handled per §4.3 (per-run in-regime, ≥4/5 gate).

---

## 6. What each outcome means (decision matrix — on the PRIMARY marginal in-regime ratio)

| Result (compaction confirmed, parity held) | Interpretation | Action |
|---|---|---|
| Marginal ratio **≥ 1.5×** | Thesis holds across regimes | Pitch stands for long/autonomous sessions; publish in-regime marginal ratio |
| Marginal ratio **1.1×–1.5×** | Modest in-regime win | Emphasize short/mid chains; hedge long |
| Marginal ratio **≤ 1.1×** (incl. **>1× costlier**, i.e. prpt loses) | Native capped re-feed erases / beats prpt once engaged — **thesis refuted for long sessions** | **Narrow the pitch** to sub-threshold chains; revisit the long-session story |
| prpt cheaper **and** higher quality (LLM-judge/forensics) | Cheap-proactive beats expensive-lossy on cost **and** continuity | Strongest result; lead with it |
| Compaction does **not** fire | Window > logged, or #16033 | Investigate apparatus before any token claim |
| CI straddles a threshold | Not separable at N=5 | Report the band; do not call it (§2.3) |

---

## 7. Follow-ups (out of scope here)
- **`enriched_session` arm** — does a richer, structured `memory_record` recover conversational losses without eroding the win? (separate design; the H4 instrument built here is a prerequisite to measure it.)
- **API-surface variant** — repeat against the raw API (1,050,000 window) where compaction engages ~4× later, contrasting Codex's small-window self-capping with a big-window surface. (Also the natural place to resolve the M-3 window-parity question.)
- **Claude analog** — claude's `--resume` caches cheaply rather than re-feeding; compaction-regime question is codex-specific but worth a claude counterpart.

---

## 8. Implementation status (built, validated, NOT fired)

The test is **implemented** (2026-06-17) and **statically validated against existing data**; it has **not been run** (no codex agent runs, no model calls). Run guide: [`research/COMPACTION_REGIME_README.md`](../research/COMPACTION_REGIME_README.md).

Files (this worktree):
- `research/chain_long_fixture.py` — the 13-turn dependent fixture (11 referential), built to push per-call occupancy past ~233k.
- `research/chain_test_v2.py` — registers `chain_long` (`--chain long`); reuses the proven runner/arms/quota guard. **No new runner code** (avoids reimplementing `codex exec resume` session handling).
- `research/analyze_compaction_regime.py` — PRIMARY measurement: per-turn cost from the harness `turn.completed.usage`, per-call occupancy + compaction + window from the matched rollouts (by `thread_id`, segmented per turn), marginal in-regime ratio + CI + straddle rule, validity gate, cumulative secondary. Read-only.
- `research/judge_continuity.py` — H4 quality (LLM judge over end-state diffs).
- `research/run_compaction_pilot.ps1`, `research/run_compaction_regime.ps1` — launchers (`.ps1` to avoid the `.cmd` CRLF gotcha).

Validation performed (no firing):
- All files parse; fixture loads (13 turns / 11 referential).
- The analyzer was run against the **existing `chain_auth_v2total` data** and: (a) reproduced the published headline **exactly** — cumulative **4.19×** (`22,358,864 / 5,334,166`; per-run `4,471,773 / 1,066,833`) using gross-input total; (b) recovered per-call occupancy peak **~56k** (matching the manual baseline); (c) read **window=258,400** from the rollouts; (d) **correctly tripped the validity gate (0/5 compaction)** and withheld the PRIMARY metric — i.e. it correctly classifies sub-threshold data as not-in-regime, the discriminator the whole test depends on.

Corrections applied after the PR #43 review (all pre-fire):
- **Total = gross `input_tokens`** (cache-inclusive), sourced from the harness saved per-run `usage` — same metric as `BENCHMARKS.md` (the old `input+output+reasoning` double-counted reasoning; that's why it read 4.17× not 4.19×).
- **Censored/timed-out turns excluded** from both metrics (via the saved `timed_out`/`score.censored`), matching `aggregate_runs`/`turn_timed_out`.
- **CI over runs, not turns:** one marginal ratio per run, CI across the N runs (turns within a run are correlated; pooling understated the band and under-fired the straddle rule).
- **Segmentation fixed:** no implicit leading segment, so the first-compaction turn `f` (which defines the in-regime window) is attributed correctly; and **all** rollout files for a thread are concatenated (a post-compaction rollover can split a thread).
- **H4 judge truncation is reference-aware:** the back-referenced files (`tests/`, `_config.py`, `_client.py`, …) are floated to the front so head-truncation no longer drops the very files the continuity rubric grades.

**Precondition still UNVALIDATED (the real gate):** whether `chain_long` actually pushes per-call occupancy past ~233k and fires ≥2 compactions/run is **untested by construction** — that is exactly what the calibration pilot (§4.0) measures. Treat the pilot as a **blocker**, not a formality; "validated against existing data" means the *instrument* is validated, not the *fixture length*.

---

## 9. Calibration pilot results (2026-06-18)

Two pilots were needed before the real run.

**Pilot 1 (original fixture — "write tests + run them"): WEDGED.** The agent executed httpx's hang-prone network/timeout tests (no real server) → pytest hung → 184s command-timeouts × retries → ~60 model calls/turn → per-turn cost exploded to ~9.8M and orphaned pytest procs piled up until the run hung at T12 (79 min no progress). Salvaged occupancy from T1–T11: monotonic 62k→178k (~11.6k/turn), 0 compaction, would not have compacted until ~turn 16. **Lesson: drop test execution; drive occupancy via large-file reads.**

**Fixture reworked** (read-heavy, no test execution, no-tests guard per turn).

**Pilot 2 (reworked fixture, builtin-only, 18 turns): SUCCESS.**
- **Compaction fires** — per-call occupancy climbed to **222k (turn 10)** then **compacted (reset to 66k, turn 11)**; clean sawtooth, ~turn 10–11. Climbed again to 214k by t18.
- **Stable** — completed all 18 turns (no wedge). One turn (T12, the ResilienceConfig refactor) timed out / censored.
- **Headline finding — compaction does NOT make native cheap.** Per-turn input kept climbing *through and past* compaction: t10=12.3M → t18=20.8M; one builtin run ≈ **~187M tokens**. Compaction resets per-*call* context (222k→66k) but per-*turn* billed cost keeps rising because the agent **churns more calls** to recover lost context (+ a bad-`rg`-retry artifact). → preliminary signal: the thesis likely **holds strongly** (native stays ~12–20M/turn in-regime vs prpt's expected ~1–2M), pending the `with_session` run. Churn largely cancels in the ratio (same agent/tasks both arms).
- **Analyzer/segmentation validated on real data** — occupancy monotonic, segments align to turns, thread_id→rollout match worked; the only "non-monotonic" drop is the legitimate compaction sawtooth (guard refinement noted).

**Cost anchor:** the 18-turn builtin run = **~20% of a 5h ChatGPT-Pro window**.

**Real-run parameters (locked):** trimmed to **13 turns** (compaction by ~11 + in-regime turns 11–13), **N=3**, both arms (`builtin` + `with_session`, MAX_TOKENS-fixed). Est. **~41% of a 5h window**, ~5–7h wall. Caveat: the in-regime window is thin (~turns 11–13, and T12 is timeout-prone) → ~6–9 in-regime turn-pairs for the marginal ratio; if too thin to separate from noise, widen to ~15 turns.

---

## 10. RESULT

### ✅ N=5 CLEAN (2026-06-19) — the definitive result (supersedes the N=2 below)

Clean N=5 on the 13-turn `chain_long`, codex, on the timeout-safe harness (1200s cap + post-run reparse; ran ~8PM→6AM riding the 5:17 quota refresh, never exhausted). **5/5 builtin runs fired compaction → regime CONFIRMED, 0 censored.**

- **Tokens — THESIS HOLDS (in the compaction-fired regime):** cumulative **9.69×** (builtin 602,086,843 / with_session 62,148,057); PRIMARY marginal in-regime **13.59×, 95% CI [10.73, 16.46]**. (Per-run builtin 155/119/106/113/109M; with_session 11.4/9.9/14.3/8.1/18.4M.) Note: compaction *fired* 5/5 while peak per-call occupancy measured ~218k (just under the nominal ~233k), so "compaction-fired regime" is the precise claim — not "occupancy crossed 233k".
- **Continuity (H4) — near-parity with a small, *verified* tax.** The LLM judge gap (builtin 0.26 vs with_session 0.134) is **mostly artifact** — an 11-agent forensic sweep + human eyeball found builtin 30/30 feature-cells present, with_session 27/30 (3 partial, **0 absent**, 3/5 runs flawless), and the ResilienceConfig refactor landed in **both** arms — **plus a real residual**: 2 genuine continuity defects in 2/5 with_session runs (run3 orphaned timeout-kwarg tests → TypeError; run4 lost Retry-After header parsing) vs **0/5 native** (both human-confirmed).
- **Honest headline:** *once compaction engages, the bounded session runs the task on ~9.7× fewer tokens (13.6× marginal) at near-parity quality, with a small verified continuity tax.*
- **Root cause + fix:** the tax traces to the bounded session's `MAX_TURNS=4` recency window — early-turn contracts fall out before late refactors; a bigger window only moves the cliff. Finding + the memory-system fix (contracts/ledger + refactor guard, not recency) are in [`SESSION_MEMORY_ARCHITECTURE.md`](SESSION_MEMORY_ARCHITECTURE.md); the MVP is `research/memory_ledger.py` (+ the `with_memory` harness arm). Full record in the `compaction_regime_n5_clean` memory.
- Data: `research/data/chain_results_v2/codex/chain_long/` (N=2 archived in `chain_long_n2_recovered/`).

> The N=2 section below is the earlier provisional run, **SUPERSEDED** by the N=5 above. Its 5.48× / 8.25× / 12.2× figures are obsolete — cite **9.69× / 13.59×**.

### N=2 run (2026-06-18, provisional — SUPERSEDED by the N=5 above)

Ran N=2 both arms (`builtin` vs `with_session`) on the 13-turn `chain_long`, via a detached Windows Scheduled Task (survives Claude session resets — the N=3 attempt died overnight when the session reset). Analyzed by `analyze_compaction_regime.py` + an ultracode workflow (timeout forensics, continuity judge, adversarial math re-verification, synthesis). **Verdict: thesis HOLDS-WITH-CAVEATS — directional signal (~5×), NOT yet a publishable headline.**

### ⚠️ CORRECTION (recovered censored data, 2026-06-18) — the win is ~5.5×, not ~8–12×
The censored turns the analyzer excluded were **not** cheap 0-token artifacts — re-reading their (late-flushed) `turn.completed` off disk shows they were `with_session`'s **heaviest** turns, where the bounded arm **thrashed**: ws run1 T13 (migrate) = **9.5M**, ws run2 T1 = **4.1M**, plus T10/T11/T6 ≈ 1–1.4M each. Excluding them made `with_session` look far cheaper than it was. With the recovered data (all turns, real `turn.completed` tokens):
- **Cumulative total ratio = 5.48×** (builtin 171.6M / with_session 31.3M) — vs the excluded-data 8.25× matched / 10.84× unmatched. **The honest token win is ~half the first figure.**
- The **marginal in-regime ratio (12.2× below) is inflated for the same reason** — the excluded T13 pair is 13.3M/9.5M ≈ **1.4×**, not ~12×. Bounded **thrashes to multi-million on the heavy referential turns** (29–54 tool calls/turn re-discovering context), so its in-regime advantage is much softer than 12.2× — **do not cite 12.2×**.
- **Still a win (≥1.5×), but ~5× cumulative, not ~10×**, and bounded's heavy-turn thrash is itself a real cost (the reliability caveat — now also a *token* caveat).

This is exactly why the harness fix matters: the censored-exclusion didn't just drop recording artifacts — it dropped bounded's *most expensive* turns. The recovered **~5.5×** supersedes the 8.25×/12.2× figures below (kept for the record). **The harness fix is now SHIPPED + free-validated (2026-06-18):** `CODEX_TIMEOUT_SEC=1200` + a **post-run reparse pass** + recovery-gating so recovered turns are counted, not censored — replayed on this N=2 data it reproduces **5.48×** with **0 censored** turns. (The reparse is a *post-run* pass, not an in-line grace-wait: the codex grandchild flushes `turn.completed` 25 s–13 min after the kill, so only a sweep after the job ends recovers it. See `docs/COMPACTION_TIMEOUT_FIX_PLAN.md`.) Re-run **N≥5** for the clean published number (expected ~5–6× cumulative).

**Compaction (H1): confirmed 2/2** — builtin run1 compacted @T10 (occ 222k→63k), run2 @T8 & T13. with_session never neared the threshold (peak occ ~127–152k). The regime engaged.

**Tokens (lead with TOTAL, gross input):**
- **Matched cumulative (fair): 8.25×** (builtin 113.6M / with_session 13.8M, 20 kept pairs). Recomputed from raw independently: 8.2518×. **[SUPERSEDED → 5.48× with recovered data; see CORRECTION above.]**
- Unmatched cumulative: 10.84× — **inflated by asymmetric censoring** (5 with_session vs 1 builtin turn dropped; the dropped with_session turns include heavy work → its sum understated). Do not headline.
- **Marginal in-regime per-turn ratio: 12.2×** (per-run 13.36×/11.03×; CI ~[9.9, 14.5]). **[SUPERSEDED — do not cite: inflated by the same censored-exclusion; the recovered heaviest in-regime pair is ~1.4×. See CORRECTION above.]**
- Uncached: matched **2.89×** (unmatched 3.61×) — **not publishable** (block-sequential + compaction busts the prefix cache → warmth-confounded).
- **Mechanism:** compaction resets per-*call* occupancy but does **not** cap per-*turn* cost — the agent churns to recover dropped context, so native keeps climbing 10–17M/turn while bounded holds ~0.4–1.2M/turn. This is *why* bounding wins in-regime, and it strengthens the long-session case.
- **Correction:** gross `input_tokens` climbs **monotonically** every turn; the sawtooth is in per-call **occupancy** only (the `~/.codex/sessions` rollout series), not the published total.

**Quality / continuity (H4): rough PARITY (no hard oracle).** The LLM judge reported a continuity gap (with_session 0.28 < builtin 0.50), but hand-inspection of the diffs **overturns it**: the unified `ResilienceConfig` dataclass + sync/async client migration (the referential refactor) **landed in all 4 runs, both arms**; the −0.22 reduces to one weak with_session run missing early-turn features (retry-after/http-date). `implemented` is ~identical (0.31/0.315). Confidence LOW on any gap; MODERATE-HIGH that the refactor carried forward in both arms. **(Judge caveat, 2026-06-18:** the as-run rubric also scored a `tests_updated` dimension + a `pytest_passed` signal — both **bogus for this fixture**, which writes **no tests** (the `_GUARD` forbids test work). The 0.625/0.0 measured spontaneous test-writing, not continuity; both were removed from `judge_continuity.py`, and the rubric was corrected to the actual 13-turn feature set. **Disregard the as-run `tests_updated`/`pytest` numbers.**)

**Reliability: a real bounding cost, amplified by a measurement artifact.** with_session censored 5 turns vs builtin 1. The asymmetry is **structural** (~3.5/5 thrash-induced): the bounded arm re-discovers file layout + re-locates prior edits every turn (T8 40–54 cmds vs builtin 7–11) → several turns hit the 300s cap. **But all 6 "timeouts" actually COMPLETED** — recorded input=0 via the **orphan-flush race** (codex grandchild flushed `turn.completed` after the harness parsed; same as `audit_uncached_timeout_bug`), so **no work was lost** (end-state parity held). **Fixed (2026-06-18):** codex cap raised to 1200s + a post-run reparse pass + recovery-gating (free-validated to reproduce 5.48× with 0 censored; see `docs/COMPACTION_TIMEOUT_FIX_PLAN.md`). One outlier (run2 T1 thrash with *no history* → 56 reads/1 edit) is agent-flailing-on-a-big-file, not a bounding defect.

**Why it stays PROVISIONAL (3 caveats):**
1. **N=2 < the design's N≥5 run count → provisional.** Direction (≥1.5×) is robust to N=2; the magnitude is a 2-point estimate with no real CI — carry the recovered cumulative **~5.5×**, not the superseded 12.2×. (With the ≥4/5 firing gate, N=2's 2/2 now passes coverage; the provisional flag is the run-count check.)
2. The **reliability cost** (5× timeout asymmetry) sits alongside the token win — bounding trades cheaper tokens for more per-turn exploration that occasionally hits the cap.
3. **No correctness oracle** — quality rests on LLM judge + diff forensics, not ground truth.

**Decision:**
- This doc records the **provisional N=2 result** (above). **Lead with the recovered cumulative ~5.5× (5.48×)** — the censored-exclusion figures (8.25× matched / 10.84× unmatched / **12.2× marginal**) are **superseded by the §10 CORRECTION** and must not be headlined; the heaviest in-regime pair is **~1.4×**, not 12×. Never publish uncached.
- **`docs/BENCHMARKS.md`: NOT updated.** The published 4.19×/1.97× is the *valid N=5 interleaved end-state-parity sub-threshold* number; this compaction result is N=2 / block-sequential / no-oracle / provisional — not at parity with the benchmark's standards.
- **To promote an in-regime number:** N≥5 interleaved on the frozen 13-turn (or widened ~15-turn) fixture, on the **shipped timeout-safe harness** (1200s cap + post-run reparse, already free-validated).

---

## Change log (v1 → v2, from the third-party review)
- **C1:** target **per-call occupancy ≥233k** (not cumulative); added a **calibration pilot** before freezing N=5 (§4.0/§4.1).
- **C2:** **marginal in-regime per-turn ratio** is now the PRIMARY metric; cumulative demoted to secondary; H3 magnitude derived from mechanics, not guessed (§2.1/§4.5).
- **H-1:** added the **>1.1× / "prpt costlier" = thesis refuted** branch (§2.3, §6).
- **H-2:** replaced `score_endstate.py` for H4 with an **LLM judge + transcript forensics** (confirmed the scorer ceilings at 1.000) (§4.5).
- **H-3:** pilot now plots the **prpt** curve too; added **H3b** (prpt stays bounded at length) (§2.2/§4.0).
- **M-1:** added an **`slm_native` in-regime spot-check** for the rewrite/session confound (§4.2).
- **M-2:** **per-run** in-regime definition + **≥4/5 firing** validity gate; non-firing runs reported separately (§4.3).
- **M-3:** quota mitigation added — **with the caveat** that `--bare` changes the window (API=1M); checkpoint/resume preferred unless window-parity is verified (§5).
- **M-4:** variance/CI + **straddle rule** committed (§2.3).
- **Nits:** H2 reworded ("per-turn increment caps"); cache-bust-at-boundary noted; capture the compaction call's tokens; shared-core proxy **source cite** added (§3).
