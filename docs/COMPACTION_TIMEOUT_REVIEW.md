# Adversarial review — COMPACTION_TIMEOUT_FIX_PLAN.md

**Reviewer:** independent / skeptical. **Date:** 2026-06-18.
**Scope reviewed:** `docs/COMPACTION_TIMEOUT_FIX_PLAN.md` against `research/agentic_variety_test.py`, `research/chain_test_v2.py`, `research/analyze_compaction_regime.py`, `prpt/_subprocess.py`, and the N=2 raw streams under `research/data/chain_results_v2/codex/chain_long/`.

---

## Verdict (one line)

**REVISE BEFORE IMPLEMENTING.** The plan's diagnosis is correct and the data is real, but **Change B (10s grace-wait) is refuted by the plan's own data — it would recover only 1 of the 6 censored turns live** — and three secondary mechanisms (analyzer gating completeness, codex reaper safety, 600s adequacy) have verdict-relevant defects. The right primary fix is a **post-run reparse pass over all stream files**, not a 10s in-line wait.

---

## What's sound (keep)

1. **The diagnosis is correct and the recovery data is real.** All 6 censored stream files contain exactly one `turn.completed` with the exact tokens the plan/§10 cite (verified on disk): ws1 T10=1,361,072 / T11=957,921 / T13=9,540,411; ws2 T1=4,059,399 / T6=1,290,230; bi2 T12=18,508,924. Post-hoc recovery by re-reading these files is valid and free. (Plan §3.1 — confirmed.)

2. **The recovered headline is right.** Independently recomputed: recovered cumulative = **5.48×** (builtin 171,557,834 / with_session 31,326,527), and the in-regime T13 pair = **1.40×** (13,338,063 / 9,540,411). Both reproduce §10 to the figure. The "censored exclusion dropped `with_session`'s *heaviest* turns" claim is correct and is the real reason 8.25× was inflated.

3. **The structural discriminator is sound *for this fixture's process model*.** Across all 52 stream files, `turn.completed` count is exactly 1 — never 0, never >1. Each `codex exec` / `exec resume` is a fresh process writing a fresh file, so the "earlier-sub-turn / resumed-thread artifact" false-positive (review Q3) does not arise here. The discriminator (`turn.completed` present ⟺ completed) is structurally defensible.

4. **Change A (raise the cap) and Change C (recovery-gating) are directionally correct and necessary** — gating is genuinely the easy-to-miss linchpin, and the plan flags it (§2 note). Keep both, with the fixes below.

5. **The editable-install awareness is correct.** Confirmed: run as a script from `research/`, `import prpt` resolves to the **editable target `B:\LLM\prpt`**, not the worktree copy — while `research/*.py` runs from the worktree. The plan's §5 note is accurate. (See should-fix S4 for the actionable consequence.)

---

## MUST-FIX before implementing (verdict-changing)

### M1 — Change B's 10s grace-wait is refuted by the plan's own data. It recovers 1/6 live.

This is the headline flaw. I reconstructed each censored turn's **flush-latency-after-kill** (real `turn.completed` flush time minus the ~300s kill instant, anchored from file mtimes + recorded `wall_t`):

| Censored turn | flush latency after kill | caught by 10s wait? |
|---|---|---|
| ws2 T6 | **25 s** | borderline (no) |
| ws1 T10 | **75 s** | no |
| ws1 T11 | **89 s** | no |
| ws2 T1 | **254 s** (~4 min) | no |
| bi2 T12 | **336 s** (~5.6 min) | no |
| ws1 T13 | **799 s** (~13 min) | no |

**A ~10s grace-wait captures 0–1 of 6.** The reason the on-disk reparse *appears* to work is purely post-hoc: the orphaned codex grandchild kept running for minutes after the kill and the flush had already landed by the time anyone re-read the file. In a **live** run, a 10s poll after `TimeoutExpired` returns long before the grandchild finishes its turn — so B as specified is a near no-op live, exactly the false comfort §10 warns about ("T13 flushed ~12 min after the run summary"). The plan even states the max *completing* turn was 274s/285s, but that is **survivorship-biased**: it is the max over turns that finished *under* the 300s cap, and excludes the very turns (T13≈1099s real, T1≈554s real) that blew past it.

**Fix:** Replace the 10s in-line wait with one (ideally both) of:
- **(preferred) A post-run reparse pass.** After all turns/runs finish, walk every `run{R}_{arm}_t{T}.jsonl`, and for any turn recorded `rc==124`, re-read the now-flushed `turn.completed`; if present, set `recovered_after_timeout=True` and rewrite the saved usage. This is deterministic and matches how §10's correction was actually produced. It is the only approach the data supports.
- **(complementary) Force the flush before reading** by reaping/terminating the codex grandchild on the timeout path *and waiting on it* so the OS flushes its stdout pipe — but note the killed process may abort *before* emitting `turn.completed` (the kill is what ends it), so this is not reliable on its own; the post-run pass is still needed for turns that only complete minutes later.

A bare 10s in-line grace-wait should be **dropped** — it adds 10s × every timeout and buys ~nothing.

### M2 — The analyzer (`analyze_compaction_regime.py`) is NOT in the plan's edit list, but it produces the headline and will still exclude recovered turns.

The plan's change table (§2) names only `agentic_variety_test.py` and `chain_test_v2.py`. But the published ratio comes from `analyze_compaction_regime.py`, whose own `is_censored()` (lines 70–74) reads the **saved** fields directly:

```python
def is_censored(turn):
    if turn.get("timed_out"):        # <-- still True if harness keeps timed_out=True
        return True
    sc = turn.get("score") or {}
    return bool(sc.get("censored"))  # <-- still True unless score.censored is cleared
```

and `load_runs()` (lines 98–106) sets `total=None` / `uncached=None` for any `is_censored` turn. So unless Change C **also clears the recorded `timed_out` field and `score["censored"]`** for recovered turns (not just adds a `recovered_after_timeout` flag), the analyzer drops them and the fix is silent — the same trap the plan calls out for `turn_timed_out()`, recurring in a third file the plan forgot.

**Fix:** Either (a) for recovered turns set `timed_out=False` and `score["censored"]=False` in the saved record (cleanest — every downstream consumer then counts them automatically), keeping `recovered_after_timeout=True` as an informational tag; **or** (b) patch `is_censored()` in the analyzer to honor `recovered_after_timeout`. Option (a) is strongly preferred and also covers the other four consumers below. Note `chain_test_v2.py:938` sets `timed_out=(rc==124)` and `:987` sets `score["censored"]=timed_out` — both must be made conditional on "no recovered tokens."

### M3 — `turn_timed_out()` returns True on `rc==124` *before* any recovery check.

`agentic_variety_test.py:387`:
```python
if turn.get("timed_out") is True or turn.get("rc") == 124:
    return True
```
Recovered turns will still carry `rc=124` (the subprocess *was* killed). If Change C only flips `timed_out` but leaves `rc=124`, this early-return still censors them in `aggregate_runs` (the per-turn AND cumulative means at `chain_test_v2.py:1106`). The gate must check recovery **before** the `rc==124` branch, e.g. `if turn.get("recovered_after_timeout"): return False` first, or drop the `rc==124` clause in favor of the recovered-tokens test. Trace confirms: `aggregate_runs` filters `per_run = [pr for pr in all_run if not turn_timed_out(pr, tool)]` — a single stale True here re-drops the turn from every mean. (Answers review Q4: with the plan as written, recovered turns are **still dropped** in both aggregate metrics unless `rc==124` is handled.)

### M4 — Codex orphan reaper (D) is unsafe to mirror naively: it can kill the user's desktop Codex.

Live check on this machine: the **Codex desktop app is running**, as a multi-process tree all named `Codex.exe` / `codex.exe` (case-insensitive on Windows), including `...\resources\codex.exe` (PID 30968) — the **same image name** as the CLI we spawn. `reap_claude_orphans()` is safe for claude.exe only because nothing else on the box spawns claude.exe; that assumption is **false for codex**. The desktop app legitimately spawns short-lived child `Codex.exe`/`codex.exe` processes (verified: PID 9172 parent=37284, itself `Codex.exe`), so a child can transiently appear "orphaned" (parent exited) and a mirrored reaper filtering `Name='codex.exe'` + orphan-check would **kill the user's desktop Codex helpers** — and the desktop app is exactly what clobbers `~/.codex/config.toml` (memory: codex-desktop-config-gotcha), so collateral damage here is doubly bad.

**Fix:** Do **not** mirror `reap_claude_orphans` by image name. A codex reaper must be far more conservative — e.g. track the exact PID `subprocess` spawned and kill only its descendant tree (`taskkill /F /T /PID <our_pid>`), never an `Name='codex.exe'` sweep. Better still: since `subprocess.run(timeout=...)` already kills the **direct** child, and the N=2 data shows the harness completed both runs (26 turns) with no handle exhaustion, **question whether D is needed at all** for codex (see S3). If kept, scope it to our own process tree by PID, not by name.

---

## Should-fix

### S1 — 600s does not clear the heaviest in-regime turns "with margin." Survivorship bias in the cap justification.

Estimated real durations (300s cap + flush-latency-after-kill, an upper bound but the only signal):

| Turn | est. real duration | under 600s? |
|---|---|---|
| ws1 T10 | 375 s | yes |
| ws2 T6 | 325 s | yes |
| ws1 T11 | 389 s | yes |
| ws2 T1 | 554 s | yes (barely) |
| **bi2 T12** | **636 s** | **NO** |
| **ws1 T13** | **1099 s** | **NO** |

**2 of 6 censored turns would still exceed 600s.** And these are the in-regime/heavy turns that matter most for the marginal ratio. The plan's "max completing turn 274s/285s → 600s clears with margin" is computed only over turns that finished under 300s — it cannot speak to the censored ones. For a clean N≥5 the cap should be **≥1200s** (matching claude's headroom philosophy), *and* the post-run reparse (M1) must still backstop turns that exceed even that. Raising to only 600s will reproduce a smaller version of the same censoring on the heaviest turns.

### S2 — "Recover + count the thrash turns" makes the number honest but does not fix the thing causing slowness (answers review Q2).

The censored turns are `with_session`'s thrash turns (29–54 tool calls/turn re-discovering layout), genuinely costing 1.0–9.5M tokens. Raising the cap + counting them is the **correct measurement choice** — but it (a) makes the run materially slower (turns now run to 600–1100s instead of being clipped at 300s) and (b) lands the ratio near **~5.5× cumulative / ~1.4× on the heaviest in-regime pair**, not the 8–12× the test originally chased. The plan should state plainly that the expected outcome of the fix is a **lower, slower, more honest** number, and that bounded-arm thrash is itself a real cost the in-regime ratio now (correctly) absorbs. This is consistent with §10 but the *plan* under-emphasizes that A+B+C will *reduce* the headline, not just "clean" it.

### S3 — D's premise (handle exhaustion across N≥5) is asserted, not evidenced for codex.

The N=2 run (26 codex turns, 6 timeouts with orphans flushing up to 13 min later) completed both runs without handle exhaustion. The pile-up that motivated `reap_claude_orphans` was a **claude.exe** retry-storm, and the pilot-1 wedge was a **pytest** orphan storm under a different (now-dropped) fixture. There is no codex-specific evidence of handle exhaustion. If D is kept, justify it with a measured codex orphan count, and implement it per-PID (M4) — otherwise consider deferring D and relying on `subprocess`'s direct-child kill plus the post-run reparse.

### S4 — `CODEX_TIMEOUT_SEC` is frozen at import; launchers don't set it; plan's "no code edit needed" is incomplete.

`agentic_variety_test.py:97` reads `CODEX_TIMEOUT_SEC` once at module import into a module global, used by `run_codex` (line 155) and re-exported by value into `chain_test_v2` (line 112, recorded as `timeout_cap_sec` at 1044). So `CODEX_TIMEOUT_SEC=600` must be in the **environment before Python starts**. Neither `run_compaction_regime.ps1` nor `run_compaction_detached.ps1` currently sets it. "No code edit needed" is true only if you add `$env:CODEX_TIMEOUT_SEC = "1200"` (per S1) to the launcher **and** ensure the Scheduled Task inherits it (Task Scheduler does not inherit an interactive shell's env). Concretely: set it inside the `.ps1`, before the `python`/`Start-Process` line.

### S5 — Other censored-flag consumers not in the edit list.

Beyond the analyzer (M2), `research/recover_uncached.py` and `research/analyze_uncached_cost.py` also key off `turn_timed_out` / censored. The clean-clear approach in M2(a) (set `timed_out=False`, `score.censored=False` on recovered turns) makes all of them correct for free; the flag-only approach would require touching each. Prefer M2(a).

---

## Minor

- **m1 — Reaper function location.** Change D says "mirror `reap_claude_orphans`" and points at `agentic_variety_test.py` for the *call site*, but the function lives in `prpt/_subprocess.py`. A new `reap_codex_orphans` belongs in the **editable `B:\LLM\prpt\_subprocess.py`**, not the worktree copy (S4/§5). The plan should name the file explicitly to avoid editing a no-op copy.
- **m2 — Hang detection is asserted, not demonstrated for this fixture.** All 52 streams completed; there is **no genuine-hang example** in the codex `chain_long` dataset to validate the discriminator's negative case. The "still catches hangs" claim (§3.3, §5) rests on the pilot-1 (different fixture) precedent. Reasonable, but mark it as untested-for-this-fixture rather than proven.
- **m3 — Grace-wait, if any, should poll the LAST line, not just existence of `turn.completed`.** Partial JSON on the final line during an active flush could `JSONDecodeError`; `parse_usage` already swallows that (line 296), so a poll must re-read until the last parseable event is `turn.completed`, not merely until the file grows.
- **m4 — Success-criteria wording.** §6 "0 (or near-0) censored turns" is unreachable if the cap is 600s (S1: 2 turns still exceed it) unless the post-run reparse (M1) is in place. Tie the success criterion to "0 *unrecovered* censored turns after the reparse pass," not to the cap.

---

## Bottom line

The plan correctly diagnoses the censoring and the recovered ~5.5× is real and independently reproduced. But **as written it will not work live**: the 10s grace-wait (B) catches at most 1 of 6 timeouts (M1), the recovered turns will still be excluded by the analyzer (M2) and by `turn_timed_out`'s `rc==124` early-return (M3), the codex reaper can kill the user's desktop Codex (M4), and 600s under-shoots the two heaviest turns (S1). 

**Minimum viable fix:** (1) replace B with a **post-run reparse pass** that flips `timed_out`/`score.censored` to False and writes recovered usage for any `rc==124` turn whose stream has a `turn.completed`; (2) raise the cap to **≥1200s** in the launcher env; (3) make `turn_timed_out` recovery-aware before its `rc==124` branch; (4) drop or PID-scope the codex reaper. With those, the harness will produce the honest ~5.5×-class number at N≥5. Do **not** fire a paid N≥5 run on the plan as currently written.
