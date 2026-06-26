# Session Memory — Verify-and-Repair Mechanism (Stage 2) — v2

**Status:** design plan, v2 (2026-06-26). v2 incorporates three independent reviews (see §13
Provenance). Supersedes the diff-based verifier sketch in `SESSION_MEMORY_ROADMAP.md §"Stage 2"` —
the N=10 finding (§2) forces an **execution-based** verifier. Companion: `SESSION_MEMORY_ROADMAP.md`
(gating), `memory_ab_result` memory file (the A/B + N=10 data), `session_memory_prior_art` memory
file (deep-research borrowables).

**Milestone (PR#49) — explicit, to avoid over-claiming.** *Landed in this PR:* the primitives
(`research/verify_repair.py`), the end-to-end orchestration hook `verify_and_maybe_repair`, and its
**gated wiring into `chain_test_v2.run_chain_once`** (opt-in `VERIFY_REPAIR=1`; **off by default**, so
the existing `with_memory` arm is byte-for-byte unchanged), plus unit + integration tests (the hook
is driven by a stub repair-runner — no model). *NOT in this PR:* the **live A1-vs-B benchmark run**
(real codex repair turns; a paid, multi-window run that §9 marks *directional/underpowered*). So the
loop is **wired and tested**, but the empirical B-vs-A1 result is the remaining, separate step.

---

## 0. Background (for a reader new to this project)

**What PromptPilot is.** A thin control layer that wraps a coding-agent CLI (OpenAI's `codex` or
Anthropic's `claude-code`) and drives it across a multi-turn task. A small/cheap language model
(**SLM**, ~gpt-5-mini/nano class) does two jobs: it rewrites the user's prompt, and it maintains
"session memory" between turns so each turn need not re-read the whole transcript.

**The experiment ("chain_long").** A reproducible benchmark: over **13 sequential turns** the agent
builds a "resilience layer" onto **httpx** (a popular Python HTTP client) one feature at a time —
per-request timeout overrides, retry/Retry-After handling, connection-pool sizing, elapsed-time
stats — then, in the last turns, **refactors** all of it into a single `ResilienceConfig` object.
Each feature is a numbered obligation **C1–C13** (e.g. C1 = a per-request `connect_timeout` override
on the client; C12 = introduce the `ResilienceConfig` dataclass; C13 = migrate the clients onto it).
The 13-turn chain is run N times to measure variance.

**The memory under test.** Two strategies are A/B-compared:
- `with_session` — a plain recency window (the last few turns), used as the control.
- `with_memory` — a structured **ledger** the SLM distills from the agent's work: each feature →
  `{files, tests, symbols, contract}`, where **contract** is a one-line statement of the public
  promise. Before a refactor turn, a **guard** is injected into the prompt listing the contracts the
  turn must preserve (additive-bias: "keep old names, ADD the new form alongside").

**The failure being prevented ("the tax").** When the final refactor (C13) folds the per-feature
options into `ResilienceConfig`, it can **drop** the earlier public names — breaking the call-sites
and tests that used them. An orphaned/broken prior contract is a *destructive-migration tax*.

**How a contract is judged (non-circular).** Not by asking an LLM. We take the agent's final code
diff, **apply it to a clean copy of httpx**, then **run a probe or test** that exercises the
contract: pass = preserved, exception = broken. (httpx ships `MockTransport`, an in-memory fake
server, so a probe can exercise a request with no network.)

**Glossary**

| Term | Meaning |
|---|---|
| SLM | small/cheap language model that drives the memory layer |
| codex / claude-code | the coding-agent CLIs being driven; `codex --json` output is lossy and **encrypts the agent's reasoning** |
| chain_long | the 13-turn httpx resilience benchmark |
| C1…C13 | the 13 incremental feature obligations of chain_long (C12 = add `ResilienceConfig`; C13 = migrate to it) |
| ledger / contract | structured per-feature memory; *contract* = the one-line public promise |
| guard | before-turn prompt text listing contracts to preserve; *additive-bias* = "keep old names, ADD the new form alongside" |
| tax | a broken/orphaned prior contract after a refactor (see formal definition in §4) |
| recall ≠ action | the agent is *reminded* of a contract yet still breaks it |
| oracle / forensic | the non-circular check: apply-diff-to-clean-baseline + run a probe/test |
| run9 | the single failing run (of 10) analysed throughout this doc |
| MockTransport | httpx's in-memory fake transport — lets a probe exercise a request without a network |
| `d764bfc` | the clean httpx baseline commit the chain starts from (vanilla httpx, none of C1–C13 present) |

---

## 1. Problem statement

PromptPilot's `with_memory` layer keeps a per-feature **ledger** and injects a before-turn **refactor
guard** surfacing prior contracts the current turn must preserve. The failure it targets is the
**destructive-migration tax**: a late consolidation turn drops earlier public names, **orphaning**
the call-sites and tests that depended on them.

Two findings shaped the problem:

1. **"recall ≠ action."** Surfacing a contract (recall) does not guarantee the agent preserves it
   (action). The OLD `"migrate to the new design"` guard was a near-no-op (4/5 runs taxed). The
   **additive-bias reword** is *correlated with* the destructive-drop class disappearing (validated
   on the oracle fixture; 0/10 drops on chain_long). We say *correlated*, not *caused*: codex
   encrypts reasoning, so we cannot prove the agent acted because of the guard (see §2 ceiling).

2. **A prompt can govern the *shape* of a migration, not the *correctness* of the implementation.**
   The decisive finding (§2): even with the additive directive provably surfaced and followed on
   shape (no names dropped), the agent can introduce a **logic bug that breaks a *kept* contract at
   runtime**. No guard wording can prevent that — it is an *execution* problem, not a *memory* one.

**Therefore:** the cheap guard has done its job (eliminates the drop class); the residual requires a
**post-turn verify-and-repair loop that runs the contract.** Throughout, the intervention under test
is **"verify + repair"**, NOT "memory" — memory supplies the obligations; execution verifies them;
repair attempts to restore them.

---

## 2. Latest finding — the N=10 hardening run (2026-06-26)

Forensic = apply each run's end-state diff to a clean `d764bfc` baseline + execution probes +
independent re-verify. All 10 `with_memory` runs (additive-bias guard, codex-cli 0.140,
`slm-openai-v2` normalizer), 0 censored turns.

| Arm | Taxed | Rate |
|---|---|---|
| **with_memory (additive guard), N=10** | **1/10** (run9) | **10%** |
| with_session (guard-agnostic control), N=5 | 1/5 | 20% |
| with_memory (additive guard), N=5 prior snapshot | 0/5 | 0% |
| with_memory (OLD "migrate" guard), codex 0.130 | 4/5 | 80% |

> **Confound warning (do not over-read the table).** Only the **within-arm with_memory N=10 = 1/10**
> number is clean. The cross-row deltas are **uncontrolled**: the 80%→10% improvement bundles the
> guard reword with a **codex-version change (0.130 → 0.140)** and a **fixture that trends additive**
> (the control itself was 4/5 additive *without* any guard). Treat cross-row comparisons as
> directional only.

- **The guard's *target* tax (destructive kwarg DROP) = 0/10.** All 10 migrations were additive in
  shape — zero removed public-kwarg lines. The old guard's 4/5-drop failure mode is gone.
- **run9 is a *different* class — "additive-but-buggy":** it KEPT every public name and ADDED
  `ResilienceConfig` (directive provably surfaced on its t13 via instrumentation), but introduced a
  logic regression in `BaseClient._merge_timeout_extensions` (`httpx/_client.py:379`):
  `dict(**extensions, timeout=timeout_extensions)` when `'timeout'` is already in `**extensions`
  → `TypeError: dict() got multiple values for keyword argument 'timeout'`. The C1/C2/C3 per-request
  override contracts (`connect_timeout`/`read_timeout`, sync+async) are **accepted but crash when
  invoked.** = **"kept the name, broke the behavior."**
- **Detection method matters (the design driver).** run9 was caught by an **execution probe** that
  *issued a request* (`client.get(url, connect_timeout=0.5)` via `MockTransport` → reproduced
  `TypeError` at `:379`, frames `get → request → send → _merge_timeout_extensions`; the run's own
  pytest = 23 failed at `:379`). **A diff/substring check would MISS it** — run9 has **zero removed
  lines**, so "drop-without-positive-migration = VIOLATED" sees no drop and passes it clean.
- **recall ≠ action, sharpened.** run9 is the cleanest datapoint: the guard was *at the decision
  point* and the agent *acted on it* (preserved the surface), yet the action was incomplete on
  implementation. The guard governs preservation **by name**, not **implementation correctness**.
- **Tokens:** with_memory N=10 **14.81M/run** gross vs with_session **15.89M** = **~1.07× lighter**,
  *down from the 1.14× N=5 snapshot* — run-to-run variance (gross is cache-independent). At this N
  the advantage is **directional, not significant**. This figure is the **A1 arm only**; the
  verify+repair arm (B) carries extra cost (§8).
- **Attribution ceiling.** The directive was *verified-surfaced* on t13 in all 5 instrumented runs
  (6–10) — but codex encrypts reasoning, so this is **"surfaced-and-correlated," not "caused."**
  run9 *is* the proof of the ceiling: present, yet behavior broke.

**The single empirical caveat that shapes the experiment design:** this entire pivot rests on **one
failing run (run9, N=1)**. Success criteria (§9) are therefore anchored on a deterministic
**fixture**, not on the underpowered chain_long N=5 comparison.

---

## 3. Driving design decision

> **Verification is execution-based, not diff-based.** Each at-risk contract is checked by *running*
> a probe that **issues a real request and asserts the resulting behavior** — not by scanning the
> diff and not by an SLM judge. The verdict is the subprocess exit code / exception, which is
> non-circular (the SLM may *generate* the probe but never *judges* the outcome). This is the
> apply-and-probe forensic that caught run9, promoted into the loop.

**Corollary (load-bearing — see §6.A example):** a probe must **exercise the runtime path**, i.e.
construct a `Client` with a `MockTransport` and **issue a request** (`client.get(...)`). Merely
calling `build_request(...)` and inspecting `extensions` is **insufficient** — it does not traverse
`request → send → _merge_timeout_extensions`, the path that produced run9's failure.

---

## 4. Operational definitions (precise enough to implement)

- **at-risk contract (for a turn T):** a prior-ledger contract whose `files` or `symbols` overlap
  T's `changed_files`. On the **final turn**, *every* prior contract is at-risk regardless of overlap
  (see §6.B, overlap-recall ceiling).
- **outcome states (per contract, per turn):** exactly one of
  - `clean` — probe ran and passed (rc 0);
  - `violated` — probe ran and raised / failed (reproduced exception, with captured `error` +
    `file:line`);
  - `unknown` — probe could not render a verdict (malformed/non-discriminating probe, timeout, or
    no-probe-and-no-runnable-test). **`unknown` is NOT `clean`** (see §8).
- **high-confidence violation (`high_conf`):** a `violated` outcome backed by a **reproduced
  execution failure** (the probe raised, not merely a diff inference). Only `high_conf` violations
  may trigger repair.
- **gated (repair trigger):** repair fires iff `high_conf` AND (the turn is a *modifiable* turn with
  a planned next turn, OR it is the **final turn**). One repair turn maximum per turn (§6.D).
- **taxed (per-run binary):** a run is **taxed** iff ≥1 prior contract is `violated` in its accepted
  final tree. `unknown` does **not** make a run "clean" — see the censoring rule in §8.
- **bounded diff:** the `git diff` already captured by `capture_end_state`, truncated to a byte
  budget; used as repair *context only*, never as a verdict.
- **accepted final tree:** the tree whose state defines the run's outcome — post-repair if repair
  passed re-verify, else the pre-repair tree after rollback (§6.D).

---

## 5. Data schemas

```text
ContractProbe         # stored on each ledger contract (optional)
  source: str               # self-contained python; MockTransport; issues a request; raises on violation
  born_turn: int
  birth_validated: bool      # passed positive + negative control at birth (§6.A)

VerifierResult        # one per (run, turn) the verifier ran on
  turn: int
  checks: [ { feature, status: clean|violated|unknown,
              evidence: { error, file_line, probe_source } | null,
              via: probe|tests } ]
  all_probes_run: bool       # true on the final turn (overlap filter bypassed)

RunVerifierMetrics    # one per run (the auditable cost/accuracy record)
  probes_generated: int
  probes_failed_birth: int           # discarded (positive or negative control failed)
  contracts_clean / violated / unknown: int
  repair_fired: bool
  repair_outcome: repaired|unrepaired|na
  rolled_back: bool
  # costs kept SEPARATE (never summed into one "verify cost"):
  slm_probe_gen_tokens: int          # once per contract, folded into ledger extraction
  probe_exec_wall_ms: int            # subprocess time (≈0 model tokens)
  repair_turn_tokens: int            # the agent repair turn (the expensive part; 0 if not fired)
  verifier_latency_ms: int
```

---

## 6. Architecture — five components

### A. Contract probes (executable check, generated at birth, double-validated)
Extend the ledger contract schema (`research/memory_ledger.py`) with an optional `probe` (see §5).
The SLM **generates** the probe at ledger-extraction time (folded into the existing
`_slm_extract_contracts` call). It **never judges**; Python's exit code does. **Verification costs
~0 model tokens** — only probe *generation* costs SLM tokens, once per contract.

**The probe must EXERCISE the contract** (driving decision corollary). Example for run9's C1, written
to actually reproduce the merge-path bug:
```python
import httpx
seen = {}
def handler(req):
    seen["ext"] = req.extensions
    return httpx.Response(200)
client = httpx.Client(transport=httpx.MockTransport(handler))
r = client.get("http://x", connect_timeout=0.5)        # get -> request -> send -> _merge_timeout_extensions
assert r.status_code == 200
assert seen["ext"]["timeout"]["connect"] == 0.5        # behavior, not just signature
```

**Birth validation = positive control + NEGATIVE control** (closes the "vacuously-passing probe" hole
all three reviews flagged):
1. **Positive:** the fresh probe must **PASS** on the just-edited tree (the turn that implemented the
   contract). A probe that fails here is mis-generated → discard.
2. **Negative (new):** the fresh probe must **FAIL** on the clean **`d764bfc` baseline** (vanilla
   httpx, where the contract does not exist). A probe that **passes on baseline is non-discriminating**
   (it asserts something always true, or swallows the exception) → discard.

A probe stored only if it passes (1) AND fails (2). Discarded probes fall back to the contract's
`tests` field (scoped, no-socket, timeout-guarded). This is what makes "every stored probe is a
proven regression detector" actually true.

### B. ObligationVerifier (instrument-first detector)
`research/verify_repair.py` → `verify_contracts(cwd, prior_contracts, changed_files, budget, final)`:
- **Trigger set:** the *at-risk* contracts (overlap with `changed_files`). **On the final turn,
  bypass the overlap filter and run ALL prior contracts' probes** — cross-cutting/transitive breakage
  (a bug in shared plumbing that doesn't touch a contract's recorded files) is the overlap filter's
  blind spot, and the final consolidation turn is where it is most likely. Cost permits it (probes
  are ~0-token subprocesses).
- Run each contract's `probe` (or fallback `tests`) in a **sandboxed subprocess** (§6.E) against the
  post-turn tree, hard timeout. Emit a `VerifierResult`.
- **Fail-closed by cause:** a malformed/non-discriminating probe, a timeout, or no runnable check →
  `unknown` — never `violated` (no false alarms) and never silently `clean` (§8). Distinguish the
  causes in `RunVerifierMetrics`; a **final-turn `unknown` on an at-risk contract is escalated to a
  warning** and falls back to the contract's `tests` before giving up.
- **Standalone value (instrument-first):** a non-circular detector that can **retro-score existing
  runs**. First deliverable: run it over the 10 finished with_memory runs.

### C. Evidence plumbing (roadmap P1b)
Per turn the verifier needs: a **prior-ledger snapshot** (`snapshot_ledger(cwd)` taken *before*
`record_to_memory` mutates it); the **post-turn tree** (in place); and the **bounded diff** (reuse
`capture_end_state`'s) for repair context only.

### D. Light gated repair + reconciliation (roadmap P1a)
On a `gated` high-confidence violation, fire **one** targeted repair turn:
> "Your last change broke these previously-working contracts: `<feature>` — `<probe error +
> file:line>`. Fix the *implementation* so each passes again. Keep the new form too (additive). Do
> NOT change anything else."

**Post-repair reconciliation (new — defines the "accepted final tree"):**
1. **Re-verify** the previously-violated contracts (and, on the final turn, all probes).
2. If now `clean` → **accept the post-repair tree**: re-run `record_to_memory` so the ledger reflects
   the repaired code, and re-run `capture_end_state` so the captured diff/endstate is the repaired
   one. Record `repair_outcome=repaired`.
3. If still `violated` (failed or over-reaching repair) → **git-rollback the repair turn's changes**
   to the pre-repair tree, so the system never ships a *net-negative* edit. Record
   `repair_outcome=unrepaired`, `rolled_back=true`.

Caps: **1 repair turn** (no loop). A failed-then-rolled-back repair counts as **detected-but-
unrepaired**, NOT a success.

### E. Probe-execution sandbox policy (new)
Probes are **SLM-generated code** — running them is arbitrary code execution, so bound it explicitly
(MockTransport handles network; this handles the rest):
- run in a **subprocess** with a hard wall-timeout (kills hangs);
- **no network** (MockTransport only; no real sockets — a probe that imports a network client is
  rejected at birth);
- **read-only intent on the workspace** — run with `cwd` = the (throwaway, reset-between-runs) target
  clone; treat any filesystem write outside a temp dir or any spawned child process as a
  birth-validation failure (reject the probe);
- restrict imports to `httpx` + stdlib at birth-validation (static check of the probe source).
- **Severity note:** in the current research harness (trusted SLM, target is a disposable httpx clone
  reset every run) the practical risk is low; this policy is the **contract required before any
  non-research use**.

---

## 7. Integration into `run_chain_once` (chain_test_v2.py)
```text
# Signatures (as implemented in research/verify_repair.py):
#   verify_contracts(contracts, cwd, changed_files, final=False, timeout_s=...) -> {checks, violations, all_probes_run}
#   repair_and_reconcile(cwd, repair_fn, verify_fn) -> {outcome: repaired|unrepaired, rolled_back: bool}
#   snapshot_ledger(cwd) -> {version, contracts}   (use ["contracts"])

prior = snapshot_ledger(target)["contracts"]       # contracts MAP, taken BEFORE the turn (§6.C)
... run the turn, record_to_memory ...             # existing
final = (turn_index == last_turn)
if _is_refactor or guard_hits or final:
    vr = verify_contracts(prior, target, changed, final=final)        # (contracts, cwd, changed_files, final)
    if vr["violations"] and (final or high_conf(vr)):                 # gated = reproduced failure AND (final or modifiable)
        # repair_and_reconcile fires ONE repair turn, re-verifies, and ACCEPTS or ROLLS BACK internally
        res = repair_and_reconcile(
            target,
            repair_fn=lambda d: repair_turn(d, vr["violations"]),     # ONE codex turn (§6.D)
            verify_fn=lambda d: not verify_contracts(prior, d, changed, final=final)["violations"])
        if res["outcome"] == "repaired":
            record_to_memory(target); capture_end_state(target)       # refresh ledger/endstate on accept
    record RunVerifierMetrics
```
(`high_conf` per §4; `repair_turn` = the one codex repair invocation, Phase 3.)
New arm **`with_memory_verify` (B)** vs **`with_memory` (A1)**.

---

## 8. Pre-registered outcomes & metrics (decide BEFORE running — avoids optimistic drift)

**Outcome accounting (pre-registered):**
- a run is **taxed** iff ≥1 contract `violated` in the accepted final tree;
- `unknown` is reported as its **own bucket** and **censors a "clean" claim** for the affected
  contract — a run with an unrepaired final-turn `unknown` on an at-risk contract is **not counted as
  clean** (it is `unverified`). It never silently improves the tax rate.

**Three reported layers (decompose the treatment effect — do NOT collapse into one B number):**
1. **Verifier-only detection accuracy** — precision/recall vs the pytest oracle, measured by
   **replaying the 10 finished runs** (free, no new spend). Answers: does it catch real breakage
   without false alarms?
2. **One-turn repair success rate** — of contracts detected `violated`, what fraction the single
   repair turn restores (on the fixture + any live detections).
3. **End-to-end B vs A1** — residual tax + cost, the product metric (directional at low N, see §9).

**Cost accounting (kept separate, never summed into one figure):** `slm_probe_gen_tokens` (once/
contract), `probe_exec_wall_ms` (≈0 model tokens), `repair_turn_tokens` (the expensive part; 0 if
not fired), `verifier_latency_ms`. The §2 "~1.07× lighter" is **A1 only**; B's net position =
A1 ± probe-gen − (repair-turn cost × P(repair fires)).

---

## 9. Experiment design & phased build (cost-gated; columns separated)

Each phase has an **artifact** (what's built), a **local acceptance** check (deterministic, cheap),
and at most one **research claim** (the falsifiable empirical result).

| Phase | Artifact | Local acceptance | Research claim |
|---|---|---|---|
| **0** | Extend `_oracle_groundtruth.py` with the **kept-but-broken** class + run9's real diff as a golden case | execution probe flags it; a diff-substring check does NOT | — (premise demonstration, 0 model cost) |
| **1** | Verifier as **detector** (probe + positive+negative birth control + `tests` fallback); retro-score all 10 runs | runs without crash; metrics emitted | **catches run9; produces no *unsupported* violations; any *additional* execution-backed failure is ADJUDICATED, not auto-scored false** (a verifier that finds a real bug the forensic missed is a win, not a gate failure) |
| **2** | Evidence plumbing (prior snapshot + bounded diff) + SLM probe-gen wired into extraction | probes generated + birth-double-validated on a smoke chain; budgets enforced | — |
| **3** | Light gated repair (1 turn, final-turn) + reconciliation + rollback | on the **run9 fixture**: detect → repair → deterministic pass; failed-repair → rollback restores tree | **one-turn repair restores the run9-class fixture deterministically** *(primary success deliverable)* |
| **4** | A1-vs-B **wiring landed** (gated `VERIFY_REPAIR=1`, hook unit/integration-tested); the **paid live run** is what remains | wiring callable + green tests; live run not yet executed | **directional only (underpowered):** at a ~1/10 base rate, chain_long N=5 cannot distinguish repair from variance. Report B-vs-A1 as directional; the *fixture* (Phase 3) is the load-bearing claim. Pair by tool-version/seed where possible; raise N only if a real effect looks plausible. |

**Why the success criterion is the fixture, not the live A/B:** the residual is ~1/10, so an N=5
A1-vs-B test is dominated by variance (if A1 lands 0/5 again, "B < A1" is unprovable). The honest,
powered claim is the **deterministic fixture** (Phase 3); the live A/B is supporting/directional.

---

## 10. How it uses the deep-research borrowables
- **CoALA** (cognitive architecture for language agents) — adds the *verification* procedure its
  memory taxonomy lacked (recall → reason → **verify** → repair).
- **Reflexion** (verbal self-reflection stored as memory) — the repair turn = reflection on the
  *execution failure* (the probe error), injected for the fix.
- **ACON** (compression/guideline learning from failure analysis) — aggregated repair failures feed a
  failure-analysis → guideline loop to keep tuning the guard wording (later, automatable).
- **Mem0** (per-fact CRUD memory) — per-contract `status` (clean/violated/repaired) is a CRUD update
  on the ledger.
- Confirms the field's lesson (no published work shows recall-memory improves code-editing
  *correctness*; the two such claims were refuted): **correctness comes from execution/feedback, not
  recall** — this is the layer that moves the residual.

---

## 11. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Probe passes vacuously (false negative) | **negative birth control** (must fail on `d764bfc` baseline) + must-exercise corollary |
| Probe mis-generated / flaky (false positive) | positive birth control + `tests` fallback + fail-closed `unknown` (never `violated`) |
| `unknown` silently inflates correctness | `unknown` is its own bucket, censors "clean", escalated on final turn (§8) |
| Cross-cutting breakage missed by overlap filter | run ALL probes on the final turn (§6.B) |
| Probe hangs / opens a socket / writes files | sandbox policy (§6.E): subprocess timeout, no network, no fs-writes/child-procs (reject at birth) |
| Failed/over-reaching repair leaves tree worse | reconcile: re-verify → **git-rollback** on still-violated; record detected-but-unrepaired (§6.D) |
| Underpowered live A/B | anchor success on the deterministic fixture; mark chain_long N=5 directional (§9) |
| In-sample overfit (precision/recall=1.0 on the 10 runs that motivated it) | flag as in-sample; plan out-of-sample validation on new runs / held-out fixtures |
| Over-claiming | effect is **"verify+repair," not "memory"**; cross-arm deltas are confounded (§2 warning) |

---

## 12. Honest positioning
This raises *correctness* (catches the kept-but-broken class the guard structurally cannot) and is
**cheap on the detection side** (execution probes, not LLM-judge calls; repair is the only model
cost, and only when it fires). It does **not** prove the *memory layer* makes the agent more correct
in general. Consistent with the deep-research, the defensible claim is *"verify-and-repair eliminates
the destructive-migration tax and **attempts** a bounded repair of the residual, at comparable-or-
better correctness and low detection cost,"* not *"memory makes the agent correct."*

---

## 13. Provenance — reviews incorporated into v2

v2 folds in three independent reviews of v1:
- **Internal 3-lens panel** (skeptical-engineer / cold-newcomer / research-methodologist), rated v1
  **7.3/10** (problem clarity 5.0; finding-credibility + design-soundness 3.7 — the weak axes).
- **Correctness review** (`SESSION_MEMORY_VERIFY_REPAIR_REVIEW_NEW_MODEL.md`) — execution-enforcement
  depth: probe must exercise; birth negative-control; `unknown` ≠ clean; overlap recall ceiling;
  **post-repair reconciliation**; **probe-execution sandbox**.
- **Methodology/clarity review** (`SESSION_MEMORY_VERIFY_REPAIR_METHODOLOGY_CLARITY_REVIEW.md`) —
  underpowered N; **3-layer reporting**; **Phase-1 gate adjudication** ("run9 + adjudicate extras");
  pre-registered outcomes; operational definitions; **schema block**; phase-table split.

**Highest-confidence fixes (≥2 independent reviews agreed):** probe-must-exercise (all 3),
`unknown` ≠ clean (all 3), underpowered-N (panel + methodology), birth negative-control (panel +
correctness), overlap recall ceiling (panel + correctness).

**One finding NOT actioned:** the methodology review's "mojibake/encoding artifacts" (Clarity C5) was
**verified a false positive** — the file is clean UTF-8 (0 replacement chars; `→`/`×`/`≠` intact).
The reviewer's read pipeline mangled Unicode on input. (If downstream consumers have weak UTF-8
handling, ASCII glyphs `->`/`x`/`!=`/`<=` are a portability option, not a correctness fix.)
