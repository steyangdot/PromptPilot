# Benchmarks

PromptPilot measures the SLM harness on two dimensions:

1. **Cost** — does routing through a small model pay for itself?
2. **Preservation** — do critical facts survive the rewrite?

A harness output is not successful just because it is shorter. Token reduction without preservation makes the expensive coding agent cheaper but less informed — usually a net loss.

> **How these numbers were measured** — the experimental design, the two-metric (total vs uncached) policy, and the honest journey of corrections that produced the final figures — is in [Testing Strategy & the Road to the Numbers](TESTING_STRATEGY.md) and the cache-mechanics deep-dive in [Measurement Methodology](MEASUREMENT_METHODOLOGY.md).

> ⚠️ **Correction (2026-07-01, applied here 2026-07-03).** Codex CLI builds since ~2026-06-14 report **thread-cumulative** token usage on resumed threads (turn *N*'s counter covers turns 1..*N*); our harness summed per-turn readings, **double-counting every codex native-resume arm by ≈ ×(N+1)/2**. All previously-published codex bounded-vs-native figures on this page were inflated by that artifact and have been corrected below: the total-token direction **survives at ~1.2–2.4× (regime-dependent)**; the old **~4.2× total / ~1.97× uncached** headline is refuted (corrected: **1.34× total; uncached 0.48× — inverted, bounding pays *more* full-price tokens on short chains**). Mechanics, refuted-vs-corrected table, and the fixed accounting code are in [Thread-Cumulative Usage Correction](THREAD_CUMULATIVE_USAGE_CORRECTION.md). Claude-code numbers were never affected (per-invocation usage).

## Measured results

These numbers come from the in-repo chain harness ([research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py)) against a real target repo (`httpx`). Each row is a specific experiment with a stable identifier so re-runs are reproducible.

| Experiment | Setup | What was measured | Result |
|---|---|---|---|
| SLM control reduces agent token use, **codex** (chain1 N=5) | Full PromptPilot (SLM rewrite + bounded session) vs raw prompt + native `codex exec resume` | Total agent input tokens over the run | ⚠️ **WITHDRAWN pending re-derivation** — the old "~7.6× fewer (1.11M vs 8.42M)" figure's raw native-arm artifacts could not be re-audited under the [thread-cumulative correction](THREAD_CUMULATIVE_USAGE_CORRECTION.md), and an earlier re-test had already flagged this family as cache-read-confounded. Do not cite. The corrected bounded-vs-native evidence is the chain_auth and chain_long sections below. |
| chain5 codex hybrid | API-key gpt-5.4-nano SLM + ChatGPT-subscription codex LLM | Token footprint by layer (15-turn chain) | **SLM control layer ~24k input tokens vs ~12.66M agent input tokens — the control layer is ~0.2% of the footprint, nearly free to add.** It frames the agent's task (rewrite, route, context-bounding) but doesn't generate the agent's tokens — codex's own loop does. Hybrid routes that 0.2% to cheap metered API, the rest to a flat-fee subscription. (≈ $0.0085 API + ~$38-at-list-rates of agent tokens, but tokens are the measured fact — see caveat.) |
| `gpt-5.4-mini` (codex CLI) vs `gpt-5.4-nano` (SDK) | Each path's cheapest usable model — nano isn't accepted on ChatGPT-auth codex, so this compares *realistic configs*, not one isolated variable; one-off 2-call measurement, not a committed benchmark | Per-call input tokens (dollars downstream) | **Two separable effects: (1) transport — codex's agent loop adds ~19k input tokens of fixed overhead per call, largely model-independent, vs the SDK's prompt-only payload (~400 for a typical call); (2) model — mini lists at 3.75× nano per token. Combined at list rates ≈ ~100× more $/call for the codex path — but that's transport × model, not a clean CLI-vs-SDK figure, and codex-on-subscription is $0 incremental anyway.** |
| Session memory value, **claude-code** (chain1 N=5) | WITH session vs NO session (both SLM-rewritten) | Success rate, cost-per-success | ⚠️ The original "+60% success" was a cached-read / phantom-bug artifact; the clean **chain_auth** replication shows **end-state parity** (see the chain_auth section below). Session value is tool-dependent and primarily a *cost* effect. |
| Session memory value, **codex** (chain1 N=5) | WITH session vs NO session | Success, input tokens | **success tied (1.70 vs 1.90, within noise), −20% input tokens** — on codex, session is a *cost* optimization, not a quality lift |
| Session vs native `exec resume`, **codex** (chain1 N=5) | full PromptPilot vs raw-prompt + native codex session | Cost-per-success, input growth | ⚠️ **WITHDRAWN pending re-derivation** — the old "~8.5× cheaper per success; native grows 465k→2.36M" row could not be re-audited (native-arm raw data not located) and the same measurement family was flagged for cache-read artifacts in the 2026-06-09 re-test. Do not cite. See the corrected chain_auth / chain_long sections below for the bounded-vs-native story. |

See [Session Memory](https://github.com/steyangdot/PromptPilot/wiki/Session-Memory) for the full breakdown and the bounded-vs-unbounded mechanism.

## chain_auth (v0.3.1)

The `chain_auth` task seeds a `DigestAuth` secret-ordering bug in `httpx` and fixes it over a 5-turn chain. **Setup:** N=5, claude-code 2.1.163 / codex-cli 0.130.0, agent models claude-opus-4-8 / gpt-5.5, SLMs gpt-5.4-nano (OpenAI API) / gpt-5.4-mini (codex subscription) / claude-haiku-4-5 (Anthropic API / Max). **Metrics: total input tokens per run** (cache-independent — the headline) **and uncached** (input minus cached) — reported separately because uncached rides the provider's cache warmth and isn't reproducible cross-run (see [Measurement Methodology](MEASUREMENT_METHODOLOGY.md)). **End-state across every config below is parity** (`pytest tests/test_auth.py` passing) — the token differences carry no quality tradeoff.

### Session mechanism, isolated (corrected — the old "tool-flip" mostly dissolves)

Holding the SLM constant and isolating the session mechanism (slm_native vs with_session):

| Tool | total (slm_native / with_session) | uncached | Verdict |
|---|---|---|---|
| **claude** | — | **0.67×** | bounded session costs **1.49× more** than native `--resume` — bounded **loses** (unchanged; claude usage was never mis-counted) |
| **codex** | **1.28×** (corrected from the refuted 1.87×) | **0.47× — inverted** (bounded pays ~2× *more* full-price) | bounded is a **modest total-token win**, an **uncached loss** |

The previously-published "~2.8× swing to the opposite verdict" was largely the accounting artifact: the codex arm's native transcript was double-counted (see the [correction](THREAD_CUMULATIVE_USAGE_CORRECTION.md)). The corrected short-chain picture is the same on both tools: **bounding buys a modest total reduction and costs uncached** — a warm native thread re-reads its history at high cache rates, and those cheap reads are what bounding forgoes. Bounding's real payoff appears on **long chains** where the native transcript crosses the compaction threshold (see the chain_long section below).

### Full product (with_session vs vanilla = raw prompt + native resume)

| Config | Metric | Ratio (corrected) |
|---|---|---|
| codex (v2 SLM, clean same-run N=5) | total | **1.34× fewer** (corrected from the refuted 4.19×) |
| codex (v2 SLM, clean same-run N=5) | uncached | **0.48× — INVERTED**: bounding pays ~2× *more* full-price (corrected from the refuted "1.97× fewer") |
| claude (bounded), uncached | uncached | **0.84×** (1.19× costlier — bounded loses; unchanged) |
| claude (rewrite-only, slm_native), uncached | uncached | **1.25× cheaper** (unchanged) |

The old absolute-token table for this run (with_session 1,066,833 vs builtin 4,471,773 total; 201,707 vs 396,534 uncached) is withdrawn — the builtin column was thread-cumulative-inflated; corrected ratios above are from the re-audit ([correction note](THREAD_CUMULATIVE_USAGE_CORRECTION.md)). On short in-window chains the honest summary is: **bounding is a modest total-token win and an uncached loss** on codex; on claude the win is **rewrite-only**: bound nothing, keep native `--resume`.

### Cached vs uncached — two separate metrics, one honest story

Report total and uncached separately — **total** (gross, cache-inclusive) is cache-independent/deterministic and is the headline; **uncached** (full-price) is the observed-cache value (cache-warmth-sensitive, varies cross-run) and is not blended into total — see [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md). Under the corrected accounting, the two metrics tell **opposite** stories for bounding on codex, and both are reported:

- **Total:** bounding wins — **1.34×** on the short chain_auth chains, **~2.4×** on chain_long (compaction regime).
- **Uncached:** bounding **loses or ties** — 0.48× (inverted) on chain_auth; ~parity (0.87–0.95×) on chain_long. The mechanism is structural, not a measurement quirk: a warm resumed thread re-reads its whole history at ~90% cache rates, while every fresh bounded invocation pays its (small) prompt cold. Bounding trades away cheap cache reads to avoid carrying the transcript at all.

The old codex absolute-token table for the v2 run (with_session 1,066,833/201,707 vs builtin 4,471,773/396,534, "→ 4.2× total / 1.97× uncached") and the v1-toolflip confirmation figures (3.81×/1.86×) are **withdrawn** — every builtin-column number was thread-cumulative-inflated ([correction](THREAD_CUMULATIVE_USAGE_CORRECTION.md)); the bounded-arm (with_session) absolutes were recorded correctly.

**Claude** (N=5, per run):

| arm | total tokens fed | cached | uncached | cache-hit |
|---|---|---|---|---|
| with_session (bounded) | 1,300,022 | 1,223,190 | 76,832 | 94% |
| slm_native (native) | 1,122,459 | 1,070,911 | 51,547 | 95% |
| builtin (vanilla) | 1,306,356 | 1,241,764 | 64,592 | 95% |

→ On claude, total tokens fed is ~the same across arms (~1.3M) and bounding reduces **neither** gross nor uncached — which is why claude should keep native `--resume`.

**Mechanism (corrected):** both tools cache at high rates on native resume, and on *short* chains the corrected codex gross (~1.4M) is comparable to claude's (~1.3M) — the previously-claimed "codex balloons to 4.5M by turn 5" was the cumulative double-count. What's real: codex's native transcript **does** grow every turn (append-only `exec resume` semantics), so its total-token cost compounds with chain length and crosses into the compaction regime on long runs — that's where bounding pays (chain_long below). On claude, native `--resume` stays cheap throughout; there's nothing to cap.

### SLM model and transport

- **Transport is agent-uncached-invariant.** Same SLM model → same agent input whether the SLM runs over an API SDK call or a subscription CLI subprocess: claude Haiku via API **67,501** vs via Max subscription **65,608** (~3%, equal). Transport only changes *where* the SLM cost lands ($ on an API key vs quota on a flat subscription) and adds ~20k tokens/call CLI overhead on the subprocess path.
- **SLM model (nano vs mini) is not yet cleanly measured.** Earlier "~1.6×" figures (with_session 1.57×, slm_native 1.71×) compared gpt-5.4-mini vs gpt-5.4-nano *across different runs* with different cache warmth (nano run ~89–94% hit vs mini run ~78–90%) and different normalizers/transport — a cross-run uncached confound, not a model effect. On cache-independent **total** tokens the mini runs actually fed slightly *fewer* tokens, so the data does **not** support "mini is bulkier / nano is terser". This comparison needs a clean interleaved same-run measurement before any token-efficiency claim — currently **unverified**. See [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md).
- **Hybrid beats pure-subscription.** Running the SLM on a cheap API key while the agent runs on the subscription avoids the ~20k tokens/call CLI subprocess overhead the all-subscription path incurs, and gives predictable metered dollars for the SLM layer. (The earlier "nano is terser than mini, so hybrid ~doubles efficiency" justification is dropped — that nano-vs-mini comparison is unverified; see the bullet above.) Default SLM per backend: OpenAI API → gpt-5.4-nano; codex subscription → gpt-5.4-mini; Anthropic API and Max → claude-haiku-4-5.

### The v2 clarify fix (shipped in PR #39)

The v2 `route=clarify` path emitted a human-style multiple-choice clarifying question as the downstream prompt. An autonomous coding agent has no human to answer, so it *answers the question* instead of acting — a silent end-state failure (slm_native v2 scored **2/5**, 60% fail, pre-fix). The mis-fire is **model-specific**: gpt-5.4-nano mis-fired **20/20** on an actionable imperative; gpt-5.4-mini **0/20**; claude-haiku-4-5 **0**. The smallest model over-clarifies.

The fix is a shared `resolve_downstream()` helper in `prpt/core/spec.py`. When `route=clarify` **and** `PROMPTPILOT_AUTONOMOUS=1`, it degrades to `act`: returns the original imperative and resets the stale spec (route/intent→act, scope→localized, memory_record→original). It is called by all three v2 normalizers plus `cli --auto/--dry-run` and the `optimize_prompt` hook. `SYSTEM_JSON_SPEC` was also tightened so clarify fires only for genuine ambiguity about *what* to change, never merely because a file/location is unstated (a repo-access agent can grep). Post-fix end-state is restored to **5/5**, and interactive CLI behavior is unchanged — the degrade is off by default, so a human still sees the clarify question.

### Optimal config (the actionable rule)

- **codex:** default SLM (nano) + **bounded** session + clarify guard (`PROMPTPILOT_AUTONOMOUS=1`) → **~1.34× fewer total tokens** than vanilla on short chains, **~2.4×** on long compaction-regime chains (uncached is parity-to-worse — bounding trades cache reads for a smaller transcript; lead with total).
- **claude:** SLM rewrite + **native `--resume`** (do *not* bound the session) → **~1.25× cheaper** than vanilla.
- Rule of thumb: bound the session on codex **when runs are long/programmatic** (the win scales with chain length); use native resume (rewrite-only) on claude; use the per-backend default SLM; keep the clarify guard on for autonomous/agent use.

Caveats:
- Single workload (`httpx`). Your repo will land somewhere different.
- **The savings are a CLI/automation story.** They come from bounding an ever-growing native transcript across a *multi-turn programmatic* run, so the multiplier scales with session length — agent chains, headless `codex exec` / `claude --resume` loops, CI/batch (~1.34× at 5 turns, ~2.4× at 13 turns in the compaction regime). A single interactive turn has little transcript to bound; expect nothing on a one-shot prompt. (This is also the workload where the SLM should run on a metered API key rather than finite subscription quota — see the dollars caveat below.)
- **Session value is tool-dependent and primarily a *cost* effect:** the original "+60% claude-code success lift" did **not** reproduce — it was a cached-read / phantom-bug artifact, and the clean chain_auth replication shows **end-state parity**. The durable rule: **bound the session on codex** (large cost win), **use native `--resume` on claude**. Don't quote +60% as a result.
- The withdrawn "~8.5× cheaper" (and the analogous claude-code "~3× cheaper than `--resume`") compared *full PromptPilot* (SLM rewrite + bounded session) against a *raw-prompt + native-session* baseline — a product comparison bundling the rewrite and session effects, and its native arm could not be re-audited under the cumulative correction. Treat all per-success-$ multipliers as unverified until re-derived; the corrected chain_auth/chain_long token tables are the citable evidence.
- N=5 success deltas under ~0.2/turn are within the noise floor; cost gaps are the robust signal.
- **Lead with tokens, not dollars.** Tokens are measured directly and are provider-neutral; dollar figures require assuming both API rates and subscription terms (the assumption that made an earlier "$38 vs $0.0085, 4,500× subsidy" framing misleading — it treated finite, flat-fee subscription quota as free). The honest, durable numbers are the token footprints: ~24k SLM tokens directing ~12.66M agent tokens (hybrid split), and ~1.3–2.4× fewer total input tokens than native session (efficiency, regime-dependent — see the correction note). What those tokens cost is downstream: per-token on metered API, or a slice of finite subscription quota (which sustained runs exhaust — we hit the ChatGPT usage limit mid-experiment, May 2026; use the API path for high-volume automation). See [Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode).
- "Success" is judged by an SLM rubric; see [research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py) for the scorer.
- **chain_auth numbers are uncached tokens, N=5; codex dollar figures are notional.** The uncached basis is the corrected one — earlier project numbers over-reported by using gross/cache-inclusive tokens. Provider-reported uncached varies with server-side cache warmth and is not reproducible cross-run, so within-run interleaved comparisons (or cache-independent total tokens) are the reliable basis — see [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md). The cross-experiment nano-vs-mini comparison is **unverified** (cache-warmth + normalizer/transport confounds; needs a clean same-run measurement).

## chain_long — the compaction regime (corrected accounting, N=3, 2026-07-01)

The `chain_long` fixture is a 13-turn dependent chain on `httpx` designed to push a native codex thread past its auto-compaction threshold — the regime long autonomous runs actually live in. Both arms measured with the corrected cumulative-aware accounting (bounded `with_memory` loop vs raw-prompt native `codex exec resume`), end-state scored per contract:

| arm | gross input tokens / run (N=3) | uncached / run | outcome |
|---|---|---|---|
| bounded loop (with_memory + verify gate) | **12.78M** (8.87 / 11.90 / 17.56M) | 1.19M | contract-green **3/3** (one run gate-assisted) |
| native resume (builtin, vanilla) | **30.76M** (30.06 / 29.32 / 32.89M) | 1.13M | contract-green **2/3**; 1/3 shipped a real crash regression (agent-authored kwargs-collision `TypeError`) |

→ **~2.4× fewer total tokens at consistent correctness; uncached ≈ parity (0.87–0.95×, mildly *against* bounding once SLM tokens are counted).** The honest chain_long claim: *the gate-equipped bounded loop delivers consistent contract-green outcomes at ~2.4× fewer total tokens; ungated warm resume is usually fine but shipped a catastrophic regression in 1 of 3 runs — exactly the additive-but-buggy class the verify gate catches.* N=3, one fixture, one tool — a consistency/product story, not a mechanism proof. (This section replaces the withdrawn "9.69×" compaction figure — same artifact as above, corrected to ~1.71× on that older run's data; the N=3 table here is the current measurement.)

## Preservation targets

For compression of bash tool output (separate subsystem from the SLM route decision), the harness is judged on whether critical facts survive — not on raw token reduction.

| Case | What must survive |
|---|---|
| pytest trace | failing test name, exception, file path, top stack frame |
| grep flood | relevant files, matched symbols, line numbers |
| git diff | changed files, behavior changes, risky edits |
| install log | failing package, error code, originating command |

If preservation fails, the correct route is passthrough. A passthrough run costs more tokens but cannot drop a failing test name.

## Interpreting results

Efficiency numbers should always be paired with preservation checks. A run that removes 90% of tokens but drops the failing test name is worse than a passthrough — the agent runs cheaper but has to re-discover the failure.

## What to measure next

- Route accuracy: clarify / answer / passthrough / act, scored against a labelled fixture set.
- Preservation recall for file paths, test names, commands, flags, symbols, stack frames, and explicit constraints.
- Compression ratio **only after** preservation checks pass.
- Cost and latency by provider/model path (Haiku SDK vs Max OAuth vs codex CLI vs OpenAI SDK).
- Regression fixtures that catch unsafe rewrites.

---

**See also:** [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation) · [Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode) · [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay) · [Roadmap](https://github.com/steyangdot/PromptPilot/wiki/Roadmap)
