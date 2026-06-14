# Review: the `route="clarify"` autonomous-mode fix is incomplete

- **Date:** 2026-06-14
- **Reviews:** [`V2_CLARIFY_ROUTE_POSTMORTEM.md`](V2_CLARIFY_ROUTE_POSTMORTEM.md) (root cause + the `slm-openai-v2` guard + the 5/5 re-run)
- **Method:** multi-agent adversarial review — every claim in the postmortem re-verified against `prpt` source and the on-disk experiment artifacts; findings cross-checked by a completeness critic.
- **Bottom line:** the postmortem's **root cause is correct** and the shipped guard **works for the one arm it was measured on** (re-run 5/5). But the fix touches a single normalizer; the same bug is **still live** in two of three v2 backends — including the production default — and in the production CLI/hook paths. There is **no test** guarding any of it, and the change is **uncommitted**.

---

## What the review confirmed (validated — no action needed)

- **Root cause is exact.** `SYSTEM_JSON_SPEC` (`prpt/core/spec.py:125-129`) instructs the model to emit a lettered clarifying question as `downstream_prompt` when `route="clarify"`; the normalizer returns it verbatim (`slm_openai_v2.py:119`); and the *only* clarify handler in the codebase (`cli.py:685`) is never reached by the autonomous harness, which calls `normalize()` directly. The question reaches the coding agent unhandled.
- **The measured arm is genuinely covered.** The `chain_auth` codex `slm_native` v2 arm instantiates `OpenAISLMNormalizerV2` via the explicit `create_normalizer("slm-openai-v2")` branch (`base.py:269-271`) — the one class carrying the guard.
- **The re-run landed.** `_session_retest_2026-06-07/chain_auth_v2fix/` completed at **5/5 end-state PASS** (all `endstate_slm_native_run*.json` → `pytest_rc=0`); the guard fired 10× in `harness.log`.
- **The editable-install gotcha is real.** A PEP 660 finder hardcodes `MAPPING = {'prpt': 'B:\\LLM\\prpt'}`; the fix is in that operative location; the worktree copy is byte-identical but never imported by harness runs.
- **v1 is genuinely unaffected** — the `route` enum is v2-only (defined in `spec.py`); the clean tool-flip result (v1, 15/15 both tools) stands.

---

## The material correction: the fix does NOT propagate "by inheritance"

The postmortem's "Affected / not affected" section says the guard covers *"by inheritance the anthropic/subscription v2 variants."* **This is false.** The three v2 normalizers are **siblings**, not a chain — each extends a *different* v1 base and defines its own `_rewrite`:

```
OpenAISLMNormalizerV2       -> OpenAISLMNormalizer       -> Normalizer   (guard HERE only)
AnthropicSLMNormalizerV2    -> SLMNormalizer             -> Normalizer   (own _rewrite, NO guard)
SubscriptionSLMNormalizerV2 -> SubscriptionSLMNormalizer -> Normalizer   (own _rewrite, NO guard)
```

The guard added to `OpenAISLMNormalizerV2` is never reached by the other two. They share `SYSTEM_JSON_SPEC` (so all are affected by the bug) but only `slm-openai-v2` was patched. Separating *affected-by-the-bug* from *covered-by-the-fix*:

| normalizer / path | affected by the bug? | covered by the current fix? |
|---|---|---|
| `slm-openai-v2` (`OpenAISLMNormalizerV2`) | yes | ✅ yes — guard at `slm_openai_v2.py:111-118` |
| `slm-anthropic-v2` (`AnthropicSLMNormalizerV2`) | yes — `slm_anthropic_v2.py:107` returns the question verbatim | ❌ NO |
| `slm-subscription-v2` (`SubscriptionSLMNormalizerV2`) — **production Max-OAuth default** | yes — `slm_subscription_v2.py:89` returns the question verbatim | ❌ NO |
| `cli.py --auto` / `--dry-run` | yes — skips the clarify gate (`cli.py:689`), forwards the question | ❌ NO — the CLI never sets `PROMPTPILOT_AUTONOMOUS` |
| `hooks/optimize_prompt.py` | yes — calls `normalize()` directly, no clarify handling (`:117`) | ❌ NO |

The worst-exposed normalizer — `SubscriptionSLMNormalizerV2`, the auto-detected Max-OAuth default (`base.py:336-337`) — is one of the *unfixed* ones.

---

## Remaining work (prioritized)

1. **[HIGH] Two of three v2 normalizers are still broken — including the production default.** Lift the guard into a single shared helper (e.g. `resolve_downstream(spec, prompt) -> (text, intent, scope)` in `prpt/core/spec.py`) called from all three `_rewrite` JSON-spec branches (`slm_openai_v2.py:119`, `slm_anthropic_v2.py:107`, `slm_subscription_v2.py:89`). Because the three are siblings, the fix cannot propagate by inheritance — it must be shared (preferred — also kills the triplication) or duplicated into the other two (which then also need `import os`).
2. **[HIGH] Production `cli.py --auto`/`--dry-run` and `hooks/optimize_prompt.py` reproduce the bug, un-guarded.** `cli.py:689` skips the clarify print-and-exit gate under `--auto`, then forwards `normalized_prompt` (the lettered question) to the agent; neither path sets `PROMPTPILOT_AUTONOMOUS`. **Confirmed**, not hypothetical. Fix: set `PROMPTPILOT_AUTONOMOUS=1` early when `args.auto`, or apply the degrade directly in `cli.py`.
3. **[HIGH] No test covers the guard, and existing tests codify the bug.** Nothing in `tests/` references the guard. Worse, `tests/test_slm.py:711` (anthropic-v2) and `:833` (subscription-v2) assert `normalized_prompt == question` — i.e. they assert the *un-guarded* behavior is correct, so the suite would not catch a regression. Add a guard test on the shared path (env on → degrade to original + `intent=act`; env off → still carries the question) and revise the two clarify tests.
4. **[HIGH] The fix is uncommitted and its activation is worktree-only.** `git status` shows ` M prpt/normalizers/slm_openai_v2.py` (working-tree only, intermixed with other dirty changes). The only setter of `PROMPTPILOT_AUTONOMOUS=1` lives in the gate-measure worktree's `session_isolation_experiment.py:70`; the canonical `B:\LLM\research` has no such file, so any entrypoint not routed through that wrapper **silently no-ops the guard** and the 60% regression returns with no warning. Commit on a feature branch (no-direct-to-main rule); move the env-setter into the canonical harness entrypoint; log whether the guard is active at startup.
5. **[MEDIUM] The degrade leaves a stale clarify spec.** `_last_spec` (hence `memory_record`) still describes the *question*, not the action taken — and both `cli._build_assistant_record` and the harness `record_to_session` persist it into session history, polluting the next turn's `[Recent conversation]` (directly undercutting the session-memory mechanism the experiment measures). Also `_last_scope` is not reset, so a `broad`/`new` clarify spec silently drops the output suffix. Fix alongside the shared helper: null/replace `memory_record` and pin `self._last_scope = "localized"` on degrade.
6. **[HIGH — postmortem listed this "optional"] Tighten `SYSTEM_JSON_SPEC` so `clarify` fires conservatively.** 20/20 clarify on a fully-actionable imperative is a ~100% mis-fire that the guard only *masks* (autonomous mode, openai-v2 only). For interactive users (guard off by default) it is **worse than a pester — a hard stop**: `cli.py:689-700` prints the question and `return 0`, refusing to do the work. The spec over-specifies the question *format* while leaving the *trigger* a single vague clause. Add negative anchors: a named-function + described-symptom imperative is actionable → `act`; never `clarify` merely to confirm file location (a repo-access agent can grep). This protects interactive users and every v2 backend the guard never touches.

---

## Empirical caveats

The 5/5 recovery, the 10× guard-firing, and the `endstate_*` PASS artifacts were re-verified on disk. But two earlier numbers are **no longer re-verifiable**:

- The probe's pre-fix "0 ACTION / 20-questions" count was overwritten — `v2_rewrite_probe_results.json` now holds the post-fix `{ACTION: 20}` state.
- The original `chain_auth_v2` per-run transcripts (`run2_slm_native_t1.jsonl`, the 2/5 table) were not found on disk.

The *causal mechanism* is fully confirmed from source; the **60% magnitude** rests on artifacts that no longer exist.

> **Correction (re-verified 2026-06-14):** the broken-`chain_auth_v2` artifacts are **NOT gone.** All ten `slm_native_run*.json` + `endstate_*` files and `run2_slm_native_t1.jsonl` (162 KB) are present under `_session_retest_2026-06-07/chain_auth_v2/codex/chain_auth/`; the 2/5 table and the run2 "diagnose-not-edit" transcript are fully re-verifiable. Only the *probe* JSON (`research/v2_rewrite_probe_results.json`) was overwritten with the post-fix `{ACTION: 20}` state — but the pre-fix "0 ACTION / 20 questions" count is preserved in the task log. The 60% magnitude stands.

---

## Resolution (2026-06-14) — all HIGH items addressed

Every finding above was acted on; the fix is now shared across all backends, not a single arm. Verified by **108 passing tests** (`test_slm` + `test_cli`).

| # | Finding | Status |
|---|---|---|
| 1 | Two of three v2 normalizers unfixed (incl. production default) | ✅ **Done** — guard lifted into `spec.resolve_downstream`; all three `_rewrite` branches call it |
| 2 | `cli.py --auto`/`--dry-run` + hook un-guarded | ✅ **Done** — `cli.py:655` sets `PROMPTPILOT_AUTONOMOUS=1` on `--auto`/`--dry-run`; `.claude/hooks/optimize_prompt.py` sets it before `normalize()` |
| 3 | No guard test; existing tests codify the bug | ✅ **Done** — unit tests of `resolve_downstream` + integration degrade tests (anthropic-v2, subscription-v2); `:711`/`:833` made env-deterministic |
| 4 | Fix uncommitted; activation worktree-only | ⏳ **Pending commit** — code is in the operative `B:\LLM\prpt`; activation no longer worktree-only (CLI + hook now set the flag in-process). Commit on a feature branch is the last step (B:\LLM is on `main` with other dirty files → selective add). |
| 5 | Degrade leaves stale clarify `memory_record`/`scope` | ✅ **Done** — `resolve_downstream` resets `route/intent→act`, `scope→localized`, `memory_record→original` on degrade |
| 6 | `SYSTEM_JSON_SPEC` clarify mis-calibrated | ✅ **Done** — tightened: clarify only for genuine *what*-ambiguity; never for unstated location (agent greps); named-symptom imperative → `act` |

The shared helper (#1+#5) also removes the triplication. Item #4 (commit) is the only open step and needs the git approach confirmed (see below).
