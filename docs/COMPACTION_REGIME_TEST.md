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
- `research/chain_long_fixture.py` — the 24-turn dependent fixture (22 referential), built to push per-call occupancy past ~233k.
- `research/chain_test_v2.py` — registers `chain_long` (`--chain long`); reuses the proven runner/arms/quota guard. **No new runner code** (avoids reimplementing `codex exec resume` session handling).
- `research/analyze_compaction_regime.py` — PRIMARY measurement: per-turn cost from the harness `turn.completed.usage`, per-call occupancy + compaction + window from the matched rollouts (by `thread_id`, segmented per turn), marginal in-regime ratio + CI + straddle rule, validity gate, cumulative secondary. Read-only.
- `research/judge_continuity.py` — H4 quality (LLM judge over end-state diffs).
- `research/run_compaction_pilot.ps1`, `research/run_compaction_regime.ps1` — launchers (`.ps1` to avoid the `.cmd` CRLF gotcha).

Validation performed (no firing):
- All files parse; fixture loads (24 turns / 22 referential).
- The analyzer was run against the **existing `chain_auth_v2total` data** and: (a) reproduced the published headline **exactly** — cumulative **4.19×** (`22,358,864 / 5,334,166`; per-run `4,471,773 / 1,066,833`) using gross-input total; (b) recovered per-call occupancy peak **~56k** (matching the manual baseline); (c) read **window=258,400** from the rollouts; (d) **correctly tripped the validity gate (0/5 compaction)** and withheld the PRIMARY metric — i.e. it correctly classifies sub-threshold data as not-in-regime, the discriminator the whole test depends on.

Corrections applied after the PR #43 review (all pre-fire):
- **Total = gross `input_tokens`** (cache-inclusive), sourced from the harness saved per-run `usage` — same metric as `BENCHMARKS.md` (the old `input+output+reasoning` double-counted reasoning; that's why it read 4.17× not 4.19×).
- **Censored/timed-out turns excluded** from both metrics (via the saved `timed_out`/`score.censored`), matching `aggregate_runs`/`turn_timed_out`.
- **CI over runs, not turns:** one marginal ratio per run, CI across the N runs (turns within a run are correlated; pooling understated the band and under-fired the straddle rule).
- **Segmentation fixed:** no implicit leading segment, so the first-compaction turn `f` (which defines the in-regime window) is attributed correctly; and **all** rollout files for a thread are concatenated (a post-compaction rollover can split a thread).
- **H4 judge truncation is reference-aware:** the back-referenced files (`tests/`, `_config.py`, `_client.py`, …) are floated to the front so head-truncation no longer drops the very files the continuity rubric grades.

**Precondition still UNVALIDATED (the real gate):** whether `chain_long` actually pushes per-call occupancy past ~233k and fires ≥2 compactions/run is **untested by construction** — that is exactly what the calibration pilot (§4.0) measures. Treat the pilot as a **blocker**, not a formality; "validated against existing data" means the *instrument* is validated, not the *fixture length*.

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
