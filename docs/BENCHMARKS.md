# Benchmarks

PromptPilot measures the SLM harness on two dimensions:

1. **Cost** — does routing through a small model pay for itself?
2. **Preservation** — do critical facts survive the rewrite?

A harness output is not successful just because it is shorter. Token reduction without preservation makes the expensive coding agent cheaper but less informed — usually a net loss.

## Measured results

These numbers come from the in-repo chain harness ([research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py)) against a real target repo (`httpx`). Each row is a specific experiment with a stable identifier so re-runs are reproducible.

| Experiment | Setup | What was measured | Result |
|---|---|---|---|
| SLM control reduces agent token use, **codex** (chain1 N=5) | Full PromptPilot (SLM rewrite + bounded session) vs raw prompt + native `codex exec resume` | Total agent input tokens over the run | **~7.6× fewer agent input tokens (1.11M vs 8.42M)** — the bounded session stops the agent re-ingesting an ever-growing transcript. Confounded by the SLM rewrite: same run as the cost-per-success row below, viewed by raw token count (a product comparison, not an isolated session-mechanism number — see that row for the per-turn growth curve). |
| chain5 codex hybrid | API-key gpt-5.4-nano SLM + ChatGPT-subscription codex LLM | Token footprint by layer (15-turn chain) | **SLM control layer ~24k input tokens vs ~12.66M agent input tokens — the control layer is ~0.2% of the footprint, nearly free to add.** It frames the agent's task (rewrite, route, context-bounding) but doesn't generate the agent's tokens — codex's own loop does. Hybrid routes that 0.2% to cheap metered API, the rest to a flat-fee subscription. (≈ $0.0085 API + ~$38-at-list-rates of agent tokens, but tokens are the measured fact — see caveat.) |
| `gpt-5.4-mini` (codex CLI) vs `gpt-5.4-nano` (SDK) | Each path's cheapest usable model — nano isn't accepted on ChatGPT-auth codex, so this compares *realistic configs*, not one isolated variable; one-off 2-call measurement, not a committed benchmark | Per-call input tokens (dollars downstream) | **Two separable effects: (1) transport — codex's agent loop adds ~19k input tokens of fixed overhead per call, largely model-independent, vs the SDK's prompt-only payload (~400 for a typical call); (2) model — mini lists at 3.75× nano per token. Combined at list rates ≈ ~100× more $/call for the codex path — but that's transport × model, not a clean CLI-vs-SDK figure, and codex-on-subscription is $0 incremental anyway.** |
| Session memory value, **claude-code** (chain1 N=5) | WITH session vs NO session (both SLM-rewritten) | Success rate, cost-per-success | ⚠️ The original "+60% success" was a cached-read / phantom-bug artifact; the clean **chain_auth** replication shows **end-state parity** (see the chain_auth section below). Session value is tool-dependent and primarily a *cost* effect. |
| Session memory value, **codex** (chain1 N=5) | WITH session vs NO session | Success, input tokens | **success tied (1.70 vs 1.90, within noise), −20% input tokens** — on codex, session is a *cost* optimization, not a quality lift |
| Session vs native `exec resume`, **codex** (chain1 N=5) | full PromptPilot vs raw-prompt + native codex session | Cost-per-success, input growth | **~8.5× cheaper per success** at equal quality; native input grows **465k→2.36M** across 5 turns (unbounded transcript) while PromptPilot stays flat ~44k/turn |

See [Session Memory](https://github.com/steyangdot/PromptPilot/wiki/Session-Memory) for the full breakdown and the bounded-vs-unbounded mechanism.

## chain_auth (v0.3.1)

The `chain_auth` task seeds a `DigestAuth` secret-ordering bug in `httpx` and fixes it over a 5-turn chain. **Setup:** N=5, claude-code 2.1.163 / codex-cli 0.130.0, agent models claude-opus-4-8 / gpt-5.5, SLMs gpt-5.4-nano (OpenAI API) / gpt-5.4-mini (codex subscription) / claude-haiku-4-5 (Anthropic API / Max). **Metric: UNCACHED input tokens per run** (input minus cached) — the corrected basis; earlier project numbers over-reported by using gross/cache-inclusive tokens. **End-state across every config below is parity** (`pytest tests/test_auth.py` passing) — the token differences carry no quality tradeoff.

### The tool-flip: bounding the session helps on codex, hurts on claude

Holding the SLM constant and isolating the session mechanism (slm_native vs with_session):

| Tool | slm_native / with_session | Verdict |
|---|---|---|
| **claude** | **0.67×** | bounded session costs **1.49× more** than native `--resume` — bounded **loses** |
| **codex** | **1.87×** | bounded session is **1.87× cheaper** than native — bounded **wins** |

Same code, task, and SLM; a **~2.8× swing** to the opposite verdict. Mechanism (detail in *Cached vs uncached* below): **both tools cache at similar rates** — codex native ~93%, claude ~95% — so codex doesn't cache *worse*. The flip is that (1) codex's transcript **balloons** (its gross grows ~3.6× larger than claude's) and (2) codex's per-turn cache-hit stays flat ~90–93%, so uncached **grows** to ~100k/turn by turn 5; claude's stays small and its per-turn hit **climbs to ~99.5%** by turn 5, so uncached **collapses** to ~1.5k/turn.

### Full product (with_session vs vanilla = raw prompt + native resume)

| Config | with_session | builtin (vanilla) | Ratio |
|---|---|---|---|
| codex (nano SLM, same-run) | 170,264 | 317,079 | **1.86× uncached** (warm) · **3.8× total** |
| claude (bounded) | 76,832 | 64,592 | **0.84×** (1.19× costlier — bounded loses) |
| claude (rewrite-only, slm_native) | 51,548 | 64,592 | **1.25× cheaper** |

The single codex uncached ratio is cache-warmth-dependent (~1.86× warm → ~3.8× cold) — see [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md); a clean v2 same-run ratio is pending.

On claude the win is **rewrite-only**: bound nothing, keep native `--resume`. On codex, bound the session.

### Cached vs uncached — two real reductions

PromptPilot reduces both the **total tokens fed** to the model (gross, including cache-reads) and the **full-price (uncached)** tokens. Both are real reductions. Lead with **total** — it's cache-independent and reproducible. Provider-reported **uncached** varies with server-side cache warmth and is not reproducible cross-run, so it's a range rather than a single number — see [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md).

**Codex** (N=5, per run):

| arm | total tokens fed | cached | uncached (full-price) | cache-hit |
|---|---|---|---|---|
| with_session (bounded) | 1,224,728 | 1,054,464 | **170,264** | 86% |
| slm_native (native) | 5,155,286 | 4,836,633 | 318,652 | 94% |
| builtin (vanilla) | 4,664,957 | 4,347,878 | 317,079 | 93% |

→ On codex, bounding the session feeds the model **~3.8× fewer total tokens** (4.66M → 1.22M). The full-price (uncached) saving is cache-warmth-dependent — **~1.86× (warm cache) to ~3.8× (cold)**, with_session winning throughout — so we don't quote a single uncached ratio; see [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md).

**Claude** (N=5, per run):

| arm | total tokens fed | cached | uncached | cache-hit |
|---|---|---|---|---|
| with_session (bounded) | 1,300,022 | 1,223,190 | 76,832 | 94% |
| slm_native (native) | 1,122,459 | 1,070,911 | 51,547 | 95% |
| builtin (vanilla) | 1,306,356 | 1,241,764 | 64,592 | 95% |

→ On claude, total tokens fed is ~the same across arms (~1.3M) and bounding reduces **neither** gross nor uncached — which is why claude should keep native `--resume`.

**Why the flip (mechanism):** both tools cache at similar rates (codex native ~93%, claude ~95%) — codex isn't worse at caching. The flip is two things: (1) codex's transcript **balloons** (gross grows to ~4.66M vs claude's flat ~1.3M, because codex agents are tool-heavy and `exec resume` re-feeds it all), and (2) codex's per-turn cache-hit stays flat ~90–93% while claude's **climbs toward ~99.5%** by turn 5 (its agent converges to small edits, so uncached collapses to ~1.5k). Bounding the session caps codex's growing transcript; on claude there's nothing to cap.

### SLM model and transport

- **Transport is agent-uncached-invariant.** Same SLM model → same agent input whether the SLM runs over an API SDK call or a subscription CLI subprocess: claude Haiku via API **67,501** vs via Max subscription **65,608** (~3%, equal). Transport only changes *where* the SLM cost lands ($ on an API key vs quota on a flat subscription) and adds ~20k tokens/call CLI overhead on the subprocess path.
- **SLM model (nano vs mini) is not yet cleanly measured.** Earlier "~1.6×" figures (with_session 1.57×, slm_native 1.71×) compared gpt-5.4-mini vs gpt-5.4-nano *across different runs* with different cache warmth (nano run ~89–94% hit vs mini run ~78–90%) and different normalizers/transport — a cross-run uncached confound, not a model effect. On cache-independent **total** tokens the mini runs actually fed slightly *fewer* tokens, so the data does **not** support "mini is bulkier / nano is terser". This comparison needs a clean interleaved same-run measurement before any token-efficiency claim — currently **unverified**. See [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md).
- **Hybrid beats pure-subscription.** Running the SLM on a cheap API key while the agent runs on the subscription avoids the ~20k tokens/call CLI subprocess overhead the all-subscription path incurs, and gives predictable metered dollars for the SLM layer. (The earlier "nano is terser than mini, so hybrid ~doubles efficiency" justification is dropped — that nano-vs-mini comparison is unverified; see the bullet above.) Default SLM per backend: OpenAI API → gpt-5.4-nano; codex subscription → gpt-5.4-mini; Anthropic API and Max → claude-haiku-4-5.

### The v2 clarify fix (shipped in PR #39)

The v2 `route=clarify` path emitted a human-style multiple-choice clarifying question as the downstream prompt. An autonomous coding agent has no human to answer, so it *answers the question* instead of acting — a silent end-state failure (slm_native v2 scored **2/5**, 60% fail, pre-fix). The mis-fire is **model-specific**: gpt-5.4-nano mis-fired **20/20** on an actionable imperative; gpt-5.4-mini **0/20**; claude-haiku-4-5 **0**. The smallest model over-clarifies.

The fix is a shared `resolve_downstream()` helper in `prpt/core/spec.py`. When `route=clarify` **and** `PROMPTPILOT_AUTONOMOUS=1`, it degrades to `act`: returns the original imperative and resets the stale spec (route/intent→act, scope→localized, memory_record→original). It is called by all three v2 normalizers plus `cli --auto/--dry-run` and the `optimize_prompt` hook. `SYSTEM_JSON_SPEC` was also tightened so clarify fires only for genuine ambiguity about *what* to change, never merely because a file/location is unstated (a repo-access agent can grep). Post-fix end-state is restored to **5/5**, and interactive CLI behavior is unchanged — the degrade is off by default, so a human still sees the clarify question.

### Optimal config (the actionable rule)

- **codex:** default SLM (nano) + **bounded** session + clarify guard (`PROMPTPILOT_AUTONOMOUS=1`) → **~3.8× fewer total tokens** than vanilla; uncached savings are cache-warmth-dependent (**~1.86–3.8×**).
- **claude:** SLM rewrite + **native `--resume`** (do *not* bound the session) → **~1.25× cheaper** than vanilla.
- Rule of thumb: bound the session on codex; use native resume (rewrite-only) on claude; use the per-backend default SLM; keep the clarify guard on for autonomous/agent use.

Caveats:
- Single workload (`httpx`). Your repo will land somewhere different.
- **Session value is tool-dependent and primarily a *cost* effect:** the original "+60% claude-code success lift" did **not** reproduce — it was a cached-read / phantom-bug artifact, and the clean chain_auth replication shows **end-state parity**. The durable rule: **bound the session on codex** (large cost win), **use native `--resume` on claude**. Don't quote +60% as a result.
- The "~8.5× cheaper" (and the analogous claude-code "~3× cheaper than `--resume`") compares *full PromptPilot* (SLM rewrite + bounded session) against a *raw-prompt + native-session* baseline — so the ratio bundles the rewrite benefit with the session-mechanism benefit. It's a product comparison, not an isolated session-only number. The transcript-growth curve is the clean session-mechanism evidence.
- N=5 success deltas under ~0.2/turn are within the noise floor; cost gaps are the robust signal.
- **Lead with tokens, not dollars.** Tokens are measured directly and are provider-neutral; dollar figures require assuming both API rates and subscription terms (the assumption that made an earlier "$38 vs $0.0085, 4,500× subsidy" framing misleading — it treated finite, flat-fee subscription quota as free). The honest, durable numbers are the token footprints: ~24k SLM tokens directing ~12.66M agent tokens (hybrid split), and ~7.6× fewer input tokens than native session (efficiency). What those tokens cost is downstream: per-token on metered API, or a slice of finite subscription quota (which sustained runs exhaust — we hit the ChatGPT usage limit mid-experiment, May 2026; use the API path for high-volume automation). See [Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode).
- "Success" is judged by an SLM rubric; see [research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py) for the scorer.
- **chain_auth numbers are uncached tokens, N=5; codex dollar figures are notional.** The uncached basis is the corrected one — earlier project numbers over-reported by using gross/cache-inclusive tokens. Provider-reported uncached varies with server-side cache warmth and is not reproducible cross-run, so within-run interleaved comparisons (or cache-independent total tokens) are the reliable basis — see [MEASUREMENT_METHODOLOGY.md](MEASUREMENT_METHODOLOGY.md). The cross-experiment nano-vs-mini comparison is **unverified** (cache-warmth + normalizer/transport confounds; needs a clean same-run measurement).

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
