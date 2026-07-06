# Finding: Why a "Simpler" Rewritten Prompt Does Not Reliably Save Tokens

**Status:** Evidence-backed finding from KG-1 v2 (2026-07-06). All numbers replayable from
committed artifacts: `research/kg1_data/kg1_run_c2.json` (runs), `kg1_verdict_c2.json`
(verdict), transcripts in `research/data/kg1_rewrite_value/` (local). Protocol and RCA
context: `docs/CAMPAIGN_CI_HEADLESS.md` §6 OUTCOMES v2.

## TL;DR

A shorter, cleaner, SLM-rewritten prompt made the agent **more expensive** as often as
cheaper — deconfounded ε₁ = **−0.038** (rewrite arm used 3.8% MORE codex-uncached tokens per
green fix than the raw traceback, 10/10 green parity both arms). The mechanism: **token cost
lives in the agent's tool loop, not in the prompt**, and the rewrite deletes the one thing
that bounds the tool loop — the *verification anchor*. A prompt's job is not to be simple;
it is to bound behavior. A raw traceback, ugly as it is, is a machine-precise behavioral
bound. Prose is not.

## 1. The experiment

10 verifier-admitted CI fixture tasks (seeded single-line semantic bugs in httpx, committed
to the tree — no git-diff leak), 2 arms each, single-shot cold `codex exec` (gpt-5.5):

- **Arm A (raw):** instruction + verbatim captured pytest failure output.
- **Arm B (rewrite):** the same evidence passed through the `slm-openai-v2` grounding
  rewrite (gpt-5.4-nano, measured usage ~11.5k tok/task, priced ~0.02× of gpt-5.5).

The rewrite produced *shorter* prompts in 8/10 cases (e.g. 4,168 → 1,611 chars) that read
better to a human: a task statement, expected behavior, constraints. Every run green, zero
integrity violations. And yet:

| task | raw uncached | rewrite uncached | ratio |
|---|---|---|---|
| decoders-gzip-wbits | 38,044 | 22,060 | **1.72× (rewrite wins)** |
| models-headers-getlist-split | 60,174 | 41,861 | 1.44× |
| urls-username-unquote | 54,767 | 38,964 | 1.41× |
| utils-noproxy-wildcard | 59,786 | 50,385 | 1.19× |
| urls-qp-merge-order | 56,451 | 49,090 | 1.15× |
| multipart-content-length | 71,055 | 71,742 | 0.99× |
| client-redirect-head-method | 40,055 | 46,839 | 0.86× |
| client-redirect-authstrip | 63,264 | 87,580 | 0.72× |
| models-response-links | 34,574 | 50,339 | 0.69× |
| models-cookies-domain-filter | 25,110 | 63,416 | **0.40× (rewrite costs 2.5×)** |
| **pooled** | **503,280** | **522,276** | **ε₁ = −0.038 → KILL** |

Prompt size is irrelevant to run cost: prompt deltas are ±2k characters (~500 tokens) while
run outcomes swing ±38,000 tokens. The prompt is <2% of the cost; the exploration it
*induces* is the other 98%.

## 2. Evidence I — CI evidence carries two anchors; the rewrite deletes the one that matters

A raw pytest failure contains:

- a **fix anchor** — where the bug probably is (traceback frames, assert diff), and
- a **verification anchor** — the exact failing invocation and its counts
  (`tests/models/test_cookies.py`, "1 failed, 6 passed").

The fix anchor turned out to be nearly worthless — agents localize well on their own (see
§3, the cookies case). The verification anchor is load-bearing: it is a **bounded
definition of done** ("rerun this file, see green, stop"). The rewrite replaces it with
behavioral requirements ("make `Cookies.get` respect domain=; don't break other behaviors")
that have **no bounded check attached**.

Exhibit (models-response-links): the raw evidence contains
`tests\models\test_responses.py:885`, the literal expected-vs-actual dict diff, and the
runnable test identity. The rewrite — an accurate, well-written spec — contains none of
them ("Search for the code behind `Response.links`..."). Ratio: 0.69×.

## 3. Evidence II — the measured mechanism: verification-scope escalation (24 vs 2)

Facing an unbounded correctness claim, the agent verifies against the world. Counting
broad full-suite pytest invocations (bare `pytest` / `pytest tests` — 1,418 tests, which
**cannot finish** inside codex's ~123s per-command timeout) across all 20 transcripts:

| | raw arms | rewrite arms |
|---|---|---|
| full-suite invocations | **2** | **24** |

Every deep-loss rewrite arm ran the full suite 2–4×; only one raw arm ever did. Worked
example — `models-cookies-domain-filter` (0.40×, the deepest loss): the rewrite correctly
named the fix file AND the fix methods, yet its agent ran **40 commands including 4 bare
full-suite runs** (raw agent: 22 commands, every pytest invocation file-scoped). Same green
fix; 358s vs 50s wall; 63.4k vs 25.1k uncached. Supporting telemetry: rewrite arms averaged
+27% tool calls (193 vs 152) and ~2× wall on every loss.

Cost decomposition (step-0 analysis): codex truncates tool output, so the 24 broad runs'
*direct* output is only ~40k tokens — the bulk of the loss is the **behavioral cascade**
around them (extra turns, re-reads, retries after the timeout kill). The waste is induced
behavior, not prompt bytes.

Why v1 never saw this: v1's uncommitted seeds leaked the bug diff into the rewrite's
context, which made verification *confident* as a side effect (v1 rewrite transcripts:
zero full-suite escalations). Remove the leak and the escalation appears immediately.

## 4. Evidence III — when the rewrite DOES save tokens, and why that can't be relied on

The five wins share one property: the SLM added a **correct diagnosis the evidence didn't
state** — "window bits lose the gzip-header flag" (agent fixed it in 3 tool calls / 22s /
1.72×), "unquote missing from username derivation," the `test_headers.py` → `_models.py`
bridge. Wins occur where the evidence leaves a genuine gap AND the nano model's
pattern-match happens to be right.

Neither condition is knowable pre-run: diagnosis correctness depends on (a) the bug being a
pretraining archetype readable from the failure signature and (b) the relevant source
having landed in the SLM's static context — a lottery. The classifier built to predict
exactly this scored **4/10 out-of-sample, below every trivial baseline**
(`kg1_data/kg1_5b_score.json`). And the payoff is asymmetric: right diagnosis earns
1.15–1.72×; wrong-or-unneeded conversion costs 0.40–0.99×. An uninspectable coin with
losses larger than wins nets negative — plus the SLM's own fee.

(Caveat, stated because it bit us twice: at N=1 per task, per-task ratios are
variance-contaminated — 4 of 5 wins sit on the costliest raw runs, which is also what
regression to the mean produces. The pooled KILL and the 24-vs-2 escalation count are
robust; individual ratios are not.)

## 5. The doctrine, corrected

The campaign's own thesis — **"evidence directs the agent; content does not"** — applies to
our rewrite too. A rewrite converts evidence into content. "Simpler prompt" optimizes the
2% (prompt bytes, human readability) while unbounding the 98% (induced exploration and
verification). The corrected rules:

1. **Never replace evidence.** The traceback is the highest-fidelity, lowest-token
   behavioral bound available. Additions go *below* it, never instead of it.
2. **Pin the done-check.** State the exact verification command (in `prpt ci`, the
   impacted-test selection) so "done" is bounded even when the evidence is noisy.
   (Zero-cost; its token effect on raw prompts is ~1% — it ships for wall-time and
   determinism, not as a savings claim.)
3. **Enforce structurally.** The pipeline gate re-runs the selection regardless of what the
   agent verified — prompt compliance is never load-bearing (the recall≠action lesson).
4. If an SLM speaks at all pre-run, it may only **append a verified, hedged diagnosis** on
   gappy evidence — the parked KG-3 hypothesis, fundable only with a repeated-measures
   design (N≥2–3/task).

## 6. Provenance

- Run/verdict artifacts: `research/kg1_data/kg1_run_c2.json`, `kg1_verdict_c2.json`
  (commit ef5b308); classifier score `kg1_5b_score.json`.
- Pre-registrations: metric amendment 2759f80 (before any v2 outcome); predictions freeze
  07ee460; protocol hardening 12045dd.
- v1 leak RCA and corrections: `docs/CAMPAIGN_CI_HEADLESS.md` OUTCOMES corrections block
  (commit a7f5a95).
