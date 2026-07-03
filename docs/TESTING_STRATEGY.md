# Testing Strategy & the Road to the Numbers

How PromptPilot's token-savings numbers are measured, why we report what we do, and the honest journey of corrections that produced the final figure. For the deep mechanics of the cache problem see [Measurement Methodology](MEASUREMENT_METHODOLOGY.md); for the result tables see [Benchmarks](BENCHMARKS.md).

---

## TL;DR

- We benchmark on a **real multi-turn coding chain** (`chain_auth`: a seeded `DigestAuth` bug in `httpx`, fixed over 5 turns), **N=5**, with an **end-state parity gate** (the token comparison only counts when both arms actually pass the tests).
- We report **two separate metrics**, never blended:
  - **Total tokens fed** — deterministic, cache-independent, fully reproducible → **the headline**.
  - **Uncached (full-price) tokens** — cache-sensitive → reported as a **range**, because the provider's cache is non-deterministic and not ours to control.
- **Headline (codex, corrected 2026-07-01):** `with_session` feeds **~1.34× fewer total tokens** than vanilla native resume on the short chain_auth chains, **~2.4×** on 13-turn compaction-regime chains — at **end-state parity**. (The originally-published **4.19× / 1.97×** was inflated by the thread-cumulative accounting bug — step 8 below.)
- **Uncached:** **parity-to-inverted for bounding** — a warm native thread's history re-reads are ~90% cached, and bounding forgoes exactly those cheap reads. Never lead with uncached.
- The two most important lessons: **you cannot measure a reproducible "uncached" number** (so lead with total), and **you must know your counter's semantics** — codex switched resumed-thread usage to thread-cumulative reporting mid-project and silently double-counted every native arm (step 8).

---

## 1. What we measure

The chain harness ([research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py)) runs the same multi-turn coding task under three arms and compares them:

| arm | what it is |
|---|---|
| **with_session** | SLM rewrite + PromptPilot's **bounded** session (the product) |
| **builtin** (vanilla) | raw prompt + the tool's **native** session (`codex exec resume` / `claude --resume`) |
| **slm_native** | SLM rewrite + native session (isolates the rewrite from the bounding) |

Two token metrics, kept strictly separate:

- **Total tokens fed** = every token sent to the model per run (`input_tokens`, including cache-reads). Caching changes the *price* of a token, never the *count* — so this number is **deterministic and reproducible**.
- **Uncached (full-price) tokens** = `total − cached`, i.e. the tokens the provider billed at full rate. This depends on the provider's cache and is **not reproducible** (see §3).

And a **quality gate**: **end-state parity**. After each run we run the repo's auth test suite (`pytest tests/test_auth.py`). A token comparison is only valid when **both arms pass** — otherwise we'd be comparing a cheaper-but-broken arm to a working one. Every published comparison is at parity.

---

## 1b. What the v2 rewrite actually produced

This is the SLM step at the heart of the `with_session` arm — the **v2 normalizer** (`slm-openai-v2`, model **gpt-5.4-nano**). The record below is **verbatim from the raw normalizer log** (`~/.promptpilot/v2_slm_raw.jsonl`, captured with `PROMPTPILOT_V2_RAW_LOG=1`): timestamp `2026-06-17T05:59:47Z`, **9,876 input / 303 output tokens** — the input is mostly the repo context the SLM read; the 303-token output *is* the spec (the "~0.2% control layer", made concrete).

**Raw prompt** — the `<developer_prompt>` fed to the SLM:

> fix the bug in DigestAuth where the digest Authorization 'response' hash is computed from the wrong secret ordering

**The `downstream_prompt` the SLM emitted — what the agent receives, verbatim:**

> Fix DigestAuth in httpx/_auth.py so that the digest Authorization header computes the `response` hash using the correct secret ordering (i.e., correct HA2/HA1 inputs and the proper placement of the precomputed secrets per the RFC algorithm). Locate the current DigestAuth implementation, identify where the `response` digest is calculated, and correct the ordering/combination so it matches the expected digest formula for username/password vs any precomputed values. Update/extend tests to cover this bug by adding a failing test case in tests/test_auth.py and/or tests/client/test_auth.py that asserts the exact Digest Authorization header (or computed response hash) matches the expected value for a known challenge/nonce/opaque/qop/uri/method setup. Ensure existing DigestAuth behavior remains unchanged except for the corrected hash. Run the relevant test suite(s).

**The rest of the `ExecutionSpec` it emitted (same JSON object):**

| field | value |
|---|---|
| route / intent / scope | `act` / `act` / `localized` |
| risk | `high` |
| target_files | `httpx/_auth.py`, `tests/test_auth.py`, `tests/client/test_auth.py` |
| memory_record (seeds the next turn's bounded session) | *"Correct DigestAuth's digest `response` hash computation in httpx/_auth.py by fixing the wrong secret ordering, and add/adjust tests to assert the correct Authorization header output."* |

So for **303 output tokens** (the ~0.2% control layer), the SLM turned one terse line into: the **target files** (`httpx/_auth.py` + both test suites), a **`risk: high`** flag, a **concrete test spec** (assert the exact Authorization header for a known challenge/nonce/qop/uri/method setup), a **protected-span guard** ("existing DigestAuth behavior remains unchanged except for the corrected hash"), and the one-line **`memory_record`** that carries intent into the next turn without re-feeding the transcript. The frontier agent still writes the code — the rewrite just stops it burning a run on ambiguity. (PromptPilot optimizes for *semantic-preserving context control*, not blind shortening: this brief is **longer** than the raw prompt, on purpose.)

> Provenance: verbatim from `~/.promptpilot/v2_slm_raw.jsonl` (the `PROMPTPILOT_V2_RAW_LOG` capture) — an auditable artifact, not a reconstruction. At runtime a short repo-grounding suffix (`[cwd=…; branch=…; tests=pytest]`) is appended before the brief is forwarded.

---

## 2. Experimental design (the rules, and why)

- **Interleave the arms** (run-major: for each run, builtin then with_session, back-to-back). This is a **matched-pairs** design: both arms hit the *same* provider state (cache warmth, load) at the *same* moment, so those nuisance factors **cancel in the ratio**. Running one arm's five runs first and the other's later (a "blocked" design) would let conditions drift *between* the blocks and contaminate the comparison.
- **N=5 per arm** to average out **workload variance** — the agent stochastically does more/fewer tool calls run-to-run. (This is unrelated to caching.)
- **Reset the fixture every run** (`git reset --hard` to the seeded-bug commit) so each run starts from an identical state.
- **End-state parity gate** (above).
- **Total tokens is the headline; uncached is a range.** See §3 for the reason this isn't a choice but a necessity.

---

## 3. The cache-warmth problem (why uncached can't be pinned down)

"Uncached" = `total − cached`, and `cached` is reported by **OpenAI's server-side prefix cache** — which we cannot inspect, flush, or pin, and which varies run-to-run with server load, routing, and a short expiry. We verified this is **non-deterministic**, not just noisy:

- The **same prompt** cached **94% one moment and 0% the next**.
- The **same benchmark config** read **118k full-price tokens at one cache state and 219k at another** — nearly double, for identical work, purely because the cache was colder.

A key asymmetry makes this unfixable by clever setup: **prompt content gives only one-directional control over the cache.** A *unique* prefix reliably *forces a miss* (nothing to match) — so we can isolate runs from each other. But an *identical* prefix only makes a hit *possible*; whether it's realized, and how much, is the provider's best-effort decision. We control whether the cache is *allowed*, not how *generous* it is when allowed. Therefore:

> A deterministic "uncached" number below total cannot exist — any discount below total requires realized cache hits, and realized hits are non-deterministic. Force every request cold and `uncached` simply collapses to `total`.

So we lead with **total** (the only fully-reproducible, fully-ours number) and report uncached as an **observed value + range**. Full treatment in [Measurement Methodology](MEASUREMENT_METHODOLOGY.md).

---

## 4. The journey to the number

The honest chronology — each step corrected the last:

1. **`2.67× fewer uncached` (wrong — a cross-run stitch).** The original codex headline divided `builtin 317,079` (from one run) by `with_session 118,610` (from a *different* run that had no builtin arm to pair against). Two runs, two different cache-warmths — not a real ratio.
2. **`1.86× uncached` (the honest same-run v1 number).** Recomputing from the *one* run that had builtin + with_session **interleaved** gave 1.86× uncached / **3.81× total**.
3. **Even 1.86× isn't reproducible.** Investigating *why* the cross-run numbers disagreed surfaced the cache non-determinism (§3). A direct API probe confirmed identical inputs cache 0%↔94%. Conclusion: *no* uncached number is a stable point.
4. **Pivot: lead with total tokens.** Total has no cache term, so it's deterministic. The v1 codex headline became **~3.8× fewer total tokens**, with uncached demoted to a cache-sensitive footnote.
5. **Split the metrics.** We separated the previously-blended "1.86×–3.8× range" into two clearly-labeled numbers: **total (deterministic)** and **uncached (cache-sensitive)** — never a single fuzzy band.
6. **The clean v2 total run.** The published 3.8× used the *v1* SLM; the product ships **v2**. To get a defensible v2 number we ran a fresh interleaved `builtin + with_session` run, N=5, on the v2 normalizer. (It hit a detour: the codex *desktop app* silently rewrote `~/.codex/config.toml` with an invalid `service_tier`, which made every `codex exec` fail with zero tokens — a pure environment bug, fixed, and the run repeated cleanly.)
7. **The v2 run (N=5, 0 censored, end-state 5/5 both arms) published as:** **4.19× total**, **1.97× uncached** (this run's cache state).
8. **The thread-cumulative correction (2026-07-01) — the largest correction of the journey.** A 7-agent adversarial audit of a newer benchmark found codex CLI builds since ~2026-06-14 report **thread-cumulative** usage on resumed threads (turn *N*'s counter covers turns 1..*N*); summing per-turn readings double-counted every native arm by ≈ ×(N+1)/2. Re-running the naive method *reproduced every published number exactly*, proving they were the artifact. Corrected: **4.19× total → 1.34×; 1.97× uncached → 0.48× (inverted — bounding pays more full-price than warm resume)**. The fix (per-turn deltas of the cumulative counter, chain-wide sticky mode, version-robust fallback) is documented with tests in [Thread-Cumulative Usage Correction](THREAD_CUMULATIVE_USAGE_CORRECTION.md).

Note how the journey keeps *deflating* over-optimistic numbers: cross-run stitch → same-run → total-led → and finally the cumulative correction cutting the headline by ~3×. Each deflation came from auditing our own most-cited claim. That correction being the loudest item on this page is the point of the discipline.

---

## 5. Approaches we considered and rejected

Several intuitive "fixes" for the cache problem make it **worse** — each is recorded so the design choices are auditable:

| Idea | Why it fails |
|---|---|
| **De-interleave (run all builtin, cooldown, then all with_session)** | Separates the arms in *time* → conditions drift between blocks → reintroduces the very confound interleaving removes. And you can't force a provider cache flush anyway. |
| **One arm per account** | Cache is org-scoped, so this confounds **org with arm** — any difference could be org A vs org B, not the session strategy. |
| **Two *freshly created* accounts** | Fixes the cold/warm-*history* asymmetry but is still **unpaired**: each arm sees its own independent cache domain whose warmth can't be synchronized. Matches the *start*, not the *trajectory*; can even flip the result's sign. |
| **Run builtin, wait 24h past cache retention, then with_session** | De-interleaving on a *24-hour* axis = the maximal time-drift confound (config/model/quota can all shift — our config bug appeared within 12h). Also fixes a non-problem (different prefixes don't share cache anyway) and still leaves uncached non-deterministic. |
| **Direct-API nonce isolation** (build an agent loop on the Responses API, salt each lineage) | The front-nonce isolation *works*, but the API cache is itself best-effort (0%↔94%), so it doesn't yield a reproducible uncached — and it measures a *proxy* path, not the Codex CLI the product runs on. |

The throughline: **the non-determinism lives in the provider, not in our setup.** No experimental trick converts uncached into a clean number; the metric itself is the limitation.

---

## 6. The final numbers & how to cite them

| | total tokens | uncached (full-price) | end-state |
|---|---|---|---|
| **codex, v1** (toolflip, same-run N=5, corrected) | 1.28× | 0.47× (inverted) | parity |
| **codex, v2** (clean same-run N=5, corrected) | **1.34×** | **0.48× (inverted)** | **5/5 both** |
| **codex, chain_long** (compaction regime, N=3, cumulative-aware) | **2.41×** (12.78M vs 30.76M) | ~0.87–0.95× (parity) | bounded 3/3 green; native 2/3 + 1 catastrophic |
| **claude** (same-run N=5, never affected) | ~1.0× (flat) | **1.25×** (rewrite-only; bounding *loses*) | parity |

**How to talk about it:**
- Lead with **total: ~1.3× fewer tokens fed on codex on short chains, ~2.4× on long compaction-regime chains, at equal quality.** The multiplier scales with chain length because the native transcript grows every turn.
- For uncached, say it straight: *"bounding does not save full-price tokens — a warm native thread re-reads its history at ~90% cache rates, and bounding gives those cheap reads up. The win is total volume (and, on long chains, consistency), not uncached."*
- Cite the correction when quoting anything pre-2026-07: the old 4.19×/1.97×/9.69× figures are refuted — [Thread-Cumulative Usage Correction](THREAD_CUMULATIVE_USAGE_CORRECTION.md).
- **Claude is different:** native `--resume` already caches history cheaply, so bounding *loses* there; the win is rewrite-only (~1.25× uncached). Bound on codex for long/programmatic runs; keep native resume on claude.

---

## 7. Reproduce

```
# codex, v2, interleaved builtin + with_session, N=5
research/session_isolation_experiment.py --runs 5 --tool codex \
    --normalizer slm-openai-v2 --chain chain_auth --arms builtin,with_session
# then:
research/score_endstate.py <out> --tool=codex --arms builtin,with_session
```

Data for the figures above lives under `_session_retest_2026-06-07/` (`chain_auth/codex` = v1 toolflip; `chain_auth_v2total_codex` = the clean v2 run). Metric basis: total + uncached input tokens/run; end-state = `pytest tests/test_auth.py` passing.
