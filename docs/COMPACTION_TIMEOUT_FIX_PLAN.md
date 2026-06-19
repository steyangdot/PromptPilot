# Plan — make the codex compaction-regime harness timeout-safe

**Status:** ✅ IMPLEMENTED + free-validated (2026-06-18). Paid N≥5 run still gated on go-ahead. **Date:** 2026-06-18.

> ### Implementation status (2026-06-18)
> All 4 changes landed; A/B/C in code, D correctly absent. Files: `research/chain_test_v2.py`
> (new `reparse_timed_out_turns()` + seam call with in-memory re-sync), `research/agentic_variety_test.py`
> (`turn_timed_out` recovery check before `rc==124`), `research/analyze_compaction_regime.py`
> (`is_censored` honors recovery), `research/run_compaction_detached.ps1` **and**
> `research/run_compaction_regime.ps1` (`CODEX_TIMEOUT_SEC=1200`, runs=5), `research/_test_timeout_instrumentation.py`
> (recovery cases).
>
> **Free validation (zero quota) PASSED:** reparse on the existing N=2 streams recovered all 6 censored
> turns with exact tokens → analyzer reproduced cumulative **8.25× → 5.48×** (builtin 171,557,834 /
> with_session 31,326,527), censored **6 → 0** (matched == unmatched, asymmetric inflation gone),
> idempotent (2nd pass recovers 0), unit test green. Original censored data preserved in
> `…/codex/chain_long_prereparse_backup/`.
>
> **Adversarial verification (3 lenses) PASSED:** M1–M4 + S1/S4 all confirmed satisfied, no blockers.
> Correction to earlier recon: BOTH launchers resolve to the **worktree** copy (so both get the fix);
> the regime launcher merely lacked the cap env, now added.
>
> **Status before this section was the planning record below (kept for provenance).**

**Original status:** PLAN v2 (revised after adversarial review). **Date:** 2026-06-18.
**Scope:** `research/agentic_variety_test.py`, `research/chain_test_v2.py`, `research/analyze_compaction_regime.py` (the codex run/score/analyze path), plus the run launcher `.ps1`. No change to the `chain_long` fixture or the compaction test design.
**Supersedes:** v1 of this file. v1's Change B (10s in-line grace-wait) and Change D (codex reaper) were **refuted by the data** in review `docs/COMPACTION_TIMEOUT_REVIEW.md`; see §8 for the changelog.

---

## 1. Problem statement

The compaction-regime run (`chain_long`, codex, N=2) **censored 6 turns** (5 `with_session`, 1 `builtin`): each hit the **300s per-turn codex cap** → `rc=124` → recorded as `input_tokens=0, censored=true`. This caused two concrete failures:

1. **Inflated headline.** The analyzer *excludes* censored turns. But the censored turns were **`with_session`'s heaviest** (it thrashed): ws run1 T13 (migrate) = **9.5M**, ws run2 T1 = **4.1M**, plus T10/T11/T6 ≈ 1–1.4M. Dropping them made `with_session` look ~2× cheaper than it was → the reported **8.25×/12.2×** is really **~5.48×** cumulative on recovered data (builtin 171.6M / with_session 31.3M), and the heaviest in-regime pair (T13) is only **1.40×**.
2. **Can't reach a clean N≥5.** While turns censor, every run loses data asymmetrically and fails its own validity gate (N=2 < ≥4). We can't produce a publishable in-regime number.

**Root cause = two compounding issues:**
- **(a) Tight cap + orphan-flush race.** The censored turns *actually completed* — every one has a real `turn.completed` (with full usage) on disk. The harness recorded 0 only because it parsed the stream **before** the codex grandchild flushed that final line. (This is `memory/audit_uncached_timeout_bug.md`, recurring.) codex's 300s cap is the asymmetric outlier (claude is 2400s).
- **(b) Bounded-arm thrash.** `with_session` starts each turn with only the small `memory_record` (no native transcript), so it **re-discovers file layout every turn** — 29–54 tool calls/turn — pushing several turns past the 300s cliff (real durations up to ~1099s).

**Goal:** make slow-but-completing turns **complete and get counted** under a safe cap, while still flagging **genuine hangs** (so we don't paper over a wedge), so a clean **N≥5** run yields the honest ratio at quality parity. The honest ratio is expected to be **lower and slower** than the censored 8–12× — landing near **~5.5× cumulative / ~1.4× on the heaviest in-regime pair** — because counting `with_session`'s thrash turns is the correct measurement, and that thrash is a real cost the in-regime ratio should absorb (see §7, S2).

---

## 2. Key mechanism (read before the change table)

**The grandchild surviving the kill is the recovery mechanism — do not break it.**
When `subprocess.run(timeout=…)` fires, it kills the **direct** child. The codex **grandchild** (the model-driving process) is in a separate process group, survives, and finishes its turn — flushing the real `turn.completed` to the stream file **anywhere from 25s to ~13 minutes later** (measured). Recovery works *because* that line eventually lands on disk. Two consequences drive the whole revised design:

1. **Recovery must be a post-run pass, not an in-line wait.** An in-line grace-wait after `TimeoutExpired` returns long before the grandchild finishes — a 10s wait catches **0–1 of 6** measured censored turns. Only a sweep over the (now-flushed) stream files *after the whole job ends* is reliable.
2. **Any "reap the codex orphan on timeout" step is counterproductive.** Killing the grandchild aborts it **before** it emits `turn.completed` → permanently destroying the data the reparse depends on. So we do **not** add a codex reaper on the timeout path (this also avoids the desktop-Codex collateral-kill hazard, M4).

---

## 3. The planned fix (revised — 4 changes)

| # | Change | File / location |
|---|---|---|
| **A** | **Raise the per-turn cap to 1200s** | set `CODEX_TIMEOUT_SEC=1200` **in the launcher `.ps1`, before the `python`/`Start-Process` line** (the var is read once at module import — env-at-launch only; see S4). 1200s, not 600s: 2 of 6 censored turns had real durations >600s (bi2 T12 ≈636s, ws1 T13 ≈1099s). |
| **B** | **Post-run reparse pass (replaces v1's 10s wait).** After all turns/runs finish, walk every codex stream file; for any turn recorded `rc==124`, re-read the now-flushed stream — if its last parseable event is `turn.completed`, set `recovered_after_timeout=True`, write the real usage, and **clean-clear** `timed_out=False` + `score["censored"]=False` in the saved per-run JSON | `agentic_variety_test.py` (new `reparse_timed_out_turns()` helper) called from `chain_test_v2.py` after `run_chain_full` / before `aggregate_runs` |
| **C** | **Recovery-gating (critical).** Make every censored-flag consumer recovery-aware: (i) `turn_timed_out()` returns `False` for `recovered_after_timeout` **before** its `rc==124` branch; (ii) analyzer `is_censored()` honors `recovered_after_timeout` defensively (belt-and-suspenders on top of B's clean-clear) | `agentic_variety_test.py:~387` + `analyze_compaction_regime.py:~70` |
| **D** | **Cap-only quota guard (no reaper).** Rely on A (cap bounds most work inline), B (reparse recovers the rare over-cap turn), and the existing direct-child kill + `QuotaExhausted` guard. **No codex orphan reaper** — it would kill the grandchild before it flushes (§2) and could hit the user's desktop Codex (M4). | (no new reaper) |

> **C is the easy-to-miss linchpin — and it spans THREE files, not one.** Clearing `timed_out` alone is a silent no-op: the analyzer's `is_censored()` also keys on `score["censored"]`, and `turn_timed_out()` early-returns on `rc==124`. B's clean-clear (set both `timed_out=False` and `score["censored"]=False`) is what makes the analyzer **and** `recover_uncached.py` / `analyze_uncached_cost.py` count recovered turns for free (S5). C(i) is still needed because `turn_timed_out()` also checks `rc==124`, which we deliberately leave as historical truth.

---

## 4. Why the revised fix will work (evidence, not assertion)

1. **Recovery is a re-read, not a re-run — the data is already on disk.** All 6 censored stream files contain exactly one `turn.completed` with real usage right now (verified on disk, reproduced independently in review §"What's sound" #1): ws1 T10=1,361,072 / T11=957,921 / T13=9,540,411; ws2 T1=4,059,399 / T6=1,290,230; bi2 T12=18,508,924. The post-run pass (B) simply reads what's there. → recovery is guaranteed for the cases we've seen, at zero new compute. This is exactly how §10's corrected 5.48× was produced.
2. **1200s clears all six measured turns; B backstops anything beyond.** Estimated real durations (cap + flush-latency-after-kill): T10≈375s, T6≈325s, T11≈389s, T1≈554s, T12≈636s, T13≈1099s — all ≤1200s. → A removes cap-clipping for the observed distribution; B recovers any future turn that still exceeds 1200s. Success is tied to "0 *unrecovered* censored after reparse," not to the cap (m4).
3. **It still catches GENUINE hangs.** The discriminator is structural: a completed turn's stream ends in a parseable `turn.completed`; a real hang (pilot-1 style — pytest deadlock, retry storm) never emits one (its last event is an in-progress `item.*`). So `recovered_after_timeout` ⟺ last parseable event is `turn.completed`. A true hang fails that check → stays `censored` and is correctly excluded. **Caveat (m2):** this fixture has *no* genuine-hang example in its 52 streams — the negative case rests on the pilot-1 precedent (a *different* fixture), so it is "untested for this fixture," not proven.
4. **The wedge mode is already designed out of this fixture.** `chain_long`'s `_GUARD` forbids running tests (the pilot-1 hang vector), and the N=2 data confirms **no turn hung** — all 6 censored turns churned real tool calls and *completed*. So a 1200s cap on this fixture won't hide a wedge.
5. **No reaper means no broken recovery and no collateral damage.** Letting the grandchild finish is what makes B possible (§2); not sweeping by image name is what protects the user's running desktop Codex (M4). The N=2 run (26 codex turns, 6 orphans flushing up to 13 min later) completed both runs with **no handle exhaustion** — so the pile-up that motivated `reap_claude_orphans` (a claude.exe retry storm under a different fixture) is unevidenced for codex (S3).

**Net:** A fixes the clip, B recovers + counts the already-finished turns via a post-run pass, C makes all three consumers count them, the `turn.completed` discriminator preserves hang detection, and dropping the reaper keeps both recovery and the user's desktop Codex intact. The result: 0 unrecovered censored turns, all turns counted → the honest ratio (expected **~5.5× cumulative**) at quality parity.

---

## 5. Validation BEFORE spending any quota (free)

1. **Replay the reparse pass (B) on the 6 existing censored stream files** → confirm all 6 flip to `recovered_after_timeout=True` with the known real tokens (1.36M / 957k / 9.5M / 4.06M / 1.29M / 18.5M) and that `timed_out`/`score.censored` clear. Zero compute.
2. **Re-run the analyzer with recovery (C)** → confirm it reproduces the corrected **5.48×** cumulative and the lower **1.40×** T13 in-regime pair. Zero compute.
3. **Only then** spend a Pro window on the clean **N≥5** run (`CODEX_TIMEOUT_SEC=1200` in the `.ps1`, detached scheduled task, ~41%/5h-window per the anchor — note 1200s caps make the run materially slower than the 300s-clipped N=2).

---

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| A genuine hang masked by the higher cap | The `turn.completed` discriminator — hangs lack it → stay censored. The fixture's no-tests `_GUARD` already removes the known wedge vector. (Negative case untested *for this fixture* — m2.) |
| A turn exceeds even 1200s | B's post-run reparse recovers it from the flushed stream. Success criterion = 0 *unrecovered* censored after the pass. |
| Grandchild runs uncapped past the cap, burning quota | Accepted tradeoff: for a measurement run we *want* the real cost counted, and >1200s turns are rare. `QuotaExhausted` guard aborts cleanly if a quota wall is hit. We do **not** kill the grandchild (that would forfeit `turn.completed`, §2). |
| Longer wall-clock (1200s × heavy turns × N≥5) | Detached scheduled-task launch (survives session resets); quota ~41%/window. |
| Recorded numbers change (drop to ~5.5×) | Intended — the corrected ~5.5× is the honest one; documented in §10 of the test doc and §7/S2 here. A+B+C **lower** the headline, they don't just "clean" it. |
| Editable-install / worktree gotcha | `research/*` runs from `sys.path[0]` (the invoked worktree copy) — edit the copy the run uses. There is **no** reaper to place in `prpt/_subprocess.py` now (D dropped), so the v1 editable-install hazard for the reaper is moot. Verify the run uses the edited `research/*` copies before firing. |
| `CODEX_TIMEOUT_SEC` not picked up | It's frozen at module import. Set it **in the `.ps1` before the python line**, and ensure the Scheduled Task inherits it (Task Scheduler does NOT inherit an interactive shell's env — set it inside the script). (S4) |

---

## 7. Success criteria

A clean **N≥5** run with **0 unrecovered censored turns after the reparse pass**, **all turns counted**, **compaction firing in ≥4/5 builtin runs** → a publishable in-regime ratio (expected **~5.5× cumulative / ~1.4× heaviest in-regime pair**) at end-state/continuity parity, no longer failing its own validity gate.

> **State plainly (S2):** the expected outcome of this fix is a **lower, slower, more honest** number than the censored 8–12×. Counting `with_session`'s thrash turns (29–54 tool calls/turn re-discovering layout) is the correct measurement; that thrash is a genuine bounded-arm cost the in-regime ratio now (correctly) absorbs. The fix's job is honesty, not a bigger headline.

---

## 8. Out of scope (deliberately)

- Switching repos (httpx stays — fix is verified against its data; hatchling backend is the fallback only if a fixed run still hangs).
- A synthetic repo (last resort; reintroduces the "is it realistic?" objection).
- Changing the `chain_long` fixture or the compaction test design.
- A codex orphan reaper (dropped — §2, M4, S3).

---

## 9. Changelog vs v1 (what the review changed)

| v1 said | v2 says | why |
|---|---|---|
| **B:** 10s in-line grace-wait after `TimeoutExpired` | **B:** post-run reparse pass over all stream files | M1 — 10s catches 0–1 of 6 (flush latency 25s→799s); the wait is a near no-op live |
| Edit list = 2 files | Edit list = 3 files (+ `analyze_compaction_regime.py`) | M2 — the analyzer produces the headline and has its own `is_censored` |
| Clear `timed_out` (implied) | **Clean-clear** `timed_out=False` **and** `score["censored"]=False`; `turn_timed_out` checks recovery **before** `rc==124` | M2/M3 — three independent censored gates; clearing one leaves the others |
| **D:** mirror `reap_claude_orphans` for codex | **D dropped** — no reaper | M4 + §2 — would kill the grandchild before flush (breaks recovery) and could kill the user's desktop Codex |
| Cap → 600s | Cap → **1200s** | S1 — 2 of 6 turns exceed 600s |
| "no code edit needed" for the cap | set `CODEX_TIMEOUT_SEC` **in the `.ps1` before python** | S4 — frozen at import; Scheduled Task won't inherit shell env |
| "0 (or near-0) censored" | "0 **unrecovered** censored after reparse" | m4 — the cap alone can't reach 0; the reparse can |
| Headline framed as "cleaned" | Headline framed as **lowered to ~5.5×** | S2 — A+B+C reduce the number; say so |
