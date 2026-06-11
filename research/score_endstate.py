#!/usr/bin/env python
r"""End-state (goal-completion) re-scorer for the chain1 seeded-timeout-bug runs.

WHY THIS EXISTS
---------------
The harness's per-turn scorer (chain_test_v2.score_turn) credits a turn only if it
*changed* an expected file that turn (file-hash diff). It is end-state-BLIND: a turn
that finds its work already done (over-delivered by an earlier turn) and correctly
verifies-and-stops scores 0, even though the repo end state is correct. On codex this
inflated slm_native (native resume keeps editing/polishing -> churn credit) over
with_session (fresh threads verify-and-stop). See codex_realbug_with_vs_builtin.md.

WHAT THIS DOES
--------------
Re-scores each run on whether the CHAIN GOAL was achieved by run-end, independent of
per-turn churn. Chain1 goal = the seeded timeout-propagation bug is fixed on BOTH
transports + regression tests exist for both.

HARD LIMITATION (be honest in any writeup)
------------------------------------------
codex applies edits via an internal apply_patch that emits only `file_change {path,kind}`
-- the edit CONTENT is never persisted, and runs were git-reset between each other, so
the final working tree does not survive. A fully objective re-score (reconstruct repo ->
run the real MockTransport verification) is therefore IMPOSSIBLE from this data. Instead
we mine the agents' OWN command outputs (rg / git diff / Get-Content / pytest) -- the
state the agent observed -- for each goal component, taking the LAST observation per site
("latest wins"). This is evidence-mined, not gold-standard: a run where the agent never
re-displayed a fix site after editing it is marked UNKNOWN, not assumed.

Sub-goals scored per run (latest observation wins):
  sync_fix   : HTTPTransport.handle_request forwards the timeout extension (bug line gone)
  async_fix  : AsyncHTTPTransport.handle_async_request forwards it
  sync_test  : a sync timeout-propagation test was written (file_change on a test file) AND
               a pytest run shows a timeout test passing
  async_test : async counterpart

Usage:  python research/score_endstate.py [out_base] [--arms a,b,c]
        out_base defaults to $PROMPTPILOT_OUT_DIR or the seededbug3 path.
"""
from __future__ import annotations
import json, os, re, sys, glob
from pathlib import Path

BUG_SUBSTR = 'if k != "timeout"'                       # the seeded bug filter
FIX_RE = re.compile(r'extensions\s*=\s*(?:dict\(\s*)?request\.extensions')  # forwarded
EXT_LINE = re.compile(r'extensions\s*=')               # any extensions= assignment line
# default.py line geography (dbeced8): sync handle_request ~247, async ~391.
SYNC_LINE_LO, SYNC_LINE_HI = 230, 300
ASYNC_LINE_LO, ASYNC_LINE_HI = 360, 420
PYTEST_RE = re.compile(r'(\d+)\s+passed|(\d+)\s+failed|(\d+)\s+error')
TIMEOUT_TEST_HINT = re.compile(r'timeout', re.I)
HUNK_RE = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)')      # git-diff hunk header -> new-file start line


def _iter_events(path: Path):
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except FileNotFoundError:
        return


def _cmd_output(item: dict) -> str:
    out = item.get('aggregated_output') or item.get('output') or ''
    return out if isinstance(out, str) else json.dumps(out)


def _classify_ext_line(line: str):
    """Return 'BUG' / 'FIX' / None for a line that mentions extensions= in default.py."""
    if BUG_SUBSTR in line:
        return 'BUG'
    if FIX_RE.search(line):
        return 'FIX'
    return None


def _linenum(line: str):
    m = re.search(r'default\.py[:\s]+(\d+)', line)
    if m:
        return int(m.group(1))
    return None


def _pytest_exercises_timeout(cmd_lc: str, changed_test_basenames: list) -> bool:
    """True iff a pytest command actually RUNS the timeout regression (not unrelated/excluded).

    A `-k EXPRESSION` restricts which tests run, so when present it is decisive: trust it only when the
    expression POSITIVELY selects a timeout test, never when it excludes one (`-k 'not timeout'` still
    contains the word 'timeout' but skips the regression -- codex bot, PR #34). Without `-k`, a timeout
    file/path/name target -- or re-running a just-modified test file -- runs the whole selection incl.
    the new test. Together with the named-PASS evidence this closes both the unrelated-green (PR #33)
    and the -k-exclusion (PR #34) holes. (`--deselect`/`--ignore` of the timeout test is a residual edge
    not covered here; the verbose named-PASS path remains the immune positive signal.)
    """
    if 'pytest' not in cmd_lc:
        return False
    km = re.search(r"-k[\s=]+(?:'([^']*)'|\"([^\"]*)\"|(\S+))", cmd_lc)
    if km:
        kexpr = km.group(1) or km.group(2) or km.group(3) or ''
        return ('timeout' in kexpr) and not re.search(r'not\s+\S*timeout', kexpr)
    return ('timeout' in cmd_lc) or any(b and b in cmd_lc for b in changed_test_basenames)


def extract_run(out_dir: Path, arm: str, run: int) -> dict:
    """Mine evidence for one run's end state from its t1..t5 transcripts."""
    test_changes = []           # (turn, kind, path)
    fix_obs = []                # (turn, site, BUG/FIX, linenum, source, snippet)
    diff_obs = []               # (turn, '+'/'-', BUG/FIX, snippet) from git diff hunks
    pytest_obs = []             # (turn, passed, failed, errored, line)
    timeout_pytest = []         # (turn, line) pytest lines naming a timeout test
    timeout_test_passed = []    # (turn) green pytest run that actually exercised the timeout regression
    final_msg = ''
    turns_present = 0

    for t in range(1, 6):
        f = out_dir / f'run{run}_{arm}_t{t}.jsonl'
        if not f.exists():
            continue
        turns_present += 1
        last_agent_msg = ''
        for ev in _iter_events(f):
            if not str(ev.get('type', '')).startswith('item'):
                continue
            it = ev.get('item', {})
            itype = it.get('type')
            if itype == 'file_change':
                for ch in it.get('changes', []) or []:
                    p = str(ch.get('path', '')).replace('\\', '/')
                    if '/test' in p.lower() or p.lower().endswith('_test.py') or '/tests/' in p.lower():
                        test_changes.append((t, ch.get('kind'), p.split('/')[-1]))
            elif itype == 'command_execution':
                out = _cmd_output(it)
                cmd_lc = str(it.get('command', '') or '').lower()
                is_pytest = 'pytest' in cmd_lc
                # does this pytest invocation actually RUN the timeout regression? A -k filter restricts
                # which tests run, so it is judged on whether it POSITIVELY selects timeout, not on the
                # raw command string -- `-k 'not timeout'` mentions 'timeout' but EXCLUDES the regression
                # (codex bot, PR #34). Without -k, a timeout file/path/name target runs the whole selection.
                cmd_exercises_timeout = _pytest_exercises_timeout(
                    cmd_lc, [tc[2].lower() for tc in test_changes])
                cmd_green = False
                hunk_site = None  # current git-diff hunk's transport, parsed from @@ headers
                for ln in out.splitlines():
                    low = ln
                    # track git-diff hunk header so +/- lines can be attributed to a transport
                    hm = HUNK_RE.match(ln.lstrip())
                    if hm:
                        c = int(hm.group(1))
                        hunk_site = ('sync' if SYNC_LINE_LO <= c <= SYNC_LINE_HI
                                     else 'async' if ASYNC_LINE_LO <= c <= ASYNC_LINE_HI else None)
                    # fix-site observations from rg / cat / Get-Content (have default.py + extensions=)
                    if 'default.py' in low and EXT_LINE.search(low):
                        cls = _classify_ext_line(low)
                        if cls:
                            n = _linenum(low)
                            site = None
                            if n is not None:
                                if SYNC_LINE_LO <= n <= SYNC_LINE_HI:
                                    site = 'sync'
                                elif ASYNC_LINE_LO <= n <= ASYNC_LINE_HI:
                                    site = 'async'
                            fix_obs.append((t, site, cls, n, 'cmd', low.strip()[:160]))
                    # git diff +/- lines touching extensions=, ATTRIBUTED to the current hunk's
                    # transport (sync ~244 / async ~388). A global, unattributed diff signal would let
                    # a one-transport fix falsely credit BOTH sites (codex bot review, PR #33).
                    s = ln.lstrip()
                    if (s.startswith('+') or s.startswith('-')) and EXT_LINE.search(ln) and 'request.extensions' in ln:
                        cls = _classify_ext_line(ln)
                        if cls:
                            diff_obs.append((t, hunk_site, s[0], cls, ln.strip()[:160]))
                    # pytest result lines
                    m = PYTEST_RE.search(ln)
                    if m and ('passed' in ln or 'failed' in ln or 'error' in ln):
                        passed = int(m.group(1)) if m.group(1) else 0
                        failed = int(m.group(2)) if m.group(2) else 0
                        errored = int(m.group(3)) if m.group(3) else 0
                        pytest_obs.append((t, passed, failed, errored, ln.strip()[:120]))
                        if passed > 0 and failed == 0 and errored == 0:
                            cmd_green = True
                    # a pytest line that names a timeout test passing (verbose-mode evidence)
                    if TIMEOUT_TEST_HINT.search(ln) and 'PASS' in ln.upper():
                        timeout_pytest.append((t, ln.strip()[:120]))
                # a GREEN pytest run that actually exercised the timeout regression counts as test-pass
                if cmd_exercises_timeout and cmd_green:
                    timeout_test_passed.append(t)
            elif itype == 'agent_message':
                txt = it.get('text') or it.get('content') or ''
                if isinstance(txt, str) and txt.strip():
                    last_agent_msg = txt.strip()
        if last_agent_msg:
            final_msg = last_agent_msg  # keep the latest turn's last message

    # ---- adjudicate per transport: latest fix-evidence vs latest bug-evidence, PER SITE ----
    # ALL evidence is site-attributed: rg/cat by line number (sync ~247, async ~391); git-diff +/- by
    # hunk @@ range. A site is FIXED only with its OWN positive fix evidence -- a fix to the OTHER
    # transport cannot credit it. This closes the global-diff false-FIXED hole the codex bot flagged on
    # PR #33: previously a one-transport fix (one '+extensions=request.extensions' line, indistinguishable
    # between the two identical bug sites) credited BOTH sites and masked a partial fix.
    # Fix evidence for a site: rg/cat shows the forwarded form in range, OR a diff hunk in range ADDS the
    # forward (+FIX) or REMOVES the bug (-BUG). Bug evidence: rg/cat shows the bug in range, OR a diff
    # hunk in range ADDS the bug (+BUG). A stale early rg-BUG cannot override a later fix (latest wins).
    def site_fix_turn(site):
        ts = [o[0] for o in fix_obs if o[1] == site and o[2] == 'FIX']
        ts += [d[0] for d in diff_obs if d[1] == site and d[2] == '+' and d[3] == 'FIX']
        ts += [d[0] for d in diff_obs if d[1] == site and d[2] == '-' and d[3] == 'BUG']
        return max(ts, default=-1)

    def site_bug_turn(site):
        ts = [o[0] for o in fix_obs if o[1] == site and o[2] == 'BUG']
        ts += [d[0] for d in diff_obs if d[1] == site and d[2] == '+' and d[3] == 'BUG']
        return max(ts, default=-1)

    def verdict(site):
        fix_t = site_fix_turn(site)
        bug_t = site_bug_turn(site)
        if fix_t >= 0 and fix_t >= bug_t:
            return 'FIXED'
        if bug_t > fix_t:
            return 'BUG'
        return 'UNKNOWN'   # no site-attributed evidence either way -> conservative (scores None)

    sync_v = verdict('sync')
    async_v = verdict('async')
    diff_says_fixed = any(d[1] in ('sync', 'async')
                          and ((d[2] == '+' and d[3] == 'FIX') or (d[2] == '-' and d[3] == 'BUG'))
                          for d in diff_obs)
    sync_state = 'bug@{0}/fix@{1}'.format(site_bug_turn('sync'), site_fix_turn('sync'))
    async_state = 'bug@{0}/fix@{1}'.format(site_bug_turn('async'), site_fix_turn('async'))

    # tests
    test_files = sorted({tc[2] for tc in test_changes})
    any_test_written = len(test_files) > 0
    pytest_pass = [p for p in pytest_obs if p[1] > 0 and p[2] == 0 and p[3] == 0]  # any all-green (contrast)
    last_pytest = pytest_obs[-1] if pytest_obs else None
    # tests_pass requires evidence the TIMEOUT regression specifically passed: a green pytest run that
    # exercised it (targeted by name/-k, or re-ran the changed test file), or a verbose PASSED line
    # naming a timeout test. A generic all-green summary is NOT sufficient (codex bot, PR #33).
    tests_pass = bool(timeout_test_passed) or bool(timeout_pytest)

    return {
        'arm': arm, 'run': run, 'turns_present': turns_present,
        'sync': sync_v, 'async': async_v,
        'sync_state': sync_state, 'async_state': async_state,
        'diff_says_fixed': diff_says_fixed,
        'test_files': test_files, 'any_test_written': any_test_written,
        'tests_pass': tests_pass,
        'timeout_test_evidence': 'targeted-green' if timeout_test_passed else (
            'named-PASS' if timeout_pytest else 'none'),
        'last_pytest': last_pytest,
        'n_fix_obs': len(fix_obs), 'n_diff_obs': len(diff_obs), 'n_pytest': len(pytest_obs),
        'final_msg': final_msg[:240],
        '_fix_obs_tail': fix_obs[-6:],
        '_diff_obs_tail': diff_obs[-6:],
    }


def endstate_score(r: dict):
    """Map sub-goal verdicts to a 0/0.5/1 end-state + a confidence flag."""
    fixed = lambda v: v == 'FIXED'
    both_fixed = fixed(r['sync']) and fixed(r['async'])
    any_unknown = 'UNKNOWN' in (r['sync'], r['async'])
    if both_fixed and r['any_test_written'] and r['tests_pass']:
        score = 1.0
    elif both_fixed:
        score = 0.75            # bug fully fixed, tests partial/unverified
    elif fixed(r['sync']) or fixed(r['async']):
        score = 0.5             # one transport fixed
    elif any_unknown:
        score = None            # insufficient evidence
    else:
        score = 0.0
    conf = 'low' if any_unknown else 'high'
    return score, conf


def score_captured_endstate(es: dict, arm: str = '', run: int = 0) -> dict:
    """Score a CAPTURED end-state artifact (git diff + live timeout-pytest) produced by
    chain_test_v2.capture_end_state — the GOLD-STANDARD, tool-agnostic alternative to
    transcript mining (identical for claude-code and codex; this is the claude generalization).

    Key difference from the miner: the captured diff is the COMPLETE working-tree delta vs the
    seeded-bug fixture, so a transport with NO diff hunk is UNCHANGED = still buggy = BUG, never
    UNKNOWN — absence of evidence here IS evidence, because the base is known-buggy on both sites.
    Returns an extract_run-shaped dict so endstate_score() maps it unchanged.
    """
    diff = es.get('diff', '') or ''
    cur_default = False
    hunk_site = None
    fix = {'sync': False, 'async': False}   # saw +FIX (forward) or -BUG (bug removed) in this site
    bug = {'sync': False, 'async': False}   # saw +BUG (residual/re-added bug) in this site
    for ln in diff.splitlines():
        if ln.startswith('+++ ') or ln.startswith('--- '):
            cur_default = ln.replace('\\', '/').rstrip().endswith('default.py')
            hunk_site = None
            continue
        if not cur_default:
            continue
        m = HUNK_RE.match(ln)
        if m:
            c = int(m.group(1))
            hunk_site = ('sync' if SYNC_LINE_LO <= c <= SYNC_LINE_HI
                         else 'async' if ASYNC_LINE_LO <= c <= ASYNC_LINE_HI else None)
            continue
        if hunk_site is None or not (ln[:1] in ('+', '-')):
            continue
        cls = _classify_ext_line(ln)
        if cls is None:
            continue
        if ln[0] == '+' and cls == 'FIX':
            fix[hunk_site] = True
        elif ln[0] == '-' and cls == 'BUG':
            fix[hunk_site] = True          # removing the seeded bug line is a fix
        elif ln[0] == '+' and cls == 'BUG':
            bug[hunk_site] = True          # bug present/re-added in the final tree

    def verdict(site):
        return 'FIXED' if (fix[site] and not bug[site]) else 'BUG'

    sync_v, async_v = verdict('sync'), verdict('async')

    # tests: a timeout regression test exists (new or modified) AND the live -k timeout run was green
    tf = {Path(f).name for f in es.get('new_files', []) if f.lower().endswith('.py') and 'test' in f.lower()}
    for ln in diff.splitlines():
        if ln.startswith('+++ ') and 'test' in ln.lower() and ln.rstrip().endswith('.py'):
            tf.add(Path(ln[4:].strip()).name)
    test_files = sorted(tf)
    tests_pass = bool(es.get('pytest_passed'))     # real run, -k timeout, rc == 0
    has_pytest = 'pytest_rc' in es
    last_pytest = None
    if has_pytest:
        tail = (es.get('pytest_tail', '') or '').splitlines()
        last_pytest = (0, 1 if tests_pass else 0, 0 if tests_pass else 1, 0, tail[-1] if tail else '')

    return {
        'arm': arm, 'run': run, 'turns_present': 1,
        'sync': sync_v, 'async': async_v,
        'sync_state': 'captured', 'async_state': 'captured',
        'diff_says_fixed': fix['sync'] or fix['async'],
        'test_files': test_files, 'any_test_written': bool(test_files),
        'tests_pass': tests_pass,
        'timeout_test_evidence': 'live-pytest-green' if tests_pass else (
            'no-timeout-test' if es.get('pytest_no_match') else
            ('live-pytest-fail' if has_pytest else 'none')),
        'last_pytest': last_pytest,
        'n_fix_obs': sum(fix.values()), 'n_diff_obs': len(diff.splitlines()),
        'n_pytest': 1 if has_pytest else 0,
        'final_msg': '', '_fix_obs_tail': [], '_diff_obs_tail': [],
        'source': 'captured',
    }


def _score_run(out_dir: Path, arm: str, run: int) -> tuple[dict, str]:
    """Prefer a captured end-state artifact (gold) when present; else mine the transcript."""
    es_path = out_dir / 'endstate_{0}_run{1}.json'.format(arm, run)
    if es_path.exists():
        try:
            es = json.loads(es_path.read_text(encoding='utf-8'))
            return score_captured_endstate(es, arm, run), 'captured'
        except Exception:
            pass
    return extract_run(out_dir, arm, run), 'mined'


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    out_base = Path(args[0]) if args else Path(
        os.environ.get('PROMPTPILOT_OUT_DIR', r'B:\LLM\_session_retest_2026-06-07\seededbug3'))
    arms_arg = next((a for a in sys.argv[1:] if a.startswith('--arms')), None)
    arms = arms_arg.split('=', 1)[1].split(',') if arms_arg and '=' in arms_arg \
        else ['with_session', 'slm_native', 'builtin']
    out_dir = out_base / 'codex' / 'chain1'

    print(f'End-state re-score  (out={out_dir})')
    print('=' * 104)
    arm_scores = {}
    for arm in arms:
        runs = sorted(int(re.search(r'run(\d+)\.json$', f).group(1))
                      for f in glob.glob(str(out_dir / f'{arm}_run*.json')))
        if not runs:
            continue
        print(f'\n### {arm}  ({len(runs)} runs)')
        print(f'  run | sync   async  | test_files                          pytest        | end  conf | evidence')
        scored = []
        for run in runs:
            r, source = _score_run(out_dir, arm, run)
            score, conf = endstate_score(r)
            scored.append(score)
            lp = r['last_pytest']
            lpstr = (f"{lp[1]}p/{lp[2]}f[{r['timeout_test_evidence']}]" if lp else '-')
            tf = ','.join(r['test_files'])[:34] or '(none)'
            sc = '----' if score is None else f'{score:.2f}'
            src = 'GOLD' if source == 'captured' else 'mine'
            print(f"   {run}  | {r['sync']:<6} {r['async']:<6}| {tf:<28} {lpstr:<26}| {sc} {conf:<4}| "
                  f"{src} fixobs={r['n_fix_obs']} diffobs={r['n_diff_obs']} diff_fixed={r['diff_says_fixed']}")
        clean = [s for s in scored if s is not None]
        arm_scores[arm] = clean
        if clean:
            print(f'  --> end-state mean: {sum(clean)/len(clean):.3f}  '
                  f'(fully-fixed runs: {sum(1 for s in scored if s and s>=0.75)}/{len(scored)}; '
                  f'unscored/unknown: {sum(1 for s in scored if s is None)})')

    print('\n' + '=' * 104)
    print('SUMMARY (end-state mean per arm):')
    for arm in arms:
        if arm in arm_scores and arm_scores[arm]:
            cs = arm_scores[arm]
            print(f'  {arm:<14} {sum(cs)/len(cs):.3f}   (n={len(cs)})')


if __name__ == '__main__':
    main()
