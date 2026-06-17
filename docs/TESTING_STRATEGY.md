# Testing Strategy & the Road to the Numbers

How PromptPilot's token-savings numbers are measured, why we report what we do, and the honest journey of corrections that produced the final figure. For the deep mechanics of the cache problem see [Measurement Methodology](MEASUREMENT_METHODOLOGY.md); for the result tables see [Benchmarks](BENCHMARKS.md).

---

## TL;DR

- We benchmark on a **real multi-turn coding chain** (`chain_auth`: a seeded `DigestAuth` bug in `httpx`, fixed over 5 turns), **N=5**, with an **end-state parity gate** (the token comparison only counts when both arms actually pass the tests).
- We report **two separate metrics**, never blended:
  - **Total tokens fed** — deterministic, cache-independent, fully reproducible → **the headline**.
  - **Uncached (full-price) tokens** — cache-sensitive → reported as a **range**, because the provider's cache is non-deterministic and not ours to control.
- **Headline (codex, v2 normalizer, clean same-run N=5):** `with_session` feeds **~4.2× fewer total tokens** than vanilla native resume, at **end-state parity**.
- **Uncached:** **~2× (range ~1.5–3.4×)** depending on provider cache warmth.
- The single most important lesson: **you cannot measure a reproducible "uncached" number** — so we lead with the metric that *is* reproducible (total) and are explicit that uncached is a range.

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

This is the SLM step at the heart of the `with_session` arm — the **v2 normalizer** (`slm-openai-v2`, model **gpt-5.4-nano**, the SLM the benchmark used) turning a terse developer line into a precise, file-grounded, constraint-pinned brief. (The per-run rewrite text isn't stored in the run records, so this is reproduced under the *identical* SLM, model, prompt, repo, and autonomous guard the run used.)

**Raw prompt** — `chain_auth`, turn 1:

> fix the bug in DigestAuth where the digest Authorization 'response' hash is computed from the wrong secret ordering

**v2 rewrite** — what the agent actually receives:

> Implement a fix for DigestAuth in `httpx/_auth.py`: the digest Authorization 'response' hash is currently computed using the wrong secret ordering. Locate where HA1/HA2 and the final response digest are built for Digest authorization, and correct the ordering to match **RFC 7616 / RFC 2069** digest calculation (ensure the final 'response' is computed from the proper components in the proper order).
>
> Also add or update tests to reproduce the bug and verify the corrected digest: find existing DigestAuth tests in `tests/test_auth.py` and `tests/client/test_auth.py`, extend them with a deterministic example (fixed realm, nonce, uri, method, qop if applicable, nc/cnonce, username/password/secret ordering) and assert that the computed Authorization header matches the expected digest 'response'. **Ensure other auth flows remain unchanged.**
>
> `[cwd=C:\projects\httpx; branch=seeded-auth-bug; tests=pytest]`

**Plus the structured decision the SLM emits alongside it:**

| field | value |
|---|---|
| route / intent / scope | `act` / `act` / `pinpoint` |
| target_files | `httpx/_auth.py`, `tests/test_auth.py`, `tests/client/test_auth.py` |
| memory_record (seeds the next turn's bounded session) | *"Fix DigestAuth so the digest Authorization 'response' hash is computed from the correct secret/component ordering, and add/update deterministic tests to validate the Authorization header."* |

So for **~0.2% of the run's tokens**, the SLM added: the **target file**, the **standard to match** (RFC 7616/2069), a **concrete test spec** (a deterministic realm/nonce/uri/qop/nc/cnonce example asserting the expected `response`), a **protected-span guard** ("other auth flows remain unchanged"), repo grounding, and the **one-line `memory_record`** that carries intent into the next turn without re-feeding the transcript. The frontier agent still writes the code — the rewrite just stops it from burning a run on ambiguity. (PromptPilot optimizes for *semantic-preserving context control*, not blind shortening: this rewrite is **longer** than the raw prompt, on purpose.)

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
7. **Final (v2, clean, same-run, N=5, 0 censored, end-state 5/5 both arms):** **4.19× total**, **1.97× uncached** (this run's cache state).

Note how the journey *deflated* an over-optimistic estimate: a cross-run stitch of v2 with_session against the v1 builtin had projected ~4.3–5× total, but the clean same-run measurement landed at **4.19×**. That correction is the point of the discipline.

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
| **codex, v1** (toolflip, same-run N=5) | 3.81× | 1.86× (obs cache) | parity |
| **codex, v2** (clean same-run N=5) | **4.19×** | **1.97×** (range ~1.5–3.4×) | **5/5 both** |
| **claude** (same-run N=5) | ~1.0× (flat) | **1.25×** (rewrite-only; bounding *loses*) | parity |

**How to talk about it:**
- Lead with **total: ~4× fewer tokens fed on codex, at equal quality.** This is the firm, reproducible, fully-ours number.
- For cost/uncached, give the **range** and the reason: *"full-price savings run ~1.5–3.4× depending on how warm the provider keeps the cache — we can't quote a single figure because the cache is the provider's and changes run-to-run, so we lead with total tokens instead."*
- If pushed for "the real cost saving": *"somewhere in that range — we report total tokens precisely so you're not trusting a number that moves every time the provider's cache does."*
- **Claude is different:** native `--resume` already caches history cheaply, so bounding *loses* there; the win is rewrite-only (~1.25× uncached). Bound on codex; keep native resume on claude.

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
