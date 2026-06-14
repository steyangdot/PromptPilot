# Postmortem: v2 normalizer `route="clarify"` silently breaks autonomous coding agents

- **Date:** 2026-06-14
- **Severity:** High — silent end-state failures (the bug isn't fixed, but no error is raised)
- **Component:** `prpt` v2 SLM normalizer (`slm-openai-v2`, and any normalizer sharing `SYSTEM_JSON_SPEC`)
- **Scope:** v2 normalizers used in **autonomous** (no-human) execution. **v1 (`slm-openai`) is unaffected** (it has no `clarify` route). The clean chain_auth tool-flip result is unaffected (it ran on v1).
- **Status:** Root-caused, fixed, fix verified at unit/probe level; full re-run (slm_native v2fix) in progress.

---

## Symptom

Running `chain_auth` (seeded DigestAuth A1-ordering bug) on codex with the **v2** normalizer, the `slm_native` arm fixed the bug in only **2 of 5 runs** — a **60% end-state failure rate**. For comparison, the **v1** normalizer scored **15/15** on the same task (codex *and* claude).

Per-run breakdown (`chain_auth_v2/codex/chain_auth/`):

| run | end-state | uncached | T1 edited the file? |
|-----|-----------|----------|---------------------|
| 1 | **PASS** (rc=0) | 120,904 | yes |
| 2 | **FAIL** (rc=1) | 181,442 | no (`changed=[]`) |
| 3 | **FAIL** (rc=1) | 143,298 | no (`changed=[]`) |
| 4 | **FAIL** (rc=1) | 254,501 | no (`changed=[]`) |
| 5 | **PASS** (rc=0) | 170,886 | yes |

The failure was **silent**: no exception, no timeout, no quota error. The run "completed" and even looked *cheaper* on tokens — a no-op T1 consumes less — so the regression would be invisible if you only watched token metrics.

---

## Root cause

Causal chain, drilling to the root:

1. The 3 failing runs all share one fact: **T1 (the bug-fix turn) made no edit** (`changed=[]`). The agent correctly *diagnosed* the bug and stopped.
2. It diagnosed-instead-of-edited because **the prompt it received was a question, not an instruction.** The v2 normalizer rewrote `"fix the bug…"` into a clarifying multiple-choice question (*"Where is the code? Which ordering is wrong? (A)/(B)/(C)…"*).
3. The rewrite was a question because the v2 normalizer selected **`route="clarify"`**, and `SYSTEM_JSON_SPEC` instructs: when route=clarify, emit a lead question + lettered options as `downstream_prompt`.
4. **ROOT CAUSE — a design mismatch:** the `clarify` route assumes a **human-in-the-loop** who answers the question, after which work proceeds. In an **autonomous agent chain there is no human to answer.** The clarifying question is delivered straight to the coding agent, which treats *"answer the question"* as its task — so it picks an option / explains and stops, instead of editing the file.

It is **not** a parsing bug, agent flakiness, or model failure. The agent did exactly what it was asked; it was asked the wrong thing. Two confirmations it's the true root:

- **Intent was classified `act`** correctly — the defect is purely that `route=clarify` overrode the body with a question, not a misread of user intent.
- **The prompt is not actually underspecified** for a repo-access agent ("fix the wrong secret ordering in DigestAuth" is fully actionable — the agent *did* locate the exact line). v2 asking *"where is the code?"* when the agent can grep shows clarify should not have fired at all.

### Evidence (confirmed three independent ways)
1. **End-state correlation:** every FAIL has T1 `changed=[]`; every PASS has T1 editing `httpx/_auth.py`. Perfect 1:1.
2. **Live transcript** (`run2_slm_native_t1.jsonl`): agent replied *"It's (D) something else: A1 = username:password:realm should be username:realm:password"* — correct diagnosis, zero edit.
3. **Isolated normalizer probe** (`research/v2_rewrite_probe.py`, N=20 on the exact T1 prompt vs the buggy repo): **20/20 rewrites were clarifying questions** (11 MIXED, 9 DIAGNOSE); **0 pure ACTION**. Decoupled from agent variance — the rewrite itself is the defect.

### Defect location
- `prpt/core/spec.py` — `SYSTEM_JSON_SPEC`: the `clarify` route definition (emits a human-style question as `downstream_prompt`).
- `prpt/normalizers/slm_openai_v2.py` — returns `spec.downstream_prompt` verbatim regardless of execution context (no human/autonomous distinction).

---

## Fix

Add an **autonomous-mode guard**: when running without a human to answer (`PROMPTPILOT_AUTONOMOUS=1`) and the SLM picks `route="clarify"`, **degrade to a best-effort `act`** — send the **original imperative prompt** (fully actionable for a repo-access agent) and relabel `intent=act` so the action output-suffix is applied. Default off preserves the human-in-the-loop CLI behavior.

**`prpt/normalizers/slm_openai_v2.py`** (in the JSON-spec branch of `_rewrite`):

```python
spec = parse_spec_json(raw)
if spec is not None:
    self._last_spec = spec
    self._last_intent = spec.intent
    self._last_scope = spec.scope
    # AUTONOMOUS-MODE GUARD: route='clarify' emits a human-style clarifying
    # question; an autonomous agent has no human to answer it, so it ANSWERS
    # the question instead of acting -> silent end-state failure. Degrade to a
    # best-effort act (original imperative) when PROMPTPILOT_AUTONOMOUS=1.
    if (spec.route == "clarify"
            and os.environ.get("PROMPTPILOT_AUTONOMOUS") == "1"):
        write_stderr("[slm-openai-v2] route=clarify in autonomous mode -> "
                     "degrading to act (original prompt; no human to answer).")
        self._last_intent = "act"
        return prompt
    return spec.downstream_prompt or prompt
```

(plus `import os` at the top of the module).

**`research/session_isolation_experiment.py`** — the chain harness is definitionally autonomous, so it enables the guard for every run:

```python
os.environ.setdefault("PROMPTPILOT_AUTONOMOUS", "1")
```

### ⚠️ Critical environment gotcha (where to apply the fix)
`prpt` is **editable-installed pointing at `B:\LLM` (the main checkout), not the git worktree.** The harness runs as a script (`python research/...`), so `import prpt` resolves to **`B:\LLM\prpt`**. Editing `prpt/` *inside a worktree has no effect on harness runs.* The operative fix lives in **`B:\LLM\prpt\normalizers\slm_openai_v2.py`**. Verify with a script-context import: `python research/x.py` doing `import prpt; print(prpt.__file__)` must print `B:\LLM\prpt`. (`research/*.py` IS the worktree copy — `sys.path[0]=research`.)

### Verification — CONFIRMED
- `GUARD PRESENT: True` when imported in script context (`B:\LLM\prpt`).
- Probe re-run with `PROMPTPILOT_AUTONOMOUS=1`: guard fired **20/20 → classification {ACTION: 20}** (was 0 ACTION pre-fix).
- **Full re-run confirmed** (`chain_auth_v2fix/codex/chain_auth/`, N=5): **end-state 5/5 fixed** (broken v2 was 2/5; v1 was 5/5). **T1 edited the file in all 5 runs** (was 2/5). The `clarify→act` guard fired **10 times** across the 25 turns — each would otherwise have risked a no-op, and every one became an action.
- Bonus (now a valid comparison since all runs fix the bug): v2fix slm_native = **190,578** uncached/run vs v1 slm_native **318,653** — v2's compact JSON-spec rewrite is **~1.67× cheaper than v1's prose rewrite at parity end-state** (rewrite-only effect; native-resume path, no memory_record).

---

## Affected / not affected
- **Affected by the bug:** all three v2 normalizers share `SYSTEM_JSON_SPEC`, so all three could emit a clarify question. They are **siblings, not a chain** (`OpenAISLMNormalizerV2`→`OpenAISLMNormalizer`, `AnthropicSLMNormalizerV2`→`SLMNormalizer`, `SubscriptionSLMNormalizerV2`→`SubscriptionSLMNormalizer`) — each has its own `_rewrite`, so a guard on one does **not** propagate by inheritance. The production Max-OAuth default is `SubscriptionSLMNormalizerV2`. Production `cli.py --auto`/`--dry-run` and `.claude/hooks/optimize_prompt.py` also forward the rewrite to an agent with no human to answer.
- **Not affected:** v1 normalizers (no `clarify` route); the clean chain_auth tool-flip result (ran on v1, 15/15 both tools); interactive CLI use with a human present (the degrade is off by default there — the question is shown to the user as intended).

## Complete fix (2026-06-14) — supersedes the single-arm patch above
The clarify→act degrade was lifted into one shared helper so it covers every backend (siblings can't inherit it):
- **`prpt/core/spec.py` → `resolve_downstream(spec, original_prompt)`** — the single guard. On `route=="clarify"` + `PROMPTPILOT_AUTONOMOUS=1`: returns the original imperative and resets the stale spec in place (`route/intent→act`, `scope→localized`, `memory_record→original` so the abandoned question is **not** persisted to session history — fixes the #5 stale-spec leak).
- **All three v2 `_rewrite` branches** (`slm_openai_v2.py`, `slm_anthropic_v2.py`, `slm_subscription_v2.py` — incl. the production default) now call it.
- **`cli.py:655`** sets `PROMPTPILOT_AUTONOMOUS=1` on `--auto`/`--dry-run`; **`.claude/hooks/optimize_prompt.py`** sets it before `normalize()`.
- **`SYSTEM_JSON_SPEC`** tightened: `clarify` only when genuinely ambiguous about *what* to change (not merely *where* — a repo-access agent greps); a function/symptom-named imperative → `act`. Protects interactive users and every backend the guard never reaches.
- **Tests:** `tests/test_slm.py` — direct unit tests of `resolve_downstream` (on/off/non-clarify) + integration degrade tests for anthropic-v2 and subscription-v2; the two existing clarify tests made env-deterministic. **108 passed** (test_slm + test_cli).
- **Editable-install note:** the operative copy is `B:\LLM\prpt` (the `pip install -e` target); worktree edits don't reach harness runs.
