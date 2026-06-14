# Response to review: `V2_CLARIFY_ROUTE_FIX_REVIEW.md`

- **Date:** 2026-06-14
- **Responds to:** [`V2_CLARIFY_ROUTE_FIX_REVIEW.md`](V2_CLARIFY_ROUTE_FIX_REVIEW.md) (adversarial review of the original single-arm `slm-openai-v2` guard)
- **Outcome:** review **accepted**; all six findings addressed and shipped in **PR [#39](https://github.com/steyangdot/PromptPilot/pull/39)** (`fix/v2-clarify-route`). One review caveat corrected (artifacts were not lost).
- **Verification:** 108 tests pass (`test_slm` + `test_cli`); `chain_auth_v2fix` slm_native N=5 = 5/5 end-state.

---

## 1. Verdict

The review is **correct and load-bearing.** My original fix proved the mechanism and restored 5/5 *on the one arm I measured*, but it was a **1-of-3 patch** — and the worst-exposed normalizer (the production Max-OAuth default) was one of the two I had not touched. I did **not** take the review on faith: I independently re-verified every claim against the operative code (`B:\LLM\prpt`, the editable-install target) before acting. All structural claims held; one empirical caveat did not.

---

## 2. Independent verification of each claim

| Review claim | My verification | Verdict |
|---|---|---|
| The 3 v2 normalizers are siblings; the guard can't propagate by inheritance | `slm_anthropic_v2.py:107` & `slm_subscription_v2.py:89` each `return spec.downstream_prompt or prompt` in their own `_rewrite`; only `slm_openai_v2` had the guard | ✅ TRUE |
| The production Max-OAuth default is unfixed | `base.py` auto-detect (`create_normalizer("slm")`) falls back to `SubscriptionSLMNormalizerV2` — unguarded | ✅ TRUE |
| `cli.py --auto`/`--dry-run` forwards the question | `cli.py:689` skips the clarify gate under `--auto`/`--dry-run`/`--compare`; cli never set the flag | ✅ TRUE |
| The hook calls `normalize()` unguarded | `.claude/hooks/optimize_prompt.py` → `create_normalizer("slm")` (auto-detect → unguarded v2), no flag | ✅ TRUE |
| No guard test; existing tests codify the bug | `test_slm.py:711` / `:833` assert `normalized_prompt == question` | ✅ TRUE |
| The guard left `_last_spec`/`memory_record`/`_last_scope` stale (#5) | my original edit only set `_last_intent="act"` | ✅ TRUE |
| Empirical: the broken `chain_auth_v2` data is "gone" | all 10 run/endstate JSONs **and** `run2_slm_native_t1.jsonl` (162 KB) are on disk under `chain_auth_v2/codex/chain_auth/` | ❌ FALSE — the 2/5 evidence is fully re-verifiable; only the *probe* JSON was overwritten (its pre-fix count survives in the task log) |

---

## 3. Point-by-point response (what changed)

**#1 — Two of three normalizers unfixed (incl. production default). [HIGH]**
Accepted. Instead of duplicating the guard, I lifted it into a single shared helper **`resolve_downstream(spec, original_prompt)` in `prpt/core/spec.py`**, called from all three v2 `_rewrite` JSON-spec branches. Siblings now share one guard; the triplication is removed.

**#2 — `cli.py --auto`/`--dry-run` + hook un-guarded. [HIGH]**
Accepted. `cli.py` now sets `PROMPTPILOT_AUTONOMOUS=1` when `args.auto or args.dry_run` (before `normalize()`); `.claude/hooks/optimize_prompt.py` sets it before its `normalize()`. Both feed an autonomous agent, so both must degrade.

**#3 — No guard test; existing tests codify the bug. [HIGH]**
Accepted. Added unit tests of `resolve_downstream` (autonomous on → degrade+reset; off → question carried; non-clarify → passthrough) plus integration degrade tests for anthropic-v2 and subscription-v2. The two existing clarify tests (`:711`, `:833`) were made env-deterministic (`monkeypatch.delenv`) so a leaked flag can't flip them.

**#4 — Uncommitted; activation worktree-only. [HIGH]**
Accepted. Activation is no longer worktree-only — the CLI and hook now set the flag in-process. Committed on a clean feature branch (see §4).

**#5 — Degrade leaves a stale clarify spec. [MEDIUM]**
Accepted and folded into the shared helper: on degrade it resets `route/intent→act`, `scope→localized`, and **`memory_record→original prompt`** so the abandoned clarify question is not persisted into session history (which would have polluted the very bounded-session mechanism the experiment measures).

**#6 — Tighten `SYSTEM_JSON_SPEC` (review upgraded from the postmortem's "optional"). [HIGH]**
Accepted. The `clarify` guidance now fires only for genuine ambiguity about *what* to change — never merely because a file/location is unstated (a repo-access agent greps). A function/symptom-named imperative routes to `act`. This protects interactive users (whom the guard never reaches) and every backend.

**Empirical caveat — "artifacts no longer exist."**
Corrected. The broken-run artifacts are intact and re-verifiable; the 60% magnitude stands. Only `research/v2_rewrite_probe_results.json` was overwritten with the post-fix `{ACTION: 20}` state, and the pre-fix count is preserved in the task log.

---

## 4. Process notes

- **Landed via a clean worktree off `origin/main`.** `B:\LLM/main` was dirty with 42 unrelated files **and a non-mine *staged* `cli.py` `-34/+2`**, so committing in place risked capturing or clobbering others' work. I created a fresh worktree off `origin/main` (base matched local `main`, `56146d7`), transferred the 6 clean files by patch, re-applied the `cli.py` hunk cleanly (excluding the contamination), copied the docs, re-verified against the worktree's own `prpt`, then committed. The committed `cli.py` is **+9 (mine only)**.
- **Editable-install gotcha.** The operative package is `B:\LLM\prpt` (the `pip install -e` target); worktree `prpt` edits do not reach harness runs. The fix lives in the editable target and the PR branch.

## 5. Outcome

| metric | before fix | after fix |
|---|---|---|
| `chain_auth_v2fix` slm_native end-state | 2/5 | **5/5** |
| probe rewrites classified ACTION | 0/20 | **20/20** |
| guard test coverage | none | unit + integration |
| backends covered | slm-openai-v2 only | **all 3 + cli + hook** |
| tests | (existing) | **108 pass** |

Interactive CLI behavior is unchanged — with the guard off by default, a genuine clarify question is still shown to the human as intended.
