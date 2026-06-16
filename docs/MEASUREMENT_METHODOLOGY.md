# Token-Measurement Methodology: the cache-warmth problem and our fix

**Status:** problem confirmed; doc correction **applied** (PR #41); clean v2 re-measure + optional structural-cache scorer still pending.
**Date:** 2026-06-16
**Scope:** how PromptPilot benchmarks count tokens on codex (and claude), why the "uncached" figure is unreliable, and what we report instead.

---

## TL;DR

- The codex headline **"2.67× fewer uncached tokens"** is **not sound** — it is a *cross-run stitch* of two different runs measured under different provider-cache warmth. The honest same-run number is **1.86×**, and even that slides with cache warmth.
- Root cause: `uncached = input − cached`, and `cached_tokens` is **OpenAI's server-side prefix cache**, which we do not control and which varies run-to-run (warmth, load, TTL). Any metric derived from it inherits that variance.
- **You cannot both "avoid the cache" and keep a cache-discounted number** — a value where `uncached < total` exists *only because of* caching. Force everything cold and `uncached` collapses to `total`.
- **Fix:** report **two separate metrics**, never a blended range. (1) **Total tokens** is the deterministic headline (cache-independent by definition, already measured, reproducible). (2) **Uncached (full-price) tokens** is a *separate*, cache-warmth-sensitive figure reported at the observed cache (single interleaved data point; cross-run it varies). Optionally add a **deterministic structural-cache cost** (we model the cached fraction ourselves from prompt prefixes, instead of reading the provider's number). **Drop provider-`cached`-derived uncached as a headline.**

---

## 1. Problem statement

### 1.1 The trigger

The README/BENCHMARKS codex headline claimed **~3.8× fewer total tokens** and **~2.67× fewer full-price (uncached) tokens** for `with_session` vs `builtin` (vanilla native resume).

Recomputation showed the `2.67×` is:

```
2.67× = builtin uncached 317,079   (from the v1 toolflip run: chain_auth/codex,    hit 93.2%)
        ────────────────────────
        with_session uncached 118,610 (from a DIFFERENT run: chain_auth_v2fix,      hit 89.1%)
```

These two numbers come from **two different runs**. The v2fix run has **no `builtin` arm at all**, so there was never an interleaved pair to divide. The same interleaved run's own ratio is:

```
builtin 317,079 / with_session 170,264 = 1.86×   (chain_auth/codex, same run, interleaved)
total:  4,664,958 / 1,224,729          = 3.81×   (same run)
```

So `3.8× total` is valid (single interleaved run); `2.67× uncached` is a cross-run stitch and should be `1.86×` — and even *that* is not stable (see §1.3).

### 1.2 Root cause: `uncached` rides a server-side variable

`uncached = total_input − cached_tokens`. `cached_tokens` is reported by OpenAI's **prefix cache**, which:

- lives on **their** servers — we cannot inspect, flush, or pin it;
- keys on the exact request prefix, scoped to the org, with a short, undocumented **TTL** (minutes);
- therefore yields **different `cached` for identical content** depending on warmth/load/timing.

Measured proof — the **same** `with_session` config (slm-openai-v2 nano, gpt-5.5, chain_auth, same fixture) read:

| run | total in/run | cached | **uncached** | **cache hit** | tool calls |
|---|---|---|---|---|---|
| chain_auth_v2fix (06-14) | 1,091,256 | 972,646 | **118,610** | **89.1%** | 11.4 |
| chain_auth_mech_codex (06-16) | 908,834 | 690,074 | **218,761** | **75.9%** | 9.7 |

The newer run did **less** work (lower total, fewer tool calls) yet its uncached **doubled** — purely because the provider cache was colder (89% → 76%). Uncached is not reproducible across runs.

### 1.3 Even interleaving doesn't fully fix it

Interleaving guarantees both arms see the *same* warmth at the same wall-clock, so the ratio is fair *at that warmth*. But the two arms have **very different total volume** (builtin ≈ 4.66M, with_session ≈ 1.1M), hence different **cache-elasticity**: builtin is only affordable *because* its huge transcript caches well; with_session has little to cache either way. So the **ratio itself slides with warmth**:

| cache state | builtin uncached | with_session uncached | ratio |
|---|---|---|---|
| warm (builtin 93% / with 86%) | 317k | 170k | **1.86×** |
| cold (≈0% both) | ≈4.66M | ≈1.22M | **3.8×** |

Note the cold endpoint (3.8×) is numerically the *total-token* ratio — which is exactly why we report **total** as its own deterministic metric and **uncached** separately as its observed-cache value, rather than as a single blended range.

A single uncached ratio is just *one point on this line*. `2.67×` and `1.45×` are both reachable from the same code, depending only on which run's warmth you sampled.

### 1.4 Cache-contamination channels in the harness

Three channels are entangled; only the third is production-faithful:

1. **Cross-arm leakage** — `builtin` and `with_session` share the codex system-prompt + tool-definition prefix; whichever runs first warms it for the other.
2. **Cross-run repeats** — the chain replays the **identical** T1–T5 prompts on every N=5 run, so runs 2–5 hit caches warmed by run 1. A real user runs a task **once**; this inflates hit rates above true single-use.
3. **Within-run cross-turn** *(keep this)* — turn 3 reuses turns 1–2's prefix; genuine session continuity, present in production.

### 1.5 The fundamental limit

The cache is server-side. The only levers we hold are **content** (make the prefix unique → guaranteed miss) and **time** (wait past TTL → unreliable, slow). And the decisive fact:

> **`uncached < total` exists *only because of* caching.** You cannot simultaneously "avoid the cache" and report a cache-discounted number. Force every request cold (unique nonce at the front of every turn) and `cached → 0`, so `uncached → total`. There is no separate clean uncached number hiding underneath.

---

## 2. Fix plan

### 2.1 Metric policy

| metric | cache dependence | reproducible | role |
|---|---|---|---|
| **total tokens** | none (caching changes *price*, not *count*) | ✅ | **primary headline** — "context fed" |
| **deterministic structural-cache cost** | modeled by us from prefixes, not the server | ✅ | optional secondary — "$-style cost" |
| provider `cached_tokens` → uncached | **server-side, varies** | ❌ | **drop as a headline** |

- **Lead with total tokens.** It is cache-independent by definition, already logged in every run, and reproducible. Honest framing: *context/volume fed to the model*, not *billed dollars* (total slightly overstates $ because real caching does discount within-session reuse).
- **If a cost figure is wanted, model the cache ourselves** (structural prefix model, §2.4) — never read the provider's `cached`.
- **Stop publishing provider-uncached ratios.** They are the only numbers carrying server variance and are the sole source of the 2.67×/1.86×/1.45× ambiguity.

### 2.2 Experimental-design rules

- **Interleave arms** (run-major: for each run, A then B) so both share warmth — required whenever a cache-dependent metric is reported.
- **Salted per-(arm, run) nonce** at the system-prompt→task boundary *if* measuring within-session cache: `nonce = f(arm, run, batch_salt)`, constant within an arm-run, unique across arm-runs and batches. This kills channels (1) and (2) while preserving (3), and makes interleaving *safe* (no cross-arm sharing even back-to-back).
- **Prefer direct OpenAI API for any cache-isolated cost study.** The Responses API exposes `prompt_cache_key` and `prompt_cache_retention`; Codex CLI does not clearly expose the same cache-domain controls, and its hidden system/tool prefix may remain shared before any user-level nonce.
- **Do NOT de-interleave (block + reset).** We cannot force a provider cache flush, and separating arms into time blocks reintroduces the time-drift confound that caused the 118k-vs-218k blowup.
- **Replication (≥3 batches + 1 non-isolated control) is required ONLY for a cache-dependent metric** — to show the ratio is warmth-invariant and to measure the contamination removed. **It is NOT needed for total tokens** (no cache term ⇒ nothing to sample).
- **N=5 per arm** for *workload* variance (agent doing more/fewer tool calls) — real but cache-unrelated.

### 2.3 Direct OpenAI API cache-isolated option

Use this only for a secondary cost-style study, not for the primary Codex CLI headline. The goal is to measure a cache-dependent number while removing cross-arm and cross-run cache contamination.

**Protocol:**

1. Run the same chain harness through a direct OpenAI API adapter (Responses API preferred) instead of `codex exec`.
2. Choose a model that supports `prompt_cache_retention="in-memory"`; avoid `gpt-5.5` / `gpt-5.5-pro` for this study because the OpenAI docs say those models only support `24h` retention.
3. Set `prompt_cache_retention="in-memory"` on every request.
4. Set `prompt_cache_key` to a fresh value per `(batch, arm, run)`, and keep that value constant for all turns inside that arm-run:

   ```text
   promptpilot:chain_auth:v2:{batch_salt}:{arm}:run{run_idx}
   ```

5. Keep the arm-run's prompt prefix stable across turns, except for the normal session growth. This preserves the production-faithful within-run cache channel while preventing one arm/run from reusing another arm/run's provider cache.
6. Run paired, run-major interleaving, preferably with alternating or randomized arm order per run (`AB`, `BA`, ...), and repeat across at least 3 independent batch salts.
7. Report this as an **API cache-isolated cost study**, separate from the Codex CLI total-token benchmark.

**What this proves:** the direct API path can estimate a reproducible cache-aware cost profile under controlled cache domains. It does not prove Codex CLI has the same cost profile, because Codex CLI may add hidden system/tool prefixes and may not expose `prompt_cache_key` or retention controls.

**What to log per request:**

- model, `prompt_cache_key`, and `prompt_cache_retention`;
- arm, run, batch salt, turn, and arm order;
- total input tokens, cached tokens, output tokens, tool calls, wall time;
- exact prompt snapshot or a stable prompt hash for structural-cache backfill.

### 2.4 Deterministic structural-cache model (optional secondary)

Compute the cached fraction from the **prompts themselves**, assuming an idealized never-evict prefix cache: per turn, `structural_cached =` longest common prefix with the prior request in that arm-run's lineage; `uncached* = total − structural_cached`. Reproducible (depends only on content), server-independent, and validateable against observed warm-run hits. Caveat: needs exact per-turn prompt logs — available for `with_session`/`mech_session`; `builtin`'s native-resume packing is harder to reconstruct exactly.

### 2.5 Concrete next steps

1. **Clean v2 total ratio:** one interleaved v2 run, `[builtin, with_session]`, N=5, on the existing fixture (seeded-auth-bug @ d764bfc) → publishable v2 **total** ratio (cache-free; no nonce/replication needed for total).
2. **(Optional) direct API cache-isolated study:** if we still want an observed uncached/cost ratio, run the direct OpenAI API protocol in §2.3. Do not present it as Codex CLI billing behavior.
3. **(Optional) structural-cache scorer:** implement §2.4 and backfill across existing runs for a reproducible cost figure.
4. **(Optional) Codex CLI cache-dependent diagnostic:** only if needed, do salted-nonce interleaved + ≥3 batches + 1 non-isolated control, per §2.2, and label it diagnostic because hidden Codex prefixes may remain shared.
5. **Correct the docs:** README headline + BENCHMARKS table/summary/optimal-config + HYBRID_MODE — replace the codex **2.67× uncached** with **total tokens (~3.8–4.3×)** as the headline; remove/relabel the cross-run uncached stitches (incl. the mini-vs-nano "1.57×/1.71×", which are the same defect). Claude's 1.25× is from a single interleaved run and is unaffected.

---

## 3. Corrected numbers (what to publish)

- **Codex:** report **two separate metrics**, never a blended range. (1) **Total tokens** = the headline: `with_session` feeds **~3.8× fewer total tokens** (deterministic, cache-free) than vanilla native resume (v1 same-run: 4,664,958 → 1,224,729). (2) **Uncached tokens** = a separate, cache-warmth-sensitive figure: **~1.86×** fewer (317,079 → 170,264, observed ~90% cache; single interleaved data point — cross-run it varies), or use the structural model. v2 feeds even less total (with_session ≈ 0.9–1.09M across two runs) — a clean v2 total ratio awaits the §2.5(1) run. **Do not** blend these two into a single range.
- **Claude:** unchanged — `slm_native`/`builtin` ≈ **1.25× fewer uncached**, from one interleaved run (`chain_auth/claude-code`), all arms ~95% hit. Same-run, not a stitch.

---

## 4. Evidence / provenance

Run data under `B:/LLM/_session_retest_2026-06-07/`:

- `chain_auth/codex/chain_auth/` — v1 toolflip, **interleaved** builtin+slm_native+with_session, N=5. builtin 317,079 / slm_native 318,653 / with_session 170,264 (uncached/run); totals 4.66M / 5.16M / 1.22M; hits 93.2% / 93.8% / 86.1%. → same-run builtin/with = **1.86× uncached, 3.81× total**.
- `chain_auth_v2fix/codex/chain_auth/` — v2, with_session 118,610 @ 89.1% + slm_native 190,578; **no builtin arm**.
- `chain_auth_mech_codex/codex/chain_auth/` — v2, with_session 218,761 @ 75.9% + mech_session 278,054 @ 77.7% (interleaved). → cache-warmth proof + mech-vs-SLM 1.27× uncached / 1.37× total.
- `chain_auth/claude-code/chain_auth/` — claude, interleaved, builtin 64,592 / slm_native 51,548 / with_session 76,832; hits ~95%.

Related memory: `chain_auth_mech_vs_slm_session.md` (cache-warmth gotcha re-confirmed), `audit_uncached_timeout_bug.md` (the censored-turn cousin), `chain_auth_toolflip_clean.md` (the 1.86× canonical run).

---

## 5. Open decisions

- [ ] Launch the single interleaved v2 run for the clean v2 **total** ratio? (§2.5-1)
- [ ] Build the direct OpenAI API cache-isolated study for observed cache-aware cost? (§2.3 / §2.5-2)
- [ ] Build the deterministic structural-cache scorer for a reproducible cost figure? (§2.4 / §2.5-3)
- [x] Apply the doc corrections to README/BENCHMARKS/HYBRID_MODE (§2.5-5) — **done in PR #41** (total-led; total and uncached as two separate metrics; nano-vs-mini marked unverified).
