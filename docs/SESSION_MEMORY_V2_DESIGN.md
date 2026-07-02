# Session Memory v2 — Working-Set Pack + Correctness Spine (Design)

**Status:** DESIGN, pre-implementation. Produced 2026-07-02 from a 5-designer/3-judge/3-red-teamer
panel (full synthesized spec: `docs/SESSION_MEMORY_V2_PANEL_SPEC.json`) plus a zero-token offline
evidence pass (`research/stage0_mine_f1.py`) that **cancelled the warm-epoch track before any code
was written**. This document is self-contained for third-party review: it defines terms, cites every
number to an on-disk artifact, labels approximations, and ends with a reviewer checklist.

**Revision 1.1 (2026-07-02)** — incorporates 5 external-review findings: (RF-1/P1) the 34%
re-exploration figure was an upper bound over a broader pool than the pack can reach → the miner
now replays the *exact pack candidate rule*; the pack-addressable market is **29% (~26k/turn)**
and all dependent numbers/gates below use it. (RF-2/P1) Stage-B arms fixed to **Stage-A-only vs
Stage-A+pack** (was confounded). (RF-3/P1) **correctness parity is now a requirement of every
shipping outcome**, including STRONG PASS. (RF-4/P2) usable-test coverage gate + fallback added
for guard-hit contracts without collect-valid tests. (RF-5/P2) Stage-A acceptance extended with
operational gates (wall-clock/timeout/rc/orphans), not just tokens.

---

## 0. How to review this document

You do not need access to our conversation history. Everything load-bearing is either (a) defined
in §2, (b) cited to an artifact path in §3, or (c) explicitly marked as a **prediction/bet** with a
pre-registered falsification gate in §7–§9. The three highest-leverage review questions:

1. §3/§5: Does the evidence actually support *cancelling* the epoch track and *building* the pack —
   or have we over-read N=3 data from one fixture?
2. §7: Is the Stage-B "displacement bet" falsifiable as specified (pack-selected re-exploration
   bytes gate, ε ≥ 0.5), and are the pre-declared outcomes (incl. parity-in-every-outcome)
   actually decision-complete?
3. §6: Do the Stage-A correctness grafts close the "recall ≠ action" hole they claim to close, and
   can the new gate machinery itself introduce false reds?

---

## 1. Problem statement

**System.** PromptPilot (`prpt`) is a CLI control-plane that wraps coding-agent CLIs (OpenAI
`codex`, Anthropic `claude`) as subprocesses. A cheap small language model (SLM, nano/Haiku-class)
rewrites each user request, maintains structured session memory, and gates results with the target
repo's own tests. The frontier model (inside codex/claude) writes all code; the SLM only directs.

**The v1 memory strategy (shipped, PR #50)** runs every turn **cold**: a fresh `codex exec` per
turn — no conversation carried — with memory supplied by a **contract ledger** (SLM-distilled
`feature → {files, tests, symbols, obligation}` records) plus a **refactor guard** (an injected
"PREFER ADDITIVE — keep these public names working" checklist on risky turns), and an optional
per-turn **verify-gate** (allow-listed `pytest`, one corrective retry).

**Measured result of v1** (chain_long benchmark, §3-E1): vs the native alternative — one **warm**
codex thread resumed every turn — the cold loop achieves **~2.4× fewer TOTAL tokens** and strictly
more consistent correctness (3/3 contract-green vs 2/3 green + 1/3 catastrophic). But it pays two
structural costs:

- **P1 — the statelessness price.** The cold loop uses **0.87–0.95× MORE full-price (uncached)
  tokens** than warm resume. Warm threads ride the provider's prompt cache (only *new* content is
  billed full price); a cold turn re-pays for everything it touches. Decomposed, the dominant
  component is **re-exploration**: the agent re-greps/re-opens files that earlier turns already
  read — 12–52 tool calls per turn; ~34% of all full-price tokens touch previously-seen files
  (upper bound, §3-E4b), of which **~29% is reachable by the pack's own candidate rule**
  (§3-E4b′) — the design targets the 29%.
- **P2 — residual correctness holes.** The guard eliminates the *destructive-drop* failure class
  (removing a promised public API), but two residuals remain: (i) **recall ≠ action** — the guard
  can surface a contract that the agent then breaks anyway (observed: a migration that kept the
  API name but broke its behavior); (ii) the ledger records what was *asked*, not what was *done*,
  and has no way to retire stale contracts (no tombstones), no record of in-flight work across
  turns.

**The design question.** Can we (a) reclaim a meaningful share of the statelessness price and
(b) close the correctness residuals — **without** giving up the cold loop's measured advantages
(bounded totals, consistency, fail-closed safety), without touching provider internals (we are a
subprocess wrapper of official binaries; no MITM, no API re-implementation), and in an
architecture whose SLM-delegated decisions can *improve as SLMs improve* without redesign?

**Answer proposed here:** a two-stage design — **Stage A** (correctness spine upgrades) +
**Stage B** (a working-set "pack" that eliminates re-exploration) on the unchanged cold backbone —
plus an explicit, evidence-backed decision **not to build** the warm-epoch track that was this
design's original headline idea (§5.3).

---

## 2. Definitions

| Term | Meaning |
|---|---|
| **cold turn** | a fresh `codex exec` with no conversation history; context = what prpt injects + what the agent reads from the repo |
| **warm turn / native resume** | `codex exec resume <thread>` — the provider re-processes the whole prior thread, mostly at cache prices |
| **gross / TOTAL tokens** | all input+output tokens processed, cached or not (cache-independent; our headline metric) |
| **uncached tokens** | input tokens billed at full price (not served from the provider prompt cache) = "novelty" |
| **provider prompt cache** | server-side, prefix-keyed: leading tokens identical to a recent request are billed ~0.1× (Anthropic) / discounted (OpenAI). Lives on the provider's servers; a wrapper cannot control it |
| **contract ledger** | prpt's SLM-distilled memory: per-feature obligations + the files/tests/symbols that lock them (`prpt/memory.py`) |
| **refactor guard** | the injected directive + at-risk-contract checklist on refactor/overlap turns |
| **verify-gate** | allow-listed `pytest` scoped to relevant tests after an editing turn; red ⇒ one corrective retry quoting the failure (`prpt/verify.py`) |
| **pack** | (this design) a small, mechanically-assembled bundle of *fresh-from-disk file excerpts* injected per turn — the working set |
| **epoch** | (evaluated, **not built**) a run of consecutive turns sharing one warm thread, cut before the compaction cliff |
| **compaction cliff** | codex auto-summarizes its thread at ~233k context; measured correctness collapses cluster there |
| **kill-gate** | a pre-registered threshold that can *cancel* a build/experiment but never substitute for its validation |

**Measurement integrity context** (why the numbers below are trustworthy): all token figures use
**corrected accounting** — codex resumed-thread usage reporting is *thread-cumulative* (turn N
reports totals for turns 1..N); naive per-turn summing inflated every earlier published
native-arm number (e.g. a published 9.69× collapsed to ~1.71×). The harness now parses per-turn
deltas (`research/chain_test_v2.py::delta_cumulative_usage`, unit- and artifact-tested), and both
arms of the evidence below were re-derived from raw streams.

---

## 3. Evidence base

All artifacts under `research/data/chain_results_v2/codex/chain_long/` (this worktree) unless noted.
Fixture: `chain_long` — 13 dependent turns building a resilience layer in httpx (target repo
`C:/projects/httpx@d764bfc`), deliberately read-heavy and compaction-crossing. Tool: codex
(ChatGPT auth). All runs 2026-07-01.

| # | Evidence | Result | Source / caveats |
|---|---|---|---|
| **E1** | Cold loop (with_memory + verify-always, N=3) vs warm native (builtin, N=3) | TOTAL: **12.9M vs 30.9M (~2.4×)**; uncached: **1.19–1.30M vs 1.13M (0.87–0.95× against cold)**; correctness: cold 3/3 contract-green (1 run gate-repaired ×2, 1 partly by inaction, 1 clean) vs warm 2/3 green + 1/3 **23 test failures incl. an agent-authored crash** | per-run JSONs + endstates; N=3, one fixture, one day; loop 3/3 partially gate-assisted (treatment, disclosed) |
| **E2** | **Prefix-cache probe**: two cold `codex exec` sharing a 9.7k-token identical prompt prefix | **ZERO cache lift** vs a different-content control (4,992 vs 5,504 cached — block noise). Constant ~5k cached in *every* cell = codex's own preamble. Mechanism: a unique per-invocation `turn_id` UUID sits before user content | `scratchpad cache_probe.py`, 4 cells, N=1/cell but exact control match; ChatGPT-auth codex, one version — re-probe before relying on other paths |
| **E3** | Claude-side cache probe (2026-06-09, prior) | cross-process prefix reuse IS real on `claude -p` (1h TTL, write 2× / read 0.1×) but only via the two-message stream-json pattern | memory `claude_cli_cache_probe`; claude arm only |
| **E4a** | **Stage-0 epoch replay**: the panel's hardened warm-eligibility policy simulated over E1's real per-turn logs | warm fraction **3%** (gate ≥40%), mean epoch length **0.11 warm turns** (gate ≥3) | `research/stage0_mine_f1.py` §(a); simulation, kill-only use; approximations labeled in its docstring |
| **E4b** | **Re-exploration decomposition** (upper bound) | **33–35% of run uncached** = first-feed output of commands touching ANY file already read in earlier turns | same script §(b); chars/4 estimate; upper bound — includes files the pack would never inject (RF-1) |
| **E4b′** | **Pack-selected replay** (the pack's true addressable market): re-exploration restricted to files the *exact pack candidate rule* (targets ∪ guard-surfaced ∪ prev-turn-modified) would have injected that turn | **26–31% of run uncached, mean 29% (~25.9k/turn)**; intra-turn amplification **m ≈ 20.8** (gross input / first-feed) | same script §(b) rev 1.1; candidate proxies labeled (expected_files for targets; guard files parsed from the logged prefix) |
| **E4c** | **Warm growth curve** (from E1 warm runs, delta-corrected) | thread growth **90–240k tokens/turn**; max single-turn growth **238k > the 233k cliff** ⇒ calibrated safe epoch ceiling **negative**; warm runs crossed the cliff by T3–T4 | same script §(c) |
| **E4d** | **Epoch economics** | cold uncached/turn ~91.5k − pack-selected ~26.2k − warm marginal ~87.0k = **residual −21.6k/turn**: after the pack, warmth has nothing left to address. (Rev 1.1 uses the *smaller* pack-selected share — conservative for the kill: it leaves MORE for warmth, and the kill still holds) | same script §(d); warm marginal includes seed turns (bias flagged; see §10 R-9) |
| **E5** | SLM decision-quality retro-baselines | intent classification 100% on edit turns; guard recall 6/6 on refactor turns; false-fire 9%; **9/9 guard-fire turns surfaced ≥1 `tests/` anchor** (usable-test baseline for §6-1's coverage gate) | same script §(e); "0% on explain turns" is a division artifact (fixture has none); anchor presence ≠ collect-validity (measured in Stage A) |
| **E6** | Mechanical-vs-SLM memory control (2026-06-16, prior) | a *mechanical* bounded session was **1.27×/1.37× heavier** than the SLM-distilled one at equal correctness | memory `chain_auth_mech_vs_slm_session`; different fixture |

> **Reviewer note:** `stage0_mine_f1.py` has had one external methodology-review round (its P1
> finding — the pack-market over-count — is fixed and re-measured in rev 1.1); an internal
> line-level review pass was not completed. Treat the miner as reviewable source: its analyses
> are kill-only inputs, and the epoch cancellation does not hang on any single one — E4a
> (policy), E4c (physics) and E4d (economics) kill independently; the verdict requires all
> three to flip.

---

## 4. Design lineage (for auditability)

1. **Baseline idea:** "epoch rebasing" — run warm within short epochs, SLM cuts + reseeds from the
   ledger; pack as a secondary latency lever. (Motivated by E1's uncached inversion.)
2. **Panel (5 independent designer priors → 3 judges):** winner *PACK+EPOCH* (127/150) inverted the
   priority — pack primary, epochs demoted to a risk-gated, probe-gated track — on two arguments:
   warm's uncached edge is thin (~5–13k/turn) *and overlaps the pack's savings pool*; and provider
   cache retention at real inter-turn pacing (10–20+ min) may not even survive between turns.
3. **Red-team (3 lenses) + revision:** added Stage 0 (offline kill-gates *before any epoch code*),
   noise-margined gate discipline, evidence-verified warmth, and 12 risks.
4. **Stage 0 executed (E4):** all three epoch kill-gates failed ⇒ **epoch track cancelled.**
5. **User design principle:** *category-stable, capability-elastic* SLM delegation (§8).

---

## 5. The design

### 5.1 Architecture overview

**Unchanged backbone:** the v1 cold loop — per turn: SLM rewrite → inject ledger + guard →
fresh `codex exec` → git-diff detection → SLM distillation into the ledger → verify-gate.
Everything below composes onto it; every failure path degrades to exactly this measured-safe
configuration (fail-closed).

**Stage A — correctness spine** (§6): closes P2. Ships regardless of Stage B's outcome.

**Stage B — the working-set pack** (§7): attacks P1's dominant component (E4b). The only new
run-time machinery.

**Not built:** warm epochs (§5.3), injected-prefix cache discipline on codex (dead by E2).

### 5.2 Why a pack and not a bigger ledger injection

The ledger stores *obligations* (semantic state); the pack carries *content* (the working set:
the exact file excerpts the next turn will read anyway). E4b′ shows **~29% of full-price spend
(~26k/turn) is the agent re-fetching content the pack's own candidate rule would inject** —
obligations don't displace that; content does. The pack is rebuilt fresh from disk each turn, so
unlike a transcript it **cannot be stale**.

### 5.3 What we are explicitly NOT building, and when to reconsider

- **Warm epochs (codex):** cancelled by E4a+E4c+E4d. The physics is decisive on this workload
  class: single turns add 90–240k tokens of new content, so no epoch fits under the 233k cliff.
  **Re-open condition:** evidence from a *different workload class* — an interactive `prpt chat`
  profile with ~5–20k/turn growth could sustain 10+-turn epochs; collect that data before
  reconsidering. The panel's epoch spec is preserved in `SESSION_MEMORY_V2_PANEL_SPEC.json`.
- **Prefix-cache engineering of injected context (codex):** dead by E2 — a codex-internal
  per-invocation UUID upstream of user content makes cross-invocation prefix reuse impossible
  regardless of how canonically we serialize. **Claude arm:** the two-message stable-prefix
  pattern (E3) remains a future increment, after the codex verdicts land.

---

## 6. Stage A — the correctness spine (ships regardless)

Targets P2. All changes are to `prpt/verify.py`, `prpt/memory.py`, and the research harness; no
new run-time processes; acceptance gate **≤1.02× total tokens** vs incumbent.

1. **Guard-test targeting (the recall→action fix).** When the guard fires, the contracts it
   surfaced carry locked `tests`. The verify-gate additionally **runs those tests**, as a
   **second, separate pytest invocation** — never mixed into the base scope — after a batched
   `pytest --collect-only -q --continue-on-collection-errors` pre-validation. Unresolvable
   node-ids (SLM-hallucinated paths) are dropped and **counted** (`UNRESOLVED` rate logged);
   pytest rc=4 maps to a tri-state `targeting-invalid` — never red (no retry burn), never green,
   never masks the base scope. *This converts "the guard reminded the agent" into "the contract's
   own tests ran."*
   **Coverage gate + fallback (RF-4):** the residual hole is a guard-hit contract with NO
   collect-valid tests — the guard then reminds but nothing runs. (a) Acceptance requires
   **≥80% of guard-hit contracts carry ≥1 collect-valid locking test** on the validation
   fixtures (F1 baseline: 9/9 guard-fire turns surfaced a `tests/` anchor — but anchors are
   SLM-extracted, so collect-validity must be measured, not assumed). (b) **Fallback** when a
   guard-hit contract has zero valid tests: the gate widens to test-pair discovery over the
   contract's `files` (existing `discover_verify_targets`), and the contract renders as
   **UNLOCKED** in the guard text — telling the agent, honestly, that this obligation is
   unverifiable. (c) `UNLOCKED` and `UNRESOLVED` rates are first-class acceptance metrics and
   are tracked per run thereafter (they measure the remaining recall→action gap).
2. **Contract-quoting retries.** A red gate's retry prompt quotes the endangered contract text,
   not just the failing output.
3. **Tombstones with intent-conditioning + quarantine.** A contract may be retired only when the
   *user's* request deterministically expresses removal intent (never from agent narration); the
   op takes effect after the NEXT gate-green turn (one-turn quarantine, guard still fires during
   it); if the retiring turn deleted the contract's locking tests, scoped green is insufficient.
   Tombstones render under a separate 400-char cap so they can never displace active contracts.
4. **DONE-not-ASKED distillation.** The ledger distiller receives the turn's *realized* changed
   files and the gate verdict, recording what happened rather than what was requested; repair
   turns re-distill.
5. **Bounded WIP record.** A ≤400-char SLM-authored "unfinished work" note, overwritten (never
   merged) each turn, injected as *unverified narrative* — closes the "in-flight intent lost
   between cold turns" gap without trusting the SLM's prose.

**Validation:** unit tests + a seeded micro-fixture (a destructive-kwarg turn and an
additive-but-buggy turn must go red *at the causing turn* with contract-quoting retries; a
corrupted/hallucinated `tests` arm must degrade to base scope — never to skip, never to a false
red) + an N=3 smoke on the cheap chain_auth fixture.

**Acceptance gates (RF-5 — tokens alone would miss the real regression surface):**
- tokens: ≤1.02× total vs incumbent;
- **wall-clock:** added gate machinery (collect-only + second invocation) ≤ +60s p50 / +180s p95
  per editing turn on the validation repos; the added latency is *measured and reported*, not
  assumed;
- **hangs/timeouts:** every pytest invocation runs under the existing 300s cap; a timeout maps to
  `targeting-invalid` (never red, never a retry); acceptance requires **zero unresolved hangs**;
- **rc distribution:** full per-invocation rc histogram logged; any rc outside {0,1,4,5,124}
  fails acceptance (unknown failure modes must be classified before shipping);
- **process hygiene:** post-run orphan-process check (the Windows reaper failure mode); any
  orphaned test process flags the run;
- coverage: the §6-1 usable-test gate (≥80%) + `UNLOCKED`/`UNRESOLVED` rates reported.

## 7. Stage B — the working-set pack

### 7.1 Mechanics (v1 is deliberately mechanical — see §8 for why)

`prpt/pack.py::build_pack()`: candidate files = this turn's predicted targets ∪ guard-hit
contracts' files ∪ last turn's git-modified files; excerpts = windows around the ledger's known
symbols (reusing `_mentions`/the file regex); **fresh from disk**, content-hash + path/line
stamped; budget ~4–6k tokens with logged overflow; **edit-provenance aware** (a file changed by
the *agent's own last turn* re-excerpts; unchanged files re-use the identical text). Injected
after the ledger prefix. Failure of any part ⇒ pack omitted, turn proceeds as v1 (fail-closed).

### 7.2 The bet, stated falsifiably (rev 1.1 — numbers from the pack-selected replay, E4b′)

**Claim:** handing codex the working set suppresses re-exploration. **It is a behavioral claim
about the agent, not arithmetic** — codex may re-read files anyway (double-checking instinct).

**The arithmetic, shown** (per the reviewer checklist; all inputs measured):
let **D** = pack-selected displaceable first-feed ≈ **25.9k tok/turn** (E4b′), **P** = injected
pack ≈ 5k tok/turn (full-price — E2 means injected text can never cache), **m** = intra-turn
amplification ≈ **20.8** (every token entering a turn is re-fed on each subsequent internal call),
**ε** = displacement efficiency — the *behavioral unknown*: the fraction of D the agent actually
stops re-reading. Then per turn:
- gross saving ≈ m·(ε·D − P); against measured cold gross input ≈ 983k/turn:
  ε=0.75 → 0.70×; ε=0.55 → 0.81×; ε=0.40 → 0.89× ⇒ **predicted total band 0.70–0.90×**
- uncached saving ≈ ε·D − P; against measured cold uncached ≈ 91.5k/turn:
  ε=0.75 → 0.84×; ε=0.40 → 0.94× ⇒ **predicted uncached band 0.85–0.95×**
The bands are exactly as good as ε — which is what the pilot and kill-gate measure.

**Falsification gates (pre-registered):**
- **Pilot** (1 run, ~5% of experiment cost) before any N=5 spend.
- **Primary kill-gate (RF-1): pack-selected re-exploration bytes** — first-feed tool-output
  tokens on commands touching *pack-injected* files — must drop **≥50% vs baseline (ε ≥ 0.5)**.
  Secondary: total first-feed −25%. Measured in *bytes/tokens*, never tool-call counts (counts
  are schema-drift-fragile; the full `item.completed` type histogram is logged so drift is
  visible). The same replay code used in Stage 0 computes both, so the gate metric and the
  market metric cannot drift apart.
- **A/B (RF-2): arms are Stage-A-only vs Stage-A+pack** — N=5 each, interleaved,
  `VERIFY_ALWAYS=1` in BOTH arms. Stage A ships first and is in *both* arms, so the measured
  delta is the pack's alone (the E1 bundling lesson, applied to ourselves).
- **Pre-declared outcomes (RF-3): correctness parity — contract-green count ≥ the baseline arm
  AND end-state oracle parity — is a REQUIREMENT of every shipping outcome, including STRONG
  PASS.** A pack that saves tokens by anchoring the agent on partial excerpts while degrading
  edits FAILS regardless of token numbers. Given parity: STRONG PASS (total ≤0.80× AND uncached
  ≤0.90×) → pack default-on in v2 + epoch question stays closed; MARGINAL (total 0.80–0.90×) →
  pack ships **opt-in**, program ends there; FAIL (either tokens outside bands or parity broken)
  → pack shelved, v2 = Stage A only. No re-run discretion; one pre-committed N-bump (to 7) on
  the decisive metric only.

### 7.3 Instrumentation (ships with the first line of pack code)

Per-turn: pack contents + hashes; **pack hit-rate** (files injected that the agent did NOT
re-read) vs **re-grep rate** (injected but re-read anyway — the behavioral failure signal);
exploration-bytes; injected-prefix tokens. These are also the §8 delegation dashboard.

## 8. The capability-elastic principle (how the SLM's role grows)

**Design rule (user direction, 2026-07-02):** assign the SLM job *categories* suited to its
role — cheap, fast, every-turn, auditable: scoping, sensing, planning, distillation — and never
hard-code today's SLM limitations into the architecture. Today's mechanical choices are
**bootstrapping floors, not commitments**.

Concretely, every SLM-adjacent choice is a **schema'd decision slot** with a deterministic floor
implementation, an SLM implementation, and an outcome log:

| Slot | Floor (v2 default) | SLM growth path | Promotion evidence |
|---|---|---|---|
| `propose_pack` | mechanical candidate set + symbol windows | relevance ranking, cross-file selection, prefetch | pack hit-rate ↑ / re-grep rate ↓ under swap A/B |
| `predict_targets` | spec.target_files as-is | genuine next-turn file prediction | predicted-vs-edited precision (logged already; E5 baseline) |
| `capture_wip` / `propose_deprecate` | SLM now, quarantined + bounded | trusted more as measured | quarantine violation rate ~0 |

Promotion mechanism: a **pinned SLM-swap A/B** (same architecture, everything held constant)
whenever a materially better small model lands; promotion criteria pre-registered per slot.
**Boundaries that never move regardless of capability:** the SLM never writes product code;
verify stays deterministic pytest; fail-closed degradation; the compliance boundary (subprocess
of official binaries only).

## 9. Experiment discipline (applies to every gate above)

Fixture chain_long, codex pinned + version hard-asserted before every run; `CODEX_MODEL` explicit;
config.toml content-hashed (the desktop-app clobber failure mode); arms interleaved; run-validity
criteria pre-registered (ledger healthy ≥12/13 turns, no version drift, no orphan contamination);
`git clean -fdx` between runs (a prior run's `__pycache__` leaked compiled tests into a later
run — found in audit); endstate pytest gains `-rf` + new-file content capture (two enumerability
gaps found in audit). **Gate rule:** point estimate AND 90% CI upper bound must clear the
threshold; variance estimated from existing N=5 artifacts before gate registration; every
ambiguous band has a pre-declared outcome. Tokens reported as TOTAL (headline) + uncached
(separately); never dollars for codex.

## 10. Risks (post-Stage-0 register; owner = this design)

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| R-1 | **Pack displacement bet fails behaviorally** (codex re-reads anyway) | high | 1-run pilot before spend; PRIMARY kill-gate on pack-selected re-exploration bytes (ε ≥ 0.5); re-grep rate logged per turn |
| R-2 | Pack injects stale/wrong excerpts → agent anchors on them | med | fresh-from-disk each turn + content-hash stamps; provenance excludes agent-own-edit churn; fail-closed omission |
| R-3 | Guard-test targeting produces false reds (bad node-ids, env-dependent tests) | high | collect-only pre-validation; second-invocation isolation; rc=4 tri-state never red; UNRESOLVED rate gate in Stage A acceptance |
| R-4 | Tombstones retire live contracts (destructive amnesia returns) | med | user-intent conditioning; one-turn quarantine; deleted-locking-tests check; under-firing is the designed failure direction |
| R-5 | WIP note = hallucinated narrative anchoring | low | bounded, overwrite-only, rendered as unverified; injected on seeds only |
| R-6 | N=5 gates undecidable (noise) | med | CI-discipline + pre-declared ambiguous outcomes + single N-bump |
| R-7 | codex CLI drift (usage semantics flipped once already, ~06-10→06-14) | med | version hard-assert; semantics fingerprint; full item-type histogram logged |
| R-8 | Complexity creep via decision slots | low | a slot exists only where an SLM implementation is plausible within ~2 model generations |
| R-9 | **Stage-0 analyses biased** (e.g. warm-marginal includes seed turns; 0-length epochs deflate the mean; expected_files proxy) | med | miner under independent code review; epoch kill requires E4a+E4c+E4d to *all* flip; E4c (physics) uses real warm data, no simulation |
| R-10 | Fixture over-fit: chain_long is read-heavy and compaction-crossing by design | med | disclosed; pack gates re-run on chain_auth smoke; interactive-profile data explicitly out of scope until collected |

## 11. Open questions

1. Does codex suppress re-exploration when handed excerpts? (R-1 — the pilot answers.)
2. What fraction of contracts end up with unusable `tests` (UNLOCKED/UNRESOLVED) — the residual
   recall→action hole after Stage A?
3. Is the WIP note sufficient continuity for genuinely dependent turns? (3-turn dependent-task
   fixture in Stage A validation.)
4. Claude arm: does the two-message stable-prefix pattern hold at pack scale (~5k) and what does
   it save? (Deferred until after the codex verdicts.)
5. Interactive-profile telemetry: what is real per-turn content growth in `prpt chat`-style use?
   (Gates any epoch reconsideration.)

## 12. Reviewer checklist

- [ ] §1: Is the problem statement's framing of P1/P2 supported by §3, or selectively read?
- [ ] §3: Are E4's approximations (docstring of `stage0_mine_f1.py`) acceptable for a *kill-only*
      decision? Is any single approximation capable of flipping all three of E4a/c/d?
- [ ] §5.3: Is the epoch re-open condition specific enough to be actionable, or a soft never?
- [ ] §6-1: Can the second pytest invocation interact with the base gate (fixtures, cache, cwd
      state) in a way that manufactures reds?
- [ ] §7.2: Check the shown D/m/P/ε arithmetic against E4b′ — are the measured inputs credible,
      and is the ε∈[0.4,0.75] range for the bands defensible? What ε would YOU predict?
- [ ] §8: Do the decision slots add value now, or are they speculative generality (R-8)?
- [ ] §9: Is anything in the experiment plan still asymmetric between arms?
- [ ] §10: Which risk would you promote to high, and what gate would you add for it?

---
*Artifacts: panel spec `docs/SESSION_MEMORY_V2_PANEL_SPEC.json`; miner `research/stage0_mine_f1.py`
(+ its output in the session log); benchmark data `research/data/chain_results_v2/codex/chain_long/`;
corrected-accounting implementation `research/chain_test_v2.py` + `research/_test_cumulative_usage.py`.*
