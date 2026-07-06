"""CI-shaped fixture corpus — single-shot pointed tasks for the CI/headless pivot.

Charter: docs/CAMPAIGN_CI_HEADLESS.md (Phase 1). Each task is a SEEDED single-line semantic bug in
the target repo (httpx @ fixture base) plus the LOCKING TESTS that catch it. The verifier applies
each seed to a clean tree, runs the locking tests, captures the REAL pytest failure output — that
captured evidence IS the task's "CI trigger" input for KG-1 (the agent gets exactly what CI gives:
a traceback, not a hand-written prompt) — asserts the failure reproduces, and resets.

Corpus rules (extension discipline):
- One semantic edit per task (single line where possible), deterministic, NETWORK-FREE locking
  tests only (no live servers beyond the repo's own local test fixtures).
- A task enters the manifest ONLY if the verifier proves: locking targets GREEN on the clean base
  and RED with the seed applied. Inert seeds are reported and excluded, never hand-waved in.
- Base state = the fixture branch head (seeded-auth-bug, d764bfc), which ALREADY contains the
  digest-auth seed — recorded as base_preseeded on that task; all other tasks' targets are
  disjoint files, and their per-task baseline check proves non-contamination.

Run:  python research/ci_fixtures.py            (verify all, write manifest + evidence)
      python research/ci_fixtures.py --task ID  (verify one)
Cost: $0 — local pytest only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HTTPX = os.environ.get("PROMPTPILOT_TEST_REPO", "C:/projects/httpx")
PY = sys.executable
# TRACKED path (review P1): research/data/ is gitignored, but the manifest + evidence are the
# PINNED KG-1 inputs — they must travel with the branch byte-stable (pytest output formatting
# varies by env, so "regenerate elsewhere" is not comparability). Commit deliberately on change.
OUT = Path(__file__).parent / "ci_fixtures_data"
EVIDENCE_TAIL_CHARS = 4000     # keep the DECIDING tail of pytest output (failures + summary)
PYTEST_TIMEOUT = 240

# Each task: one seeded semantic bug + the locking tests. `edits` are exact-match
# search/replace pairs applied to `file` (empty edits = pre-seeded in the base commit).
TASKS = [
    dict(
        id="auth-digest-a1",
        cls="failing-test",
        file="httpx/_auth.py",
        edits=[],  # PRE-SEEDED in base d764bfc (A1 term order username:password:realm)
        base_preseeded=True,
        targets=["tests/test_auth.py"],
        note="DigestAuth A1 hash-term order swapped -> RFC digest response wrong.",
    ),
    dict(
        id="content-json-type",
        cls="failing-test",
        file="httpx/_content.py",
        edits=[('    content_type = "application/json"',
                '    content_type = "text/json"')],
        targets=["tests/test_content.py", "tests/models/test_requests.py"],
        note="encode_json emits wrong Content-Type.",
    ),
    dict(
        id="url-port-norm",
        cls="failing-test",
        file="httpx/_urlparse.py",
        edits=[("    if port_as_int == default_port:",
                "    if port_as_int != default_port:")],
        targets=["tests/models/test_url.py"],
        note="Default-port normalization inverted (strips non-default ports, keeps default).",
    ),
    dict(
        id="utils-bool-str",
        cls="failing-test",
        file="httpx/_utils.py",
        edits=[('    if value is True:\n        return "true"',
                '    if value is True:\n        return "True"')],
        targets=["tests/models/test_queryparams.py", "tests/client/test_queryparams.py"],
        note="Boolean query/data values coerced Python-style ('True') instead of JSON-style.",
    ),
    dict(
        id="decoder-trailing-cr",
        cls="failing-test",
        file="httpx/_decoders.py",
        edits=[('        if text.endswith("\\r"):\n            self.trailing_cr = True',
                '        if text.endswith("\\r"):\n            self.trailing_cr = False')],
        targets=["tests/test_decoders.py"],
        k="line_decoder",
        scope_reason="2 unrelated charset-autodetect tests are red on the clean base in this env "
                     "(chardet-version drift) — out of task scope",
        note="LineDecoder drops the carried trailing CR across chunk boundaries.",
    ),
    dict(
        id="config-timeout-tuple",
        cls="failing-test",
        file="httpx/_config.py",
        edits=[("            self.write = None if len(timeout) < 3 else timeout[2]",
                "            self.write = None if len(timeout) < 3 else timeout[1]")],
        targets=["tests/test_config.py"],
        note="Timeout 3/4-tuple: write takes the read slot's value.",
    ),
    # ---- corpus v2 (2026-07-05): +10 candidates. Selection criterion = MODULE DIVERSITY
    # (mostly files the v1 corpus doesn't touch, plus two same-name-module tasks so pointed-
    # shaped evidence stays represented) -- NEVER how the KG-1.5 triage signals would score
    # them. Out-of-sample discipline: the classifier was frozen at commit 1736266 BEFORE any
    # v2 task existed; kg1_5b_predict.py records its predictions before any KG-1 v2 run.
    dict(
        id="models-headers-getlist-split",
        cls="failing-test",
        corpus=2,
        file="httpx/_models.py",
        edits=[('            split_values.extend([item.strip() for item in value.split(",")])',
                '            split_values.extend([item for item in value.split(",")])')],
        targets=["tests/models/test_headers.py"],
        note="Headers.get_list(split_commas=True) stops stripping whitespace around commas.",
    ),
    dict(
        id="models-response-links",
        cls="failing-test",
        corpus=2,
        file="httpx/_models.py",
        edits=[('            (link.get("rel") or link.get("url")): link',
                '            (link.get("url") or link.get("rel")): link')],
        targets=["tests/models/test_responses.py"],
        k="not autodetect and not cp_1252",
        scope_reason="2 charset-autodetect tests red on the clean base in this env (chardet-"
                     "version drift detects 'iso8859-15'): test_response_decode_text_using_"
                     "autodetect, test_response_no_charset_with_cp_1252_content -- out of scope",
        note="Response.links keyed by URL instead of rel (precedence swapped).",
    ),
    dict(
        id="models-cookies-domain-filter",
        cls="failing-test",
        corpus=2,
        file="httpx/_models.py",
        edits=[("                if domain is None or cookie.domain == domain:",
                "                if domain is None or cookie.domain != domain:")],
        targets=["tests/models/test_cookies.py"],
        note="Cookies.get domain filter inverted -> lookups with domain= match the wrong cookies.",
    ),
    dict(
        id="urls-username-unquote",
        cls="failing-test",
        corpus=2,
        file="httpx/_urls.py",
        edits=[('        return unquote(userinfo.partition(":")[0])',
                '        return userinfo.partition(":")[0]')],
        targets=["tests/models/test_url.py"],
        note="URL.username returns the still-percent-encoded form (unquote dropped).",
    ),
    dict(
        id="urls-qp-merge-order",
        cls="failing-test",
        corpus=2,
        file="httpx/_urls.py",
        edits=[("        q._dict = {**self._dict, **q._dict}",
                "        q._dict = {**q._dict, **self._dict}")],
        targets=["tests/models/test_queryparams.py", "tests/client/test_queryparams.py"],
        note="QueryParams.merge precedence swapped -> existing keys win over merged params.",
    ),
    dict(
        id="client-redirect-authstrip",
        cls="failing-test",
        corpus=2,
        file="httpx/_client.py",
        edits=[("        if not _same_origin(url, request.url):",
                "        if _same_origin(url, request.url):")],
        targets=["tests/client/test_redirects.py"],
        note="Auth-strip condition inverted: cross-origin redirects keep Authorization, "
             "same-origin redirects lose it.",
    ),
    dict(
        id="client-redirect-head-method",
        cls="failing-test",
        corpus=2,
        file="httpx/_client.py",
        edits=[('        if response.status_code == codes.FOUND and method != "HEAD":',
                '        if response.status_code == codes.FOUND and method == "HEAD":')],
        targets=["tests/client/test_redirects.py"],
        note="302 method-rewrite guard inverted: HEAD converts to GET (POST no longer does).",
    ),
    dict(
        id="utils-noproxy-wildcard",
        cls="failing-test",
        corpus=2,
        file="httpx/_utils.py",
        edits=[('                mounts[f"all://*{hostname}"] = None',
                '                mounts[f"all://*.{hostname}"] = None')],
        targets=["tests/test_utils.py", "tests/client/test_proxies.py"],
        note="NO_PROXY bare domain becomes subdomain-only wildcard (bare host no longer bypassed).",
    ),
    dict(
        id="multipart-content-length",
        cls="failing-test",
        corpus=2,
        file="httpx/_multipart.py",
        edits=[('        length += 2 + boundary_length + 4  # b"--{boundary}--\\r\\n"',
                '        length += 2 + boundary_length + 2  # b"--{boundary}--\\r\\n"')],
        targets=["tests/test_multipart.py"],
        k="not text_mode_file",
        scope_reason="test_multipart_encode_files_raises_exception_with_text_mode_file is red on "
                     "the clean base on Windows (TemporaryFile(mode='w') isn't io.TextIOBase "
                     "here, so the expected TypeError never raises) -- platform drift, out of scope",
        note="Multipart Content-Length off by two (closing boundary's CRLF not counted).",
    ),
    dict(
        id="decoders-gzip-wbits",
        cls="failing-test",
        corpus=2,
        file="httpx/_decoders.py",
        edits=[("        self.decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)",
                "        self.decompressor = zlib.decompressobj(zlib.MAX_WBITS)")],
        targets=["tests/test_decoders.py"],
        k="gzip",
        scope_reason="same clean-base env drift as decoder-trailing-cr (charset-autodetect reds "
                     "from chardet-version drift) -- scoped to the seeded gzip behavior",
        note="GZipDecoder window bits lose the gzip-header flag (raw zlib) -> gzip decode fails.",
    ),
]


def sh(cmd, cwd=HTTPX, timeout=PYTEST_TIMEOUT):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def reset_repo():
    sh(["git", "checkout", "--", "."], timeout=60)
    sh(["git", "clean", "-fdx", "--quiet"], timeout=60)


def apply_edits(task) -> str | None:
    """Apply the task's exact-match edits. Returns an error string or None."""
    if not task["edits"]:
        return None
    path = Path(HTTPX) / task["file"]
    src = path.read_text(encoding="utf-8")
    for old, new in task["edits"]:
        if src.count(old) != 1:
            return "edit anchor not unique ({0} occurrences): {1!r}".format(src.count(old), old[:60])
        src = src.replace(old, new)
    path.write_text(src, encoding="utf-8")
    return None


def pytest_argv(task) -> list:
    """The EXACT scoring invocation for this task — recorded in the manifest so any downstream
    scorer replays it verbatim (review P1: a scorer inferring 'the file' would hit the unrelated
    env reds that `-k` scoping exists to exclude)."""
    argv = ["-m", "pytest", *task["targets"], "-q", "--no-header"]
    if task.get("k"):
        argv += ["-k", task["k"]]
    return argv


def run_targets(task):
    rc, out = sh([PY, *pytest_argv(task)])
    return rc, out


def verify_task(task) -> dict:
    t0 = time.time()
    res = dict(id=task["id"], cls=task["cls"], file=task["file"], targets=task["targets"],
               corpus=task.get("corpus", 1),
               k=task.get("k"), scope_reason=task.get("scope_reason"),
               pytest_argv=["<python>"] + pytest_argv(task),
               note=task["note"], base_preseeded=task.get("base_preseeded", False))
    reset_repo()
    # Baseline: locking targets must be GREEN on the clean base — except the pre-seeded task,
    # whose red-on-base IS its seed. (Proves other tasks aren't contaminated by the base seed.)
    rc_base, out_base = run_targets(task)
    if task.get("base_preseeded"):
        res["baseline"] = "red-on-base (pre-seeded)" if rc_base != 0 else "UNEXPECTED-GREEN"
        if rc_base == 0:
            res["verdict"] = "INERT (pre-seeded bug not caught by targets)"
            reset_repo()
            return res
        evidence = out_base
    else:
        res["baseline"] = "green" if rc_base == 0 else "RED-ON-CLEAN-BASE"
        if rc_base != 0:
            res["verdict"] = "BAD-BASELINE (targets not green on clean tree)"
            res["baseline_tail"] = out_base[-1500:]
            reset_repo()
            return res
        err = apply_edits(task)
        if err:
            res["verdict"] = "EDIT-FAILED: " + err
            reset_repo()
            return res
        rc_seed, evidence = run_targets(task)
        if rc_seed == 0:
            res["verdict"] = "INERT (seed does not fail the targets)"
            reset_repo()
            return res
    # reproduced: capture evidence (the CI-trigger input for KG-1)
    tdir = OUT / task["id"]
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "evidence.txt").write_text(evidence[-EVIDENCE_TAIL_CHARS:], encoding="utf-8")
    failed = [ln for ln in evidence.splitlines() if " failed" in ln and " passed" in ln]
    res["pytest_summary"] = failed[-1].strip() if failed else "(no summary line)"
    res["verdict"] = "REPRODUCES"
    res["evidence_file"] = str(tdir / "evidence.txt")
    res["wall_s"] = round(time.time() - t0, 1)
    reset_repo()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", help="verify a single task id")
    ap.add_argument("--all", action="store_true",
                    help="re-verify EVERY task (regenerates ALL evidence files, breaking the "
                         "byte-stability of prior pinned KG inputs -- fresh corpus rebuilds only)")
    ap.add_argument("--force", action="store_true",
                    help="allow --task to re-verify an ALREADY-ADMITTED task, deliberately "
                         "overwriting its pinned evidence.txt (a KG input)")
    args = ap.parse_args()

    # Incremental by default: previously-admitted entries pass through untouched so their
    # evidence files stay byte-stable (they are the PINNED inputs of already-run KG gates).
    prior = None
    if (OUT / "manifest.json").exists():
        prior = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    head = sh(["git", "rev-parse", "--short", "HEAD"], timeout=30)[1].strip()
    if prior and prior["base_commit"] != head and not args.all:
        sys.exit("fixture base moved ({0} -> {1}): prior evidence is no longer comparable. "
                 "Restore the base commit or rebuild the whole corpus with --all.".format(
                     prior["base_commit"], head))

    if args.task:
        tasks = [t for t in TASKS if t["id"] == args.task]
        if not tasks:
            sys.exit("no such task id")
        # Review #1: re-verifying an already-admitted task on the SAME base overwrites its
        # pinned evidence.txt (whose bytes carry run-specific timing) -> silently desyncs the
        # input from any KG result already computed on it. Refuse unless the operator opts in.
        prior_admitted = {a["id"] for a in prior["admitted"]} if prior else set()
        if args.task in prior_admitted and not (args.force or args.all):
            sys.exit("{0} is already admitted; re-verifying overwrites its pinned evidence.txt "
                     "(a KG input). Pass --force to refresh it deliberately.".format(args.task))
    elif args.all or prior is None:
        tasks = list(TASKS)
    else:
        admitted_ids = {a["id"] for a in prior["admitted"]}
        tasks = [t for t in TASKS if t["id"] not in admitted_ids]
        if not tasks:
            sys.exit("nothing new to verify (all defined tasks admitted; use --all to rebuild)")

    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for t in tasks:
        print("[verify] {0} ...".format(t["id"]), flush=True)
        r = verify_task(t)
        print("         {0}  {1}".format(r["verdict"], r.get("pytest_summary", "")), flush=True)
        results.append(r)
    ok = [r for r in results if r["verdict"] == "REPRODUCES"]
    rejected = [r for r in results if r["verdict"] != "REPRODUCES"]
    # Review P2: the ADMITTED list is the corpus; rejected results are kept for audit only.
    # Consumers (KG-1 runner, sweeps) MUST iterate manifest["admitted"], never a raw task list.
    ran = {r["id"] for r in results}
    admitted = ([a for a in prior["admitted"] if a["id"] not in ran] if prior else []) + ok
    rejected_all = ([r for r in prior.get("rejected_for_audit", []) if r["id"] not in ran]
                    if prior else []) + rejected
    order = {t["id"]: i for i, t in enumerate(TASKS)}
    admitted.sort(key=lambda a: order.get(a["id"], len(TASKS)))
    manifest = dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        repo=HTTPX, base_commit=head,
        python=PY, n_defined=len(TASKS), n_admitted=len(admitted),
        admitted=admitted, rejected_for_audit=rejected_all)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("\nthis run: {0}/{1} ADMITTED; corpus total {2}/{3} -> manifest at {4}".format(
        len(ok), len(tasks), len(admitted), len(TASKS), OUT / "manifest.json"))
    if len(ok) < len(tasks):
        sys.exit(1)


if __name__ == "__main__":
    main()
