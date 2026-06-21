# Session-Memory Relevance Design — Risk Register

**Date:** 2026-06-21
**Reviews:** the relevance-retrieval design (`…/SESSION_MEMORY_RELEVANCE_DESIGN.md` — three memory channels:
pre-rewrite retrieval, execution guard, post-turn verifier+repair).
**Method:** 6 adversarial risk-finding lanes (retrieval, verifier+repair, circularity/eval, generalization,
cost/ops/persistence, ledger integrity) + a synthesis pass; 36 candidates → 35 deduped, ranked by
severity×likelihood with *"produces a wrong result that looks right"* weighted highest. Grounded in this
project's scars (recall≠action, confident-invention, the v2 clarify-route defect, circularity, the 2/5
reliability cost, the code-centric over-fit). Code claims spot-verified against `memory_ledger.py` /
`chain_test_v2.py`.

---

## TL;DR — the dominant failure shape, and the one precondition

Every top risk is the **same shape**: an internally-coherent **fabrication that every downstream check
ratifies** — *wrong-but-looks-right*. A confidently-wrong referent → a clean rewrite → a flawless agent edit →
green tests on the untouched feature → file-hash scorer says "done." Plain `with_session` *can't* make this
error (it has no referent model); adding resolution can make end-state continuity **worse** while the dashboards
show parity.

**The precondition that gates everything else:** on **chain_long**, the only non-circular oracle (`pytest`
on the diff applied to a clean baseline) is **structurally disabled** — `_run_timeout_pytest` uses `-k timeout`
(→ rc=5 no-match on most contracts), the fixture `_GUARD` forbids test *execution*, and `judge_continuity` is
the same nano family with the answer baked into its rubric, ceilinged at parity. **So you cannot validate this
design on chain_long as it stands.** Build the non-circular ground truth *first*, or the whole experiment
ratifies coherent fabrications.

---

## Build gates (the actionable output)

### 🔒 Must mitigate BEFORE Stage 1–2 (pre-rewrite retrieval)
- [ ] **Abstain, don't resolve, on ambiguity.** Channel-1 may inject a referent only if it's **mechanically grounded** (id ∈ ledger keys *and* ≥1 named file/symbol exists on disk *and* the deterministic signal agrees with any SLM pick); otherwise inject an explicit `[unresolved reference]` marker or pass the pronoun through — never a fabricated concrete contract. (risks 1, 6, 26)
- [ ] **Closed-set selection call.** If the SLM selection call fires, constrain it to enumerated ledger keys + an explicit `NONE`; reject any out-of-set / free-text id. (risk 19)
- [ ] **On-disk grounding at merge time.** Reject any extracted contract whose files/symbols don't exist on disk; an un-grounded ("phantom") contract may not be surfaced, fire the guard, or become an obligation. (risks 7, 24)
- [ ] **Importance-aware retention, not pure recency.** `MAX_CONTRACTS=40` evicts by `turn` → the turn-1 invariant referenced at turn 50 is gone (the distance cliff re-enters). Pin invariant/architectural kinds; evict least-*relevant*, not least-*recent*. (risks 9, 18)
- [ ] **Deterministic last-active pointer.** Derive "last active feature" from the turn's actual git-modified files, **not** from the (sometimes-failing) SLM extraction; if extraction `ok=False`, mark the tie-breaker unavailable. (risk 17)
- [ ] **Collision-safe merge.** Key contracts on `(feature, kind)`; on id collision **append/version**, never overwrite `cur['contract']` (a data-semantic note clobbering a code contract). (risks 8, 35)
- [ ] **Extraction-failure floor.** On `ok=False`, retry/fallback + hard run-level degraded flag — don't let one nano blip blank retrieve+extract+verify in one turn. (risk 16)
- [ ] **Sidecar run-isolation.** Stamp the ledger with a run-id + timestamp; reject/clear a sidecar whose run-id ≠ current at load; add an **end-of-run** `clear_ledger`. (risk 23)
- [ ] **Schema-version check on load.** `load_ledger` must check `LEDGER_VERSION` and migrate-or-discard; `.get()` defaults for new fields; absent `confidence`/`last_touched` = "no signal," not low-confidence. (risk 32)
- [ ] **Reference-only injection.** Mark injected memory `REFERENCE-ONLY — do not expand task scope unless the request names it`; A/B-measure scope drift (the rewrite folding the surfaced contract into scope). (risk 15)

### 🔒 Must mitigate BEFORE Stage 4 (verifier + repair)
- [ ] **Prove repair moves end-state, not just the flag.** Gate the *entire* Stage-4 channel behind a pre-registered, non-circular oracle showing repair changes end-state continuity; a repair must **not** clear the obligation / update `last_touched_turn` until an independent check confirms the orphan is gone. (risks 2, 12)
- [ ] **A non-circular oracle must exist.** Don't validate retrieval/verifier on chain_long; build ≥1 orphan-detecting fixture that **writes tests + permits `-k` execution** (or hand-labeled fixtures graded by a **different tier**). (risks 3, 5, 13)
- [ ] **Tier-break the loop.** Retrieval/verify (or at least the verifier-*validator*) must be a **different model family** than the rewriter — correlated nano blind spots otherwise ratify the wrong answer. (risks 4, 14)
- [ ] **Anti-leniency = positive migration evidence.** A migration is CLEAN only if the old call-sites+tests are *demonstrably re-pointed* (substring/AST check the migrated symbol now appears); absence of migration = VIOLATED. (risk 10)
- [ ] **Evidence rule scoped to removals.** Diff-substring grounding applies to removal-class only; for invariant/data-semantic kinds use a presence-of-required-conversion check or abstain to UNKNOWN — never auto-CLEAN for lack of a diff line. (risk 20)
- [ ] **Final-turn repair needs an independent re-check.** T13 *is* the last turn — re-verify with a different tier/oracle, or extend the fixture by one no-op turn. (risk 21)
- [ ] **Hard-scope the repair turn.** Restrict to the flagged file(s)/symbol(s); forbid "migrate/remove" framing; re-run `guard_hits` after repair to confirm no NEW contract was orphaned. (risk 22)
- [ ] **Account repair-turn cost separately.** Repair-turn tokens land in `downstream_cost` (a full agent turn the baselines never run) → asymmetric inflation, and the zero-token 1200s-cap timeout bug recurs on a late repair; exclude from the baseline-comparable headline + treat a censored repair as INVALID-run. (risk 14-cost)
- [ ] **Don't gate on nano self-confidence.** Gate suppression/repair on deterministic grounding (overlap count, on-disk evidence), not the model's self-reported confidence; calibrate any threshold on the labeled fixture. (risk 25)
- [ ] **Measure cry-wolf.** Teach the verifier that *retained-old-kwargs-alongside-new-config = CLEAN (additive)*; require a precision floor (false-positive rate on the known-clean runs 2/3/5) before enabling repair. (risk 30)
- [ ] **New continuity metric with headroom.** `score_endstate` ceilings at 1.000 and the judge's |gap|<0.1 = parity → a real gain is invisible. Define a per-turn referent-resolution / orphan-detection metric *before* running. (risks 13-eval, 15)
- [ ] **Don't greenlight "no machinery" from chain_long alone.** A migration-shaped fixture structurally can't exhibit no-overlap failures; require ≥1 no-overlap (invariant/debugging) fixture in the gate. (risk 34)

---

## The "wrong-but-looks-right" killers (top 5, code-verified)
1. **Confidently-wrong pre-rewrite referent** (high/high). `guard_hits` keys only on file/symbol/word-boundary overlap (`memory_ledger.py:287-302`), so a bare pronoun falls through to the SLM selection call → the confident-invention machine returns a plausible-wrong contract → it lands in the `[Recent conversation]` slot *before* the rewrite (`chain_test_v2.py` `prepare_with_session`) → the rewriter authors a precise **wrong** prompt the agent executes flawlessly. *Worse than no resolution.*
2. **recall≠action at the verifier** (high/high). The load-bearing unproven leap is detection→repair→better continuity. The scar says surfacing the contract didn't make the agent preserve it; `merge_contracts` lets a repair turn that merely *touches* the file update the obligation and clear the trigger → the ledger **claims** resolution while the orphan still TypeErrors.
3. **No non-circular oracle on the headline chain** (high/high). `-k timeout` → rc=5 on chain_long, `_GUARD` forbids test execution, `judge_continuity` is nano with the answer in its rubric → retrieval correctness + verifier precision have **literally no ground truth** on the chain that motivates the design.
4. **Circular self-grading** (high/high). Same nano family picks, rewrites, extracts *and* verifies, and the validator is the same tier — correlated blind spots ratify the wrong-but-coherent result (exactly how the A/B judge over- then mis-stated continuity until forensics corrected it).
5. **Eval can't see an improvement** (high/high). `score_endstate` ceilings at 1.000 and |gap|<0.1 reads as parity → a real continuity gain is mathematically invisible; you could "validate" by reproducing the same null the prior arms produced.

---

## Full ranked register

| # | sev/lik | dimension | risk | one-line mitigation |
|---|---|---|---|---|
| 1 | H/H | retrieval | Confidently-wrong referent poisons the turn (worse than none) | ground or abstain; closed-set select; no free-text contract |
| 2 | H/H | verifier | Repair RELABELS the ledger satisfied without moving end-state | gate Stage-4 behind a non-circular "orphan gone" check |
| 3 | H/H | eval | The non-circular oracle (-k pytest on clean baseline) is absent on chain_long | build an orphan-detecting fixture before any accuracy claim |
| 4 | H/H | circularity | Same nano picks+rewrites+extracts+verifies | tier-break ≥1 channel + the validator |
| 5 | H/H | verifier | Verifier inherits the no-test guard → pytest disabled where the tax lives | validate on a test-writing fixture + diff-on-clean-baseline |
| 6 | H/H | retrieval | Wrong referent looks identical to a resolved one; file-hash scorer ratifies it | score by edited-symbol-vs-intended, not file-hash |
| 7 | H/H | extraction | Phantom contract poisons all three channels | reject contracts whose files/symbols aren't on disk |
| 8 | H/H | extraction | `_norm_feature` collision overwrites a live obligation | key on (feature, kind); append/version, don't overwrite |
| 9 | H/H | retrieval | MAX_CONTRACTS recency eviction drops the turn-1 contract | importance-aware retention; pin invariants |
| 10 | H/H | verifier | Tidy kwarg-drop read as "intentional migration" | require positive re-pointing evidence; absence = VIOLATED |
| 11 | H/H | schema | Code-centric schema can't represent/retrieve invariant/data/debug | anchorless retrieval; scope design as code-contract-only until then |
| 12 | H/M | schema | Architectural-invariant unreachable by `guard_hits` | invariant kinds bypass file/symbol gating on broad/new turns |
| 13 | H/H | eval | score_endstate/judge ceiling → can't register an improvement | new headroom metric (per-turn referent accuracy) |
| 14 | H/H | cost | Repair-turn cost in downstream_cost + zero-token timeout recurs | separate cost field; censored repair = INVALID-run |
| 15 | M/H | retrieval | Injected memory becomes a scope attractor in the rewrite | mark REFERENCE-ONLY; A/B scope drift |
| 16 | H/M | extraction | Extraction failure silently → no-memory (design widens blast radius) | retry/fallback + hard degraded flag |
| 17 | H/M | retrieval | Stale last-active tie-breaker → yesterday's feature | derive last-active from edited files, not extraction |
| 18 | H/M | retrieval | Right contract evicted/truncated before retrieval | importance retention + relax cap for pinned |
| 19 | H/M | retrieval | SLM fallback hallucinates an id not in the ledger | closed multiple-choice over ledger keys + NONE |
| 20 | H/M | verifier | Invariant/data violations have no diff line → false CLEAN | presence-of-conversion check or UNKNOWN, never auto-CLEAN |
| 21 | H/M | verifier | Final-turn repair has no turn to re-verify | independent re-check / +1 no-op turn |
| 22 | H/M | verifier | Repair turn does an unbounded destructive edit | hard-scope to flagged files; re-run guard_hits after |
| 23 | H/M | persistence | cwd-hash sidecar, no TTL/run-id → cross-run leakage | run-id stamp + load-time reject + end-of-run clear |
| 24 | H/M | extraction | Phantom contract biases rewrite/guard/repair | on-disk grounding at merge_contracts |
| 25 | M/H | circularity | Phantom high-confidence drives a self-consistent wrong repair | gate on grounding, not self-confidence |
| 26 | M/M | retrieval | No-overlap invariant turn → wrong memory + fabricated scope | zero-overlap ⇒ don't fall back to last-active; mark unresolved |
| 27 | M/H | cost | ~5 nano round-trips + agent turn on one quota pool | conditional/batched calls + quota/latency guards |
| 28 | M/H | cost | Stale `slm_cost_estimate` doesn't model new calls | real per-call nano cost for retrieve/select/extract/verify |
| 29 | M/M | cost | No-overlap refactor dumps the WHOLE ledger into the prompt | cap fallback to top-K pinned contracts |
| 30 | M/M | verifier | Cry-wolf false-VIOLATED on correct additive migrations | additive=CLEAN; precision floor before enabling repair |
| 31 | M/M | eval | git-diff oracle can't tell "preserved" from "orphaned tests" | orphan-detection pass (apply diff to clean baseline + AST) |
| 32 | M/M | persistence | `load_ledger` never checks LEDGER_VERSION | version check + migrate/discard + .get() defaults |
| 33 | H/M | schema | Debugging-finding contracts have no artifact to retrieve/verify | distinct topic-keyword kind; label as known coverage gap |
| 34 | H/M | eval | Stage-1 gate greenlights "no machinery" on chain_long alone | require a no-overlap fixture in the gate |
| 35 | M/L | extraction | id-collision merges data-semantic into a code contract (kind clobber) | (feature, kind) key; covered by risk 8 |

---

## Coverage gaps (classes the 6 lanes *under*-surfaced)
1. **Concurrency / lost-update race** — two arms or parallel runs writing the same `promptpilot_ledger_<hash>.json` (no file lock; `update_ledger` is read→merge→save) → lost updates.
2. **Content-sourced prompt injection** — chain_long reads large httpx modules each turn; a symbol/comment mined from file content flows **unsanitized** into the pre-rewrite prompt / ledger.
3. **Determinism / reproducibility** — every nano call is non-deterministic; the multi-call design worsens run-to-run variance, and the "lead with total tokens" methodology may not absorb verifier/repair-turn variance.
4. **Cross-arm leakage in one process** — if `clear_ledger` is skipped on a non-with_memory arm, the sidecar can persist across arms (compounds the fixed-arm-order caveat).
5. **New-field population correctness** — risks cover *absent* v1 fields but not the inverse: `call_sites`/`watch_for`/`anchors` populated by the same unreliable nano with no validation (anchors pointing at non-existent lines; vacuous `watch_for`).
6. **Operator-trust suppression** — a verifier reporting "satisfied" could suppress the **human forensics** (transcript mining / multi-agent diff review) that actually caught the truth in *every* prior result — removing the only check that has historically worked.
7. **No positive control** — the eval needs a case where retrieval *should obviously* fire + a known-bad baseline that *should obviously* fail, to prove the instrument can detect **any** signal before a parity verdict is trusted.

---

## Bottom line
The design's *direction* is sound (relevance retrieval before the rewrite is the right fix for the referential
gap). But its top risks are **not about the mechanism — they're about measurement and circularity**: the most
dangerous outcomes are coherent fabrications that every current instrument ratifies, on a chain that has no
non-circular ground truth.

So the build order the risks imply is:
1. **First, ground truth** — a non-circular oracle / an orphan-detecting, test-writing fixture + a positive control. *No retrieval or verifier accuracy number means anything until this exists.*
2. **Then Stage 1–2** with **abstain-not-resolve + on-disk grounding + importance-aware retention** (the 🔒 list above).
3. **Then Stage 4** only behind **proof that repair moves end-state**, with a **tier-broken** validator and **separated cost accounting**.

Until #1 exists, building #2/#3 risks producing a confident, well-instrumented, *wrong* answer — the exact
pattern this project has had to walk back twice already.
