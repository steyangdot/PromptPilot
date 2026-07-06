# Phase 3 — `prpt ci`: the Headless CI Product

**Status:** design (2026-07-06), successor to the research program closed by PR #54.
**Charter:** `docs/CAMPAIGN_CI_HEADLESS.md` (Phases 0–2 complete; every component below is
either a validated asset, a $0 mechanical guarantee, or explicitly fenced out).
**Companion finding:** `docs/FINDING_REWRITE_TOKEN_ECONOMICS.md` (why the prompt path is raw).

## 1. What Phase 3 ships

One command:

```
prpt ci [--repo .] [--evidence <file|->] [--diff <ref>] [--json]
```

A CI job hands it the failing-test output (and optionally the triggering commit); it drives
one cold agent attempt plus at most one bounded repair, gates the result with impacted-test
selection, and emits a JSON verdict + exit code the pipeline can act on.

```
evidence in ──► prompt = instruction + VERBATIM evidence + verification pin
                      │
                cold agent run  (no session, no memory, no rewrite)
                      │
                integrity check (mechanical git: tests/conftest/pytest-config untouched)
                      │
                GATE: line-scoped impacted-test selection ──► green ──► report, exit 0
                      │ red
                ONE bounded repair (retry prompt = evidence + agent diff stat
                      │              + SLM-distilled failure output)
                GATE again ──► green → report, exit 0 / red → report, exit 1
```

## 2. Components and their evidence

### 2.1 Prompt construction — raw + pin (no SLM)

- Instruction + **verbatim** CI evidence. The traceback is the prompt; nothing replaces it
  (KG-1 v2 KILL, ε₁ = −0.038; see the companion finding).
- **Verification pin** appended: *"Verify at least with: `<selection argv>`. Do not run the
  full suite — it exceeds your command timeout and the pipeline gate runs it separately."*
  Ships as an engineering default (bounded behavior + ~2 min dead wall saved per full-suite
  escalation), carries **no token-savings claim** (step-0: ~1% on raw prompts). Phrased as a
  floor ("at least"), not a ceiling, to avoid a correlated blind spot with the gate.
- **Culprit-commit diff** included in evidence when the trigger is a push (legitimately
  available in real CI; the strongest diagnosis input in the system, needs no model).
- Teach-to-test prohibition stays in the instruction; enforcement is mechanical (§2.4).

### 2.2 Impacted-test selection — the validated asset

- **Primary: line-scoped coverage** — tests whose `--cov-context=test` contexts execute the
  changed lines. Out-of-sample: **16/16 catch, 16/16 strict at 5.2 files mean**
  (`kg1_data/kg2_result_all.json`, pre-registered re-score, frozen matcher).
- **Fallback: pruned static matching** (name heuristic + symbol grep, test files only) used
  ONLY where the coverage map is blind: new files, uncovered lines, map staleness. Never
  unioned by default — the static tier's symbol breadth is what dragged the OOS wall to
  0.48× (`_models.py` → 23-file selections).
- **Coverage map lifecycle:** built once per base commit (bare `--cov`, ~12% overhead over
  a plain full run), cached outside the repo, refreshed alongside the scheduled full-suite
  run — which remains the pipeline backstop for anything selection misses.

### 2.3 Gate — the verifier the agent doesn't have to be

- Post-agent, `prpt` runs the selection via the safe allow-listed runner (`prpt/verify.py`).
  Agent verification is never trusted (recall≠action); the gate is the correctness owner.
- Output: JSON verdict (selection, per-file results, wall, token report) + exit code.

### 2.4 Integrity guard — mechanical teach-to-test enforcement

- `git status --porcelain` after the agent (clean-tree start guaranteed by the runner):
  any touch of `tests/`, `conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg` →
  the attempt is REJECTED regardless of pytest outcome (protocol-v2 mechanism, 20/20
  compliance observed but never assumed).

### 2.5 Bounded repair — the SLM's load-bearing job

- On a red gate: **one** retry. Retry prompt = original evidence + the agent's diff stat +
  the SLM's distillation of the NEW failure output (assertion diffs and failing node ids,
  never test source). This is the one pre-agent-adjacent place the SLM survived every gate:
  it speaks *after* evidence, about a concrete red run, into a bounded loop.
- Fail-open: SLM unavailable → retry prompt carries the raw failure tail.

### 2.6 Reporting & repo-memory

- Honest token report per the accounting doctrine: uncached primary, cached/gross for
  contrast, SLM as its own line, never folded (codex via `--json` usage; claude-code arm
  via native OTEL when enabled).
- SLM writes the human-facing residue: PR comment, commit message, optional `AGENTS.md`
  suggestion — durable knowledge goes into the repo, which is the only cross-job memory
  (charter §4: no ledger, no session, no carrier in CI).

### 2.7 Runtime hardening (inherited from protocol v2)

Orphan-tree kill on agent timeout (`taskkill /T`); generous agent timeout (the
CODEX_TIMEOUT_SEC=1200 lesson); base-commit + clean-tree asserts before and after; `--bare`
API-key lane as the conservative default auth for sustained automation; append-only run
artifacts.

## 3. Fences — what Phase 3 does NOT contain

- **No prompt rewrite** (KILLed, ε₁ = −0.038) and **no triage classifier** (4/10 OOS).
- **No cross-job memory of any kind** — the repo is the ledger.
- **No KG-3 distillation in the pre-agent path** — parked; fundable only via the
  pre-registered repeated-measures design (N≥2–3/task) after a step-0b note-quality audit.
- **No interactive-mode changes** — the shipped recency session stays frozen.

## 4. Milestones & acceptance (each $0-testable on the fixture corpus)

- **M1 — skeleton:** evidence→prompt+pin→agent→gate→JSON verdict. *Accept:* on the 16-task
  corpus, gate catches 16/16 seeded defects; line-scoped selection mean ≤ ~0.25× full-suite
  wall (static-free config); zero full-suite invocations by the harness itself.
- **M2 — repair + integrity:** bounded retry wired; test-touch rejection. *Accept:* a
  seeded red first attempt (forced via a deliberately under-specified prompt) recovers
  within one repair round on the majority of tasks; injected teach-to-test edits are
  rejected 100% mechanically.
- **M3 — lifecycle + recipe:** coverage-map build/cache/refresh; a GitHub Actions example
  workflow (`prpt ci` on failing test job → PR comment).
- **M4 — reporting:** token/wall report validated against the harness's measured numbers on
  a replayed corpus task.

**Then Phase 4:** the reasoning_effort sweep on this corpus and harness — the flagship cost
benchmark (~23× known dial), now on audited ground.

## 5. Open questions (tracked, not blocking)

- Language generality: line-scoped contexts are pytest/coverage.py-specific; the design is
  Python-first. Other ecosystems need their own coverage-context source.
- Selection under heavy churn: many-file diffs may select near-full suites (honest — the
  gate degrades to the backstop, never below it).
- Parallelism: xdist would cut gate wall but risks port conflicts in server-heavy suites
  (observed in httpx); deferred until a corpus shows it safe.
