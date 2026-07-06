# PromptPilot Campaign Charter — Pivot to CI/CD & Headless Jobs

**Status:** ACTIVE charter (2026-07-04). Supersedes the interactive/TUI session-memory direction.
**Owner decision:** pause the TUI/interactive-session battle; make `prpt` genuinely useful for
CI/CD pipelines and other headless/batch coding jobs.

This document is the campaign's charter: the thesis, the evidence that forced the pivot, what
carries over vs. what is retired, and the **pre-registered kill-gates** the next experiments must
pass. It follows the project's measurement discipline (pre-registered decision bands, provenance
files, N-rules) — the same discipline that has retired three prior directions.

---

## 1. Thesis

> **We cannot make cold exploration cheaper — three independent kill-gates prove the agent's
> re-read reflex is untouchable from the prompt. What we *can* do, in the regime where every agent
> is cold anyway, is (a) shrink how much exploration the task demands, (b) carry the few durable
> facts that steer decisions, and (c) enforce with tests — all at near-zero carrier cost.**

The interactive/warm regime is the platform's home turf (a live thread carries prior observations
in-context at cache rates). The cold/stateless regime — CI/CD, scheduled jobs, batch fixes — is
where the platform's native memory is *absent*, and where our validated assets are the only lever.
So we move to where our measured wins live.

## 2. Why the pivot (evidence, not preference)

### 2.1 Three kill-gates: injected context does not suppress re-exploration

| carrier | what it injected | result |
|---|---|---|
| Stage B **pack** | fresh working-set file content (hash-stamped) | **KILL** ε = −0.70 (70% *more* rule-redo) |
| **E2** delta-note | a mechanical "what changed" directive | pre-empted — redo is behavior-locked |
| V3 **journal** | append-only first-person self-transcript on the pinned cache | **KILL** ε = −0.34 to −0.59 |

The re-read reflex is **behavior-locked**: native resume, carrying its *entire* transcript
in-thread, still shows a ~67% redo share — essentially identical to the bounded arm's 66.9%.
Content injection cannot move it; only *evidence* (a failing test, a traceback) directs the agent,
and only *fewer files to look at* (scoping) reduces the volume.

### 2.2 The regime analysis

Re-exploration cost is governed by **pacing × tree-stability**, not by the task label:

- **Warm pole (interactive, same-day, own edits):** native resume gets "channel 2" for free —
  prior observations remembered in-context, served at cache rates, with causal warrant (the tree
  changed mostly through the agent's own edits). gpt-5.5 cache retention is 24h, covering human
  pacing. **Native wins here by default; our prompt-side carriers lose to it (§2.1).**
- **Cold pole (CI/CD, scheduled, cross-commit, stateless):** every job is a fresh trigger. There is
  no thread to resume; a resumed thread would carry *stale* observations (the repo moved between
  jobs) = confident-wrong memory, worse than none; past 24h the cache is dead → full-price cold
  re-send of the whole transcript. **Every agent pays the exploration floor and native is not in
  the tournament.** The question becomes "what adds value on top of raw cold prompts?" — which is
  exactly the set of things we have validated.

## 3. What carries over (validated, cold-regime-native)

1. **Pointed rewrite (grounding).** The one prompt-side intervention class that survived every
   kill-gate: *evidence directs behavior, content does not.* A raw trigger becomes a scoped task —
   named target files, the locking tests, explicit requirements. Reduces the exploration *need*,
   not the reflex. (Cross-chain evidence: ~3× cheaper per success vs raw + native resume.)
2. **Verify gate + contract gate + bounded verify-repair.** Test-based enforcement is CI's native
   currency and needs no persistent session. (`prpt/verify.py` shipped; the repair mechanism
   exists.) The gate's targets are derived from the **diff**, not a memory store.
3. **SLM as the cheap two-stage bracket** (§5).
4. **Bounded/cold prompts** as the honest cost model — in a stateless regime, carrying a growing
   context is stale, full-price, or both.
5. **Measurement & compliance posture.** Pre-registered gates, provenance files, the audit culture;
   the `--bare` API-key path as the conservative headless lane; honest token reporting (Claude arm
   via native OpenTelemetry, codex arm via `--json` usage) with no MITM.

## 4. What is retired for CI (architecturally wrong for the regime, not merely shelved)

- **The ProjectState ledger.** It was built to fix contracts falling out of the recency window on
  long *interactive* chains. **CI already has a better ledger: the repository itself** — tests *are*
  the contracts, intent lives in commit messages / PR descriptions / `AGENTS.md`, all repo-native,
  runner-durable, tool-agnostic, and enforced by the pipeline regardless of what any agent
  remembers. Durable facts an agent learns should be *written into the repo*, not into a temp-dir
  sidecar only `prpt` reads (which dies with every ephemeral runner anyway). **Survives only as:**
  - (a) the guard's file/symbol → locking-test matching, **re-pointed at the diff** = impacted-test
    selection (mechanics, not memory);
  - (b) the ledger scoped to **within a single long headless job** (that is the chain regime again —
    same process, no cross-job storage problem).
- **The pinned thread, journal, pack, epochs, delta-note.** All existed to make a growing
  injected/carried head cacheable across invocations. With no carrier, there is nothing to pin;
  CI arms run plain cold `exec` (simpler and the honest cost model). Preserved flag-gated on the
  research branches with their specs; resumable if the regime ever changes.

## 5. The SLM's role in CI

The SLM stops being a *memory engine* and becomes the **cheap stage bracketing the expensive cold
agent call** — everything worth doing at nano-cost before and after:

- **Before:** log-triage / evidence distillation (10k-line log → the 15 lines that matter — the one
  input class the campaign validated); task grounding / rewrite (§3.1, conditional on **KG-1**);
  and a **triage gatekeeper** — classify the failure (flaky→retrigger, infra→alert, lint→mechanical,
  real-bug→invoke agent) so a nano-call decides whether to spend a 100k-token agent invocation at
  all. That prevention is the best token-leverage in the system.
- **After:** repair-evidence distillation into the retry prompt (assertion failures, not test source
  — the teach-to-test guardrail); reporting & repo-memory writing (PR descriptions, commit messages,
  `AGENTS.md` suggestions — the distiller's skill, retargeted at the repo and humans).
- **Internal, unchanged:** judging/scoring in our own benchmark harness.

Through-line: **the SLM points and compresses; it never carries.** Doctrine stays "pitch in tokens"
— SLM overhead is ~0.2%-class, and in CI the gatekeeper sharpens that (it prevents agent calls, not
just cheapens them).

## 6. Pre-registered kill-gates (must pass before the wedge is claimed)

Both are cheap and currently **unmeasured** — our rewrite/contract evidence all comes from vague
multi-turn chains, not the pointed single-shot tasks CI actually produces.

- **KG-1 — rewrite value on already-pointed tasks.** Real CI tasks arrive with a traceback (already
  scoped). Does the grounding rewrite still add value when the input is not vague?
  *Design:* the admitted CI fixture corpus (docs/CI_FIXTURES_SPEC.md; both arms receive the SAME
  captured pytest evidence as input), arm A = raw-cold codex, arm B = `slm-openai-v2` rewrite +
  codex; pilot = every admitted task × both arms × 1 rep (6×2 at corpus v1).
  *Metric:* **ε₁ = 1 − (B tokens-per-green / A tokens-per-green)**, where tokens = codex uncached
  + ALL SLM tokens (all-in — the rewrite arm pays its own engine), and tokens-per-green =
  Σ tokens / Σ green over the arm's tasks.
  *Pre-registered bands:* **CONTINUE ε₁ ≥ 0.15** with green-count(B) ≥ green-count(A);
  **KILL ε₁ ≤ 0 OR green-count(B) < green-count(A)** (the rewrite-for-pointed-tasks claim dies;
  wedge narrows to gates + triage); **REPLICATE otherwise** — one more rep of the full corpus,
  decide on pooled means, borderline again = KILL. Correctness is scored by each task's recorded
  `pytest_argv` (never a re-derived invocation).

  > **METRIC AMENDMENT (2026-07-05 — registered BEFORE any KG-1 v2 outcome existed; the
  > amending commit is the proof).** For v2 onward the **primary gated ε₁ uses codex-uncached
  > (LLM) tokens only**. Rationale: the SLM is nano-priced (~0.02× gpt-5.5), so the original
  > 1:1 all-in definition over-weights its tokens ~50×; the accounting doctrine has always been
  > "SLM as its own reported line, never folded into the LLM headline." SLM usage is now
  > MEASURED (in-process tap, classify + rewrite passes) and reported alongside as **all-in
  > 1:1** and **cost-weighted 0.02×** secondary lines, so an SLM-hungry rewrite cannot hide.
  > Bands unchanged, applied to the primary metric. The v1 run was gated on the original
  > all-in definition and its recorded verdict stands as-decided (see OUTCOMES caveats: with
  > measured SLM accounting, v1's all-in ε₁ ≈ +0.13 would have landed REPLICATE; its
  > codex-only ε₁ = +0.374).
- **KG-2 — impacted-test selection.** Does diff → locking-tests selection beat full-suite /
  agent-chooses? *Metrics:* selection recall on seeded defects, suite wall time, escaped-red rate.
  Largely replayable **offline** against the corpus before any live spend.
  *Pre-registered bands:* **CONTINUE** if selection catches **6/6** seeded defects (zero escapes)
  AND mean selected-suite wall ≤ **0.5×** full-suite; **KILL** if any seeded defect escapes
  selection after at most **one** matcher iteration (an enforcement gate that misses known bugs is
  worse than the slow full suite); else iterate the matcher once and re-judge.

> **OUTCOMES (2026-07-04):**
> **KG-1 = CONTINUE** — greens 6/6 both arms (0/12 runs touched tests), ε₁ = +0.237 all-in /
> +0.371 cost-weighted. Finding is BIMODAL: rewrite mildly hurts on already-localized evidence
> (~0.87×) and wins 1.2–21× on hard-localization tasks → triage-then-rewrite hypothesis (KG-1.5:
> two complementary cheap pre-run signals separate 6/6 offline — over-fit caveat, corpus-gated).
> Ship default: always-rewrite. (`research/kg1_rewrite_value.py`, `research/kg1_data/`)
> **KG-2 = CONTINUE** — final matcher = static ∪ **line-scoped** coverage (the one allowed
> iteration, spent on precision after file-level coverage hit 0.97× wall): **6/6 catch, 6/6
> strict, mean wall 0.24×** vs the 124s full suite. Static-only was 5/6 (missed the
> `_utils.py`→queryparams mapping); file-level coverage 6/6 but ~whole-suite broad; line-scoping
> gives both. Caveat: hot-path lines stay broad (url-port's changed line → 16 files, 115s).
> (`research/kg2_test_selection.py`, `research/kg1_data/kg2_result.json`)
>
> **CORRECTIONS & CAVEATS (2026-07-05, from the gate-script audit — both verdicts SURVIVE):**
> **KG-2 wall corrected 0.24× → 0.34×.** The published mean contained a fake sample: the
> config-timeout-tuple timed run ABORTED at collection (selection included `tests/conftest.py`
> via the symbol grep → pytest rc=2, 1.9s, zero tests executed). Honest re-run with the fixed
> selector (test_*.py only) + cold-cold timing: **6/6 catch, 6/6 strict, mean wall 42.4s =
> 0.34×** of the 123.0s full suite (config row real wall 78.1s — the `Proxy` symbol legitimately
> pulls the slow proxies suite) → CONTINUE. Artifact: `kg1_data/kg2_result_c1.json`
> (`kg2_result.json` = the superseded v1 record, kept immutable). t2_line alone: 6/6 strict at
> 6.2 files — the wall cost lives in the static tier's broad symbol matches (Phase-3 lead).
> *Iteration honesty:* the artifact records `iteration: 2`; "one allowed matcher iteration"
> holds only under the reading that the designed two-tier static∪coverage was matcher-0 (no
> committed pre-registration discriminates, single-commit history). The 16-task v2 re-score
> with the NOW-frozen matcher is the clean confirmation.
> **KG-1 caveats:** (a) *Seed-leak confound* — v1 applied seeds as uncommitted edits, so arm
> B's SLM repo-context saw the seed diff + file name via `git status`/`git diff` (an oracle
> absent in real CI, where the failing state is committed); magnitude and the bimodal narrative
> are confounded; protocol v2 commits seeds. (b) *SLM tokens were estimated, not measured* —
> the tap now measures ~2× the estimate (10,323 vs ~5.2k on the dry-run task); with measured
> accounting v1's all-in ε₁ ≈ +0.13 = REPLICATE-grade; codex-only ε₁ = +0.374 is unaffected by
> accounting (still leak-confounded). Under the pre-registered REPLICATE rule, the KG-1 v2 run
> (clean protocol, codex-only primary metric per the §6 amendment) is the deciding replicate.
> (c) *"0/12 touched tests"* had no runner check behind it; re-verified 2026-07-05 by a full
> transcript scan of both write channels (patch envelope + shell writes) — the claim stands;
> the v2 runner enforces and persists it per run. (d) The +0.371 cost-weighted variant is now
> computed in code (was session arithmetic); the "0.97× file-level wall" figure came from an
> unrecorded intermediate run — treat as approximate.
>
> **OUTCOMES v2 (2026-07-06 — the deciding replicate, protocol v2, 10 new tasks × 2 arms):**
> **KG-1 v2 = KILL** (pre-registered codex-only band, ε₁ = −0.038 ≤ 0). Raw 50,328 vs rewrite
> 52,228 codex-uncached tok/green at 10/10 green parity BOTH arms; all-in 1:1 = −0.267;
> cost-weighted 0.02× = −0.042. Validity: 0 censored, 0 errors, 0 integrity violations, SLM
> measured on 10/10 rewrite rows (~11.5k tok/task). Deconfounded per-task ratios: wins
> 1.15–1.72×, losses 0.40–0.99× — **v1's 5–21× magnitudes were the git-diff leak**, and the
> honest rewrite nets ≈ zero on codex tokens while paying its own SLM. The rewrite and the
> always-rewrite ship default are RETIRED for pointed CI tasks. (Interactive/vague-prompt
> rewrite evidence is a different regime, untouched here.)
> **KG-1.5b out-of-sample = classifier DEAD.** Frozen predictions (7W/3L, commit 07ee460)
> scored **4/10** against the frozen label rule — below every trivial baseline (always-WIN
> 5/10, always-LOSE 5/10, fanout-only 5/10); triage-policy EV (54,697 tok/green) is WORSE than
> both always-raw (50,328) and always-rewrite (52,228) — it skipped the two biggest clean wins
> (gzip 1.72×, noproxy 1.19×). The v1 "6/6 separation" is confirmed over-fit; the SLM triage
> gatekeeper leaves the roadmap absent a new pre-registered result.
> **RCA retractions (pre-committed checks, prompt audit of all 10 rewrites):** the mid-run
> "anchor-loss" and "spec-inflation" mechanisms FAILED as predictors (3 of 4 anchor-dropping
> rewrites won; obligation count anti-correlated with losing) and are retracted. At clean
> effect sizes, per-task N=1 ratios are variance-dominated (the task-level analogue of the
> N=3-chain-noise rule). Surviving hypothesis (post-hoc, UNPROVEN): the SLM pays as an
> **evidence DISTILLER on long/noisy CI output** (3 of 5 wins had ≥2× compression of ≥2.3k-char
> evidence) — any KG-3 test of it requires a repeated-measures design (N≥2–3/task).
>
> **PRE-REGISTRATION — KG-2 16-task out-of-sample re-score (2026-07-06, registered before the
> run):** matcher FROZEN as committed at 12045dd (t1 static test-file-only ∪ t2 line-scoped
> coverage; zero changes permitted for this re-score). Bands, judged on all 16 admitted tasks
> with cold-cold timing: **CONFIRM = catch 16/16 AND mean primary wall ≤ 0.5×** the full
> suite; any escape or wall-fail = the honest out-of-sample answer and feeds Phase-3 matcher
> design (line-scoped-only or pruned-static) — no matcher iteration is permitted on this
> corpus generation. The v2 subset (10 tasks) is reported separately; any aborted timed
> selection → INVALID. Known risk, stated in advance: `_models.py` tasks may balloon the
> static tier's symbol matches (Response/Headers grep breadth) → wall-fail with clean catch is
> a live possibility and would redirect Phase 3 to the line-scoped tier.
>
> **RESULT (2026-07-06, `kg1_data/kg2_result_all.json`): CONFIRM — 16/16 catch, 16/16 strict,
> mean primary wall 60.1s = 0.48× of the 124.4s full suite** (passes the pre-registered band by
> 2pp; zero aborts; frozen matcher, zero iterations). **The selector's recall is now validated
> out-of-sample** — the one v1-era "6/6" that survived the campaign's deconfounding week.
> Tier data (Phase-3 design input, not a gate change): **t2 line-scoped ALONE = 16/16 catch,
> 16/16 strict at 5.2 files mean** — perfect recall with the smallest selections; t1 static =
> 15/16 catch (the known `_utils`→queryparams call-flow miss) and is what drags the union's
> wall (pre-declared `_models.py` symbol breadth materialized: 23–24-file selections, walls to
> 123s ≈ full suite). Phase-3 product decision this licenses: **line-scoped coverage as the
> primary selector, static tier demoted to a pruned fallback** (for changes outside the
> coverage map, e.g. new files) — that configuration's implied mean wall is far inside the
> band. The scheduled full-suite run remains the pipeline backstop, as designed.

### KG-3 — additive verified-diagnosis note (PRE-REGISTRATION, 2026-07-06, before any run)

The KG-1 v2 rewrite KILLed because *replacing* evidence destroyed the verification anchor
(`docs/FINDING_REWRITE_TOKEN_ECONOMICS.md`). KG-3 tests the loss-flooring redesign: keep the
verbatim traceback + the same verification pin in BOTH arms, and in arm B APPEND a tightened,
grep-verified triage note (`research/kg3_note.py`: 1-line mechanism + ≤1 verified source file,
hedged). *Desk gate cleared:* step-0b (`kg1_data/kg3_step0b.json`) found the SLM's location
diagnosis correct 8/9 on the gappy-evidence subset — necessary (rules out garbage notes), not
sufficient (location was never the failure mode).

- *Design:* `research/kg3_distill_run.py`. Arm A = instruction + verbatim evidence + pin;
  arm B = A + the note. 16 admitted tasks × 2 arms × **N=3 reps** (repeated measures — the
  non-negotiable fix for the N=1 variance that ate two KG-1 v2 RCA mechanisms), committed-seed
  clean tree (no diff leak), cold `codex exec`, scored by recorded `pytest_argv`.
- *Primary metric:* pooled **codex-uncached** tok/green over ALL rep-runs, ε₁ = 1 − (B/A) —
  the same LLM-only metric amended for KG-1 v2. SLM measured and reported (all-in + 0.02×),
  never folded into the gate.
- *Pre-registered bands:* **CONTINUE ε₁ ≥ 0.10** with green(B) ≥ green(A); **KILL ε₁ ≤ 0 OR
  green(B) < green(A)**; **REPLICATE** otherwise (borderline again = KILL). Secondary, reported
  not gated: per-task paired B-vs-A win-rate across reps, and the gappy-evidence subgroup
  (frozen module-mismatch) — the hypothesis predicts the note earns on gappy, not crisp, tasks.
- *Note-construction rules frozen here* (`research/kg3_note.py`, no post-hoc tuning): a single
  `gpt-5.4-nano` diagnostician call (input = evidence + the httpx source-file list ONLY — no
  diff) returns strict JSON `{mechanism: ≤1 sentence root cause in the source, not a test-name
  restatement; file: one path or ""}`. The note = hedged header + the mechanism line + the file
  line ONLY when `file` is a non-test `httpx/` path existing at HEAD (grep-verified, at most
  one); empty note (→ B degrades to A) on call/parse failure or empty mechanism. *Desk-gate
  note:* step-0b cleared on the `slm-openai-v2` target_files (8/9 gappy); the shipping note
  switched to the dedicated diagnostician after the dry-run showed the rewrite's first sentence
  was boilerplate — its file guess re-checks at **12/16** (still ≫ the 50% threshold) and its
  mechanism is a correct diagnosis even on most file-misses. Wrong-file lines are low-risk in
  the append form: evidence is intact, so codex verifies against the real traceback (the
  pack/journal "agents skip ignorable injected content" result).
- *Validity guards inherited:* protocol-v2 committed-seed / measured-SLM / censoring /
  integrity guard / arm-size assertion → INVALID; append-only artifacts.
- *Scope:* a CONTINUE adds at most an opt-in `--distill` flag to `prpt ci`; it does NOT change
  the raw-prompt default, which ships regardless. A KILL retires the SLM from the pre-agent
  path entirely.

**Then the flagship optimization:** the **reasoning_effort sweep** — the biggest known cost lever
(~23× high-vs-minimal), regime-agnostic, and easiest to exploit in headless batch (no latency
watcher). Run it on the new CI fixtures; it becomes the pivot's headline benchmark.

## 7. Phased plan

- **Phase 0 — close out the old campaign (DONE, 2026-07-04):** V3 KILL accepted; results + pivot
  pinned to memory; V3 spec banner; research branch committed.
- **Phase 1 — groundwork ($0, design + offline):** this charter; CI-shaped fixture corpus
  (single-shot pointed tasks seeded/harvested in httpx, scored tests-green + tokens); ledger
  re-scoped to within-job in docs/config; guard → impacted-test-selection prototype (offline).
- **Phase 2 — the two kill-gates (cheap):** KG-1, KG-2. Decide the wedge shape.
- **Phase 3 — product (`prpt` headless mode):** `--bare` default, non-interactive, JSON verdicts +
  exit codes, honest token reporting; verify + bounded-repair as the first-class CI feature;
  impacted-test selection if KG-2 is green; CI recipes (e.g. a GitHub Actions example).
- **Phase 4 — reasoning_effort sweep** on the CI fixtures (flagship number).

## 8. Scope fences (what this pivot does NOT claim)

- It does **not** claim we beat native in the warm regime — we don't, and we stop trying.
- It does **not** ship cross-job SLM memory — the repo owns cross-job continuity.
- The wedge's value on pointed tasks is **hypothesized, not measured** until KG-1/KG-2 clear.
- The interactive path is **paused, not deleted** — `prpt`'s recency session stays shipped-and-frozen;
  the cache-side research (pinning, journal, pack) is preserved on branches, resumable.

## 9. Provenance / linked records

- Kill-gate records (memory): `with_memory_v3_journal_result.md` (journal ε=−0.34/−0.59),
  `with_memory_v2_design.md` (pack ε=−0.70), `codex_prefix_cache_probe.md` (pinning + version
  semantics), `output_token_reduction_research.md` (reasoning_effort ~23×),
  `campaign_pivot_ci_headless.md` (this decision).
- Research branch: `feat/with-memory-v3-journal` (`1c07171`) + pilot data
  `research/data/t2_journal_pilot/`.
- Prior release scoping: `verify_repair_release_plan.md` (session-memory stack = research-only;
  prpt ships recency session + safe verify-gate).
