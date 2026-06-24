# Bounded-Session Continuity — Finding, Smoking Gun, and Memory-Architecture Direction

**Date:** 2026-06-19. **Source run:** clean N=5 compaction-regime test (`chain_long`, codex, gpt-5.5).
**Status:** finding confirmed (forensics + human eyeball); architecture direction proposed, not yet built.

---

## 1. Problem statement

PromptPilot's value proposition is an **SLM control layer**: a small model (gpt-5.4-nano) that, per turn, (a) rewrites the prompt and (b) maintains an SLM-directed **bounded session**, so a long agent job runs on far fewer tokens than native context management — *at quality parity*.

The published **4.19×** headline was measured entirely **sub-threshold** — small sessions where native codex never auto-compacts. The market that matters (long autonomous agent runs) lives **above** the threshold, where native codex's auto-compaction engages (~233k per-call occupancy). Two open questions:

1. Does the token win **survive above the threshold**, once native is compacting?
2. At what **continuity (quality) cost** — does the bounded session preserve cross-turn dependencies as well as native's full-transcript-with-compaction?

---

## 2. Test finding (clean N=5)

13-turn dependent chain on httpx; `with_session` (full PromptPilot: SLM rewrite + bounded session) vs `builtin` (raw prompt + native `codex exec resume`); N=5; gpt-5.5, reasoning_effort=xhigh.

**Tokens — THESIS HOLDS, decisively.**
- **Cumulative 9.69×** (builtin 602,086,843 / with_session 62,148,057 gross input tokens).
- **Marginal in-regime 13.59×, 95% CI [10.73, 16.46]** (the PRIMARY metric).
- **5/5 builtin runs fired compaction → regime CONFIRMED**, 0 censored. (Supersedes the provisional N=2 5.48×, which undercounted native via a 300s cap + a bounded thrash-outlier.)

**Continuity (H4) — near-parity with a small, *real*, asymmetric tax.**
- LLM judge reported builtin **0.26** vs with_session **0.134** (~2× favoring native).
- An 11-agent forensic sweep + a human eyeball of all 10 end-state diffs found the **2× magnitude is overstated**: builtin **30/30** feature-cells present; with_session **27/30** (3 partial, **0 absent**, 3/5 runs flawless); the continuity-critical ResilienceConfig refactor landed in **both** arms.
- **But the residual is real:** bounded had **2 genuine continuity defects in 2/5 runs vs 0/5 native** (both confirmed by direct diff inspection):
  - **run3** — T1–T3 per-request `connect_timeout=`/`read_timeout=` kwargs were consolidated into ResilienceConfig at T13, but the T1–T3 *tests* calling those kwargs were left orphaned → **TypeError** in the reconstructed end state.
  - **run4** — T4–T5 Retry-After **header parsing** (delta-seconds + HTTP-date) was dropped at the T12 refactor; only the T6 max-delay *cap* survived, untied to any parsed header.

**Honest headline:** *Once compaction engages, PromptPilot's bounded session runs the 13-turn task on **~9.7× fewer tokens (13.6× marginal in-regime)** at **near-parity quality**, with a **small, verified continuity tax** (a genuine defect in 2/5 bounded runs vs 0/5 native).*

---

## 3. The smoking gun — *why* the continuity tax happens

The tax is **not** mysterious model behavior. It is a direct, mechanical consequence of how the bounded session is assembled, in `prpt/session.py`:

```python
MAX_TURNS         = 4     # line 12  — only the last 4 user+assistant PAIRS are surfaced
ORGANIC_TURN_TRUNC = 300  # line 24  — each surfaced turn is truncated to 300 chars
...
recent = messages[-(MAX_TURNS * 2):]                 # line 59 — hard sliding-window cutoff
return ["{0}: {1}".format(r.upper(), c[:300] ...)]   # line 61 — per-turn truncation
```

And `prepare_with_session` runs a **fresh `codex exec` every turn** (no native transcript), so the **only** cross-turn memory the agent has is this 4-turn / 300-char window (plus whatever it re-reads from the repo).

**Window arithmetic on a 13-turn chain (refactor at T12–T13):** at turn T the window covers turns `(T−4)…(T−1)`:

| | window (MAX_TURNS=4) |
|---|---|
| at **T12** | T8–T11 |
| at **T13** | T9–T12 |

So by the refactor, **T1–T5 are completely gone from memory.** That is exactly where both defects live:
- **run3 (T1–T3):** at T13 the agent migrates the clients to ResilienceConfig with **zero memory** of T1–T3's kwarg tests → orphans them.
- **run4 (T4–T5):** at T12 the agent rewrites the retry path with **zero memory** of T4–T5's Retry-After parsing → drops it.

**The code comment is the smoking gun.** `MAX_TURNS=4` was explicitly calibrated for **"5-turn referential chains"** (session.py:13–16). We ran **13**. The window is mis-sized for the chain — the continuity tax is a *consequence of the window*, not a property of bounding.

**Contrast — why native scored 0/5:** native re-feeds the *full* transcript, compacting only at ~233k (model-generated summary). So at T13 it still "sees" T1–T5 and carries them forward correctly. Native does **compress-don't-drop**; prpt does **drop-early**.

*(Diff evidence: run3 — `connect_timeout`/`read_timeout` appear only as `_config.py` dataclass fields; **no** request-method signature change in `_client.py`; tests still call `client.get(..., connect_timeout=0.5)`. run4 — 23 retry-related diff lines, all about the max-delay cap, **zero** about header parsing; `new_files` is only `_stats.py`, so the parsing isn't hidden in an untracked test file.)*

---

## 4. Why a bigger window does NOT fix it (the fundamental problem)

Raising `MAX_TURNS` (4→8→12) **moves the cliff; it does not remove it.**

- Any fixed window of size **N**, on a job of length **M**, drops **M−N** turns. For M=13, N=8 *felt* close. For **M = 100 or 1000** — a realistic long-autonomous-agent job — **N=8 (or 50, or 200) is rounding error.** Turn 1000's refactor can reference turn 7's contract, and no recency window will ever hold it.
- **Recency windowing structurally cannot provide arbitrary-distance continuity.** "What MAX_TURNS" is the wrong question.
- A quirk worth noting: the **token win comes from the 300-char truncation + fresh-exec, not the turn count** — each extra windowed turn is only ~300 chars (~75 tokens). So bumping MAX_TURNS is nearly free on tokens but still doesn't scale on continuity. The win and the tax are two ends of the *recency* dial, and **no fixed setting of that dial satisfies both at scale.**
- **Native is not a free lunch at scale either.** Its compaction is also lossy — at 1000 turns it has compacted many times and early detail fades there too. So a long-horizon job stresses **both** current approaches (window *and* compaction). The genuinely scalable mechanism is neither.

---

## 5. The fix — a memory *system* (contracts + artifacts + guarded recall)

> Sharpened by an independent review (`SESSION_MEMORY_FIX_RECOMMENDATION.md`, codex worktree 0257), incorporated here.

The solution is a **memory system**, not a smarter window. Key reframe: for *code* work the highest-value memory is **not general prose history — it is durable contracts tied to files, tests, symbols, and APIs.** run3 and run4 weren't missing conversation snippets; they were **missing obligations** (an API/test contract; a feature contract). The architecture should remember *obligations*, not chat.

**Slogan: compress the state, retrieve the evidence, verify against artifacts.**

Four bounded components:

**1. Bounded ProjectState Ledger (structured, not prose).** One fixed-budget *structured* state doc per session, SLM-updated each turn: active contracts, public-API obligations, tests added/modified, files-per-feature, symbols/params introduced, refactors-in-progress, unresolved hazards. Compress-don't-drop — a contract is never dropped merely for being old. Example entry:

```text
F001 timeout-overrides:
  Contract:  sync+async client request APIs accept connect_timeout/read_timeout.
  Artifacts: httpx/_client.py, httpx/_config.py,
             tests/client/test_client.py, tests/client/test_async_client.py
  Guard:     any ResilienceConfig migration MUST preserve these kwargs OR
             intentionally migrate the tests + public-API expectations.
```

**2. Artifact Map (machine-readable `feature → files/tests/symbols`).** A queryable index that tells the agent *which files and tests must be re-read* when a later change touches a feature — the layer that makes memory artifact-grounded instead of trusting a lossy prose summary.

```json
{ "timeout-overrides": { "files": ["httpx/_client.py","httpx/_config.py"],
    "tests": ["tests/client/test_client.py","tests/client/test_async_client.py"],
    "symbols": ["connect_timeout","read_timeout","ResilienceConfig"] } }
```

**3. Refactor Guard (the standout — trigger-/overlap-activated recall).** Before any turn that *refactors, migrates, consolidates, replaces, or unifies*, force a recall step: identify impacted files/symbols → retrieve every contract tied to them → list the tests + public-API call-sites that must be preserved-or-intentionally-migrated → prepend a compact checklist to the downstream prompt. **Both confirmed defects happened at refactor turns (T12–T13)** — this aims a deterministic guard exactly there. *(Refinement: don't gate solely on trigger keywords — a refactor phrased "change the clients to use one config" has none. Also fire when the turn's impacted files/symbols overlap an existing contract.)*

**4. Targeted hybrid retrieval (sequenced *after* the MVP).** Then add retrieval driven by the current task + the artifact map — **hybrid, not embeddings-alone**: code-symbol/lexical matches (`connect_timeout`, `Retry-After`), file-overlap from the artifact map, refactor-trigger terms, *and* semantic similarity. Return exact prior turn records + relevant ledger entries + artifact pointers — never a blind transcript replay. This is the distance-independent layer that ultimately scales to 1000-turn jobs; it's deferred only because it's the fuzziest to build/evaluate, not because it's optional.

**All four stay bounded → the ~9.7× token win survives** (the win comes from *not* re-feeding the full transcript; a structured ledger + targeted recall is tiny per turn). Marginal cost ≈ one SLM update/turn — which prpt already pays for the rewrite.

**New failure surface to watch (added in review):** the ledger only helps if the SLM *reliably extracts* contracts each turn. This relocates the problem from "the window drops contracts" to "the SLM must populate the ledger correctly" — a *better* problem (structured, testable) but not free; it needs its own extraction-accuracy eval. Artifact-grounding (forcing a re-read of listed files when a contract activates) is the backstop against a stale or wrong ledger entry.

---

## 6. PromptPilot's existing primitives + the gap

prpt is partway there:

- **HAS** `load_all_turns()` (session.py:68 — full transcript, untruncated, no TTL filter) and `append_synth_turn()` (session.py:110 — a compressed "carried state" turn stored untruncated). These power the handoff/checkpoint flow that already Haiku-summarizes a full transcript into one synth turn.
- **The chain harness's `with_session` doesn't use them** — it uses the naive `load_recent_turns()` last-4 window.
- **MISSING entirely: retrieval.** No index, no relevance recall.

So the rolling-state layer is mostly a *wiring* job against existing primitives (fold each turn's `memory_record` into a synth "state" turn via an SLM call, cap it to a token budget, surface it via `append_synth_turn`/`load_all_turns` instead of the window). The retrieval layer is genuinely new — and it's the strategic differentiator: **codex compacts but doesn't retrieve**, so a retrieval-augmented bounded session could beat native on long-horizon continuity *while keeping the token win*.

---

## 7. MVP, acceptance criteria, and risks

**Not** a `MAX_TURNS` sweep — that only confirms the cliff. Build the memory system, MVP-first.

**MVP (these three before broad semantic retrieval):**
1. **ProjectState Ledger** (§5.1)
2. **FeatureMap artifact index** (§5.2)
3. **Refactor-triggered recall** (§5.3)

Rationale: broad semantic retrieval is the fuzziest/noisiest/hardest-to-eval layer; the two *confirmed* defects (run3, run4) are deterministically covered by ledger + map + refactor-guard alone. This MVP is still "retrieval" — just over a **structured curated index**, not embeddings over raw transcript. Mostly a *wiring* job against `load_all_turns`/`append_synth_turn`; only the FeatureMap + guard are net-new.

**MVP per-turn prompt assembly:** current request + bounded ledger + relevant feature-map entries + refactor-guard checklist (when triggered) + top-k prior records *only* when activated by feature/symbol/file overlap.

**Acceptance criteria** — the fix counts *only* if it improves continuity **without** giving back the token win:
- Preserve a large gross-token advantage over native transcript replay (target: stay ~9×-class).
- Per-turn memory payload stays bounded (fixed budget *per layer*).
- On a 30–50-turn chain, recall contracts **older than any recency window**.
- During late refactors, surface the tests/API obligations tied to earlier features.
- Drive the per-run continuity-defect rate from ~2/5 toward **0/5** (kill the run3 orphaned-kwarg and run4 lost-Retry-After classes).

**Risks & mitigations:**
| Risk | Mitigation |
|---|---|
| **State drift** — ledger compresses away a detail | retrieve exact prior evidence; point the agent back to artifacts |
| **Retrieval noise** — semantic recall brings junk | rank by file/symbol overlap + refactor triggers, not embeddings alone |
| **False confidence** — a contract claims a feature the code has since changed | artifact-grounding: require re-reading the listed files/tests when a contract activates |
| **Token creep** — state + map + retrieval quietly expand context | enforce a fixed token budget per layer |
| **Ledger-extraction miss** — SLM fails to record a contract | extraction-accuracy eval (the new failure surface, §5); artifact re-read as backstop |

**Validation chain:** test on a deliberately longer, **forward-referential 30–50-turn fixture** that *guarantees* deep back-references no window could catch — so we measure the *architecture*, not the constant. Success promotes the headline from "near-parity with a small tax" to "**parity at length, distance-independent**."

---

## 8. Grounding the ledger in the transcript — DONE, not ASKED

The MVP extractor `_slm_extract_contracts` (`research/memory_ledger.py:198`) is fed only the turn **prompt**, our own `spec.memory_record` summary, and predicted/changed file **paths** — so every `files/tests/symbols` field is inferred from what the turn was *asked* to do. That is recall≠action one layer down: a contract built from the request can't carry the API surface a later refactor must preserve.

**Fix:** distill the **codex rollout transcript** — what the turn actually *did* — into the fact bundle the extractor consumes.

**Verified schema (codex-cli 0.130.0 → 0.142.0-alpha.6).** Codex writes two streams: the harness-captured `codex exec --json` stdout (`run…jsonl`, **lossy** — `file_change` is path+kind only, no diff) and the on-disk rollout (`~/.codex/sessions/**/rollout-*.jsonl`, **rich**). The rollout carries the real edit in `patch_apply_end.changes` (shaped by kind: add→`content`, update→`unified_diff`, rename→`move_path`, plus a `success` flag) and `custom_tool_call.input` (the `*** Begin Patch` envelope); real pytest PASS/FAIL in `function_call_output`; exact per-turn usage in `token_count`; the baseline commit in `session_meta.git`. `reasoning` is **encrypted** — intent is unrecoverable.

**Floor / ceiling split.** A pure-Python `distill_rollout()` fills `files/tests/symbols` true-by-construction (the safety floor); the SLM is **demoted** to emitting only `{feature, contract, watch_for}` over that bundle, so it can no longer hallucinate a non-existent file/symbol/test.

**Honest scope.** This closes the *guessed-fields* gap; it does **not**, by itself, close recall≠action (a richer reminder is still a reminder). It is a **precondition** for the §5 ObligationVerifier — which now gets real diff+test evidence sourced from the transcript rather than a separate capture.

Cost-gated build order lives in `SESSION_MEMORY_ROADMAP.md` §6; source-specific risks (wrong-rollout selection, turn-local mis-attribution, schema-by-kind drift) in `SESSION_MEMORY_RELEVANCE_RISKS.md`. Detailed working notes are kept as local scratch (not committed).

---

## Appendix — key figures (clean N=5, 2026-06-19)

| Metric | Value |
|---|---|
| Cumulative token ratio | **9.69×** (602.1M / 62.1M) |
| Marginal in-regime ratio | **13.59×**, 95% CI [10.73, 16.46] |
| Compaction firing | 5/5 builtin runs (CONFIRMED) |
| Censored turns | 0 |
| Continuity (LLM judge) | builtin 0.26 vs with_session 0.134 |
| Continuity (forensic) | builtin 30/30 present; with_session 27/30 (0 absent); defects 2/5 vs 0/5 |
| Root cause | `MAX_TURNS=4` recency window (session.py:12), calibrated for 5-turn chains |

Data: `research/data/chain_results_v2/codex/chain_long/` (+ `chain_long_n2_recovered/`, `chain_long_prereparse_backup/`). Memory: `compaction_regime_n5_clean.md`.
