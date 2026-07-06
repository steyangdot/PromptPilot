# CI Fixture Corpus — Spec

**Campaign:** docs/CAMPAIGN_CI_HEADLESS.md (Phase 1). **Code:** `research/ci_fixtures.py`.
**Purpose:** the benchmark substrate for the pivot's kill-gates (KG-1: rewrite value on pointed
tasks; later the reasoning_effort sweep). Replaces multi-turn chain fixtures as the primary
benchmark for headless work.

## 1. Task model

A task is a **single-shot, pointed, CI-shaped unit**: one seeded semantic bug in the target repo +
the locking tests that catch it + the **captured real pytest failure output** as the task's trigger.

```
task := {
  id, cls,                  # cls ∈ {failing-test} (v1; lint-error, type-error, dep-bump deferred)
  file, edits[],            # ONE semantic edit (exact-match search/replace; [] = pre-seeded in base)
  targets[], k?, scope_reason?,   # locking pytest targets (network-free) + optional -k scoping
  pytest_argv,              # the EXACT scoring invocation, recorded in the manifest — downstream
                            # scorers MUST replay this verbatim, never re-derive "run the file"
  evidence.txt              # captured at verify time: the tail of the REAL pytest failure output
}
```

**Consumers iterate `manifest["admitted"]` only.** The verifier writes every result (admitted +
`rejected_for_audit`) so inert seeds and bad baselines stay visible, but the corpus IS the admitted
list — a rejected task never enters an experiment.

**Artifacts are TRACKED** at `research/ci_fixtures_data/` (deliberately outside the gitignored
`research/data/`): the manifest + evidence are the pinned KG-1 inputs and must travel with the
branch byte-stable — pytest output formatting varies by environment, so "regenerate elsewhere" is
not comparability. Re-running the verifier is how you *propose* a corpus change; committing the
regenerated artifacts is how you *adopt* it.

**The evidence file is the point.** KG-1's realism requires the agent to receive exactly what CI
gives — a traceback and a failing-test summary — not a hand-written task description. Both KG-1
arms (raw vs rewritten) start from the same evidence; the rewrite arm additionally gets the SLM
grounding pass over it.

## 2. Verification gate (what makes a task admissible)

A task enters the manifest only if `research/ci_fixtures.py` proves, on each run:

1. **Baseline green:** locking targets PASS on the clean base tree (exception: the pre-seeded
   base task, whose red-on-base is its seed — this check simultaneously proves the other tasks'
   targets aren't contaminated by the base seed).
2. **Seed reproduces:** with the single edit applied, the locking targets FAIL.
3. Evidence captured, tree reset. Inert seeds are excluded loudly, never hand-waved.

Base state: httpx fixture branch `seeded-auth-bug` @ `d764bfc` (carries the digest-auth seed).
Verifier cost: $0 (local pytest only). Re-run the verifier whenever the base commit or the
python env changes — admissibility is per-environment, not assumed.

**Incremental verification (since v2):** by default the verifier runs only tasks not yet in the
manifest and MERGES the results — previously-admitted entries and their evidence files pass
through byte-untouched, because they are the pinned inputs of already-run KG gates. `--all`
rebuilds the whole corpus (regenerating all evidence = a new corpus generation, commit it
deliberately); the verifier refuses to run incrementally if the base commit has moved.

## 3. v1 corpus (6 tasks, all `failing-test`)

| id | seeded defect | locking targets |
|---|---|---|
| auth-digest-a1 | DigestAuth A1 hash-term order (pre-seeded in base) | tests/test_auth.py |
| content-json-type | encode_json wrong Content-Type | tests/test_content.py, tests/models/test_requests.py |
| url-port-norm | default-port normalization inverted | tests/models/test_url.py |
| utils-bool-str | bool query values 'True' not 'true' | tests/models/test_queryparams.py, tests/client/test_queryparams.py |
| decoder-trailing-cr | LineDecoder drops carried CR at chunk boundary | tests/test_decoders.py `-k line_decoder` |
| config-timeout-tuple | Timeout tuple: write takes read's slot | tests/test_config.py |

Diversity axes covered: hash/crypto logic, header emission, parser normalization, value coercion,
incremental stream state, constructor unpacking — six distinct modules, no shared files.

## 3.1 v2 corpus (2026-07-05, +10 tasks — the KG-1.5b out-of-sample set)

| id | seeded defect | locking targets |
|---|---|---|
| models-headers-getlist-split | Headers.get_list(split_commas) stops stripping whitespace | tests/models/test_headers.py |
| models-response-links | Response.links keyed by URL instead of rel | tests/models/test_responses.py `-k "not autodetect and not cp_1252"` |
| models-cookies-domain-filter | Cookies.get domain filter inverted | tests/models/test_cookies.py |
| urls-username-unquote | URL.username stays percent-encoded | tests/models/test_url.py |
| urls-qp-merge-order | QueryParams.merge precedence swapped | tests/models/test_queryparams.py, tests/client/test_queryparams.py |
| client-redirect-authstrip | auth-strip origin condition inverted | tests/client/test_redirects.py |
| client-redirect-head-method | 302 method-rewrite guard inverted | tests/client/test_redirects.py |
| utils-noproxy-wildcard | NO_PROXY bare domain gets subdomain-only wildcard | tests/test_utils.py, tests/client/test_proxies.py |
| multipart-content-length | multipart Content-Length off by two | tests/test_multipart.py `-k "not text_mode_file"` |
| decoders-gzip-wbits | GZipDecoder loses the gzip-header window-bits flag | tests/test_decoders.py `-k gzip` |

**Selection discipline (out-of-sample honesty for KG-1.5b):** the v2 tasks were chosen for
**module diversity** — mostly files the v1 corpus doesn't touch (`_models.py`, `_urls.py`,
`_client.py`, `_multipart.py`), plus two same-name-module tasks (`_decoders.py`, `_multipart.py`)
so pointed-shaped evidence stays represented — and **never** for how the KG-1.5 triage signals
would score them. The triage classifier was frozen in commit `1736266` before any v2 task
existed, and `research/kg1_5b_predict.py` recorded its predictions
(`research/kg1_data/kg1_5b_predictions.json`) **before any KG-1 v2 run produced outcomes**:
7 predicted WIN (all via the module-mismatch signal — structural to httpx's aggregate modules,
whose tests live under differently-named files) and 3 predicted LOSE. Scoring the frozen
predictions against the eventual KG-1 v2 labels is the classifier's real out-of-sample test.
Caveat surfaced by that split: min-fanout fired on zero v2 tasks, so v2 chiefly tests the
module-mismatch signal, not the combined OR rule — noted here so the eventual accuracy number
isn't over-read.

Two scoping notes (rule §5): `test_responses.py` carries the same chardet-version drift as
`test_decoders.py` (2 charset-autodetect reds on the clean base), and `test_multipart.py` has one
Windows-platform red (`TemporaryFile(mode="w")` is not `io.TextIOBase` here). Both tasks scope
those out with a negative `-k`, recorded on the task with the reason.

## 4. Scoring (for KG-1 and later sweeps)

- **Green:** the task's locking targets pass after the agent's patch (same pytest invocation as the
  verifier; unrelated pre-existing reds in the base are out of scope by target-scoping).
- **Cost:** codex tokens from `--json` usage (uncached as primary, gross for contrast) + any SLM
  tokens (separate line, per the §4.1 accounting doctrine — SLM tokens never fold into the codex
  headline).
- **Primary KG-1 metric: tokens-per-green at correctness parity**, pre-registered bands in the
  charter. Per-task wall time reported, not gated.

## 5. Extension rules

- One semantic edit per task; deterministic; network-free locking tests; verifier-admitted only.
- A task may scope its targets with a pytest `-k` filter when the clean base has unrelated
  environment reds in the same file (e.g. `decoder-trailing-cr` scopes to `-k line_decoder`
  because 2 charset-autodetect tests are red on this env's chardet version) — the scoping must be
  recorded on the task with the reason.

- New classes (lint-error via ruff, type-error via mypy, dep-bump) join only with the same
  verify-reproduce-capture discipline; dep-bump requires hermetic pinning and is explicitly
  deferred until someone designs that.
- Tasks must stay **file-disjoint** from each other where possible, so per-task baselines stay
  meaningful under the pre-seeded base.

## 6. Verification results

**v1 (2026-07-04): 6/6 ADMITTED** on base `d764bfc`, all baselines green (or red-by-seed for the
pre-seeded task): auth-digest-a1 (3F/5P), content-json-type (7F/60P), url-port-norm (19F/71P),
utils-bool-str (1F/16P), decoder-trailing-cr (1F/2P scoped), config-timeout-tuple (1F/27P).

**v2 (2026-07-05): 10/10 ADMITTED** (8 first pass; 2 re-admitted after env-drift `-k` scoping,
see §3.1): headers-getlist (1F/26P), response-links (2F/102P scoped), cookies-domain (1F/6P),
username-unquote (5F/85P), qp-merge (2F/15P), redirect-authstrip (4F/27P), redirect-head-method
(1F/30P), noproxy-wildcard (6F/103P), multipart-content-length (11F/26P scoped), gzip-wbits
(1F/3P scoped). **Corpus total: 16/16.** Manifest + captured evidence:
`research/ci_fixtures_data/` (tracked); v1 evidence byte-untouched by the v2 run.
