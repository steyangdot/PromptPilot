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


def extract_run(out_dir: Path, arm: str, run: int) -> dict:
    """Mine evidence for one run's end state from its t1..t5 transcripts."""
    test_changes = []           # (turn, kind, path)
    fix_obs = []                # (turn, site, BUG/FIX, linenum, source, snippet)
    diff_obs = []               # (turn, '+'/'-', BUG/FIX, snippet) from git diff hunks
    pytest_obs = []             # (turn, passed, failed, errored, line)
    timeout_pytest = []         # (turn, line) pytest lines naming a timeout test
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
                for ln in out.splitlines():
                    low = ln
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
                    # git diff +/- lines touching extensions=
                    s = ln.lstrip()
                    if (s.startswith('+') or s.startswith('-')) and EXT_LINE.search(ln) and 'request.extensions' in ln:
                        cls = _classify_ext_line(ln)
                        if cls:
                            diff_obs.append((t, s[0], cls, ln.strip()[:160]))
                    # pytest result lines
                    m = PYTEST_RE.search(ln)
                    if m and ('passed' in ln or 'failed' in ln or 'error' in ln):
                        passed = int(m.group(1)) if m.group(1) else 0
                        failed = int(m.group(2)) if m.group(2) else 0
                        errored = int(m.group(3)) if m.group(3) else 0
                        pytest_obs.append((t, passed, failed, errored, ln.strip()[:120]))
                    # a pytest line that names a timeout test passing
                    if TIMEOUT_TEST_HINT.search(ln) and 'PASS' in ln.upper():
                        timeout_pytest.append((t, ln.strip()[:120]))
            elif itype == 'agent_message':
                txt = it.get('text') or it.get('content') or ''
                if isinstance(txt, str) and txt.strip():
                    last_agent_msg = txt.strip()
        if last_agent_msg:
            final_msg = last_agent_msg  # keep the latest turn's last message

    # ---- adjudicate each sub-goal: LATEST evidence wins across rg/cat + git diff ----
    # git diff is the authoritative working-tree-vs-clean-base delta: a '+extensions=request.extensions'
    # hunk means the fix is applied to the tree at that turn. rg/cat with a line number attribute to a
    # site (sync ~247, async ~391). A stale EARLY rg showing the BUG (the agent's initial exploration)
    # must NOT override a LATER diff/rg showing the fix -- that was the v1 scorer bug. We therefore compare
    # the latest fix-evidence turn against the latest bug-evidence turn per site.
    def site_turns(site):
        bug_t = max([o[0] for o in fix_obs if o[1] == site and o[2] == 'BUG'], default=-1)
        fix_t = max([o[0] for o in fix_obs if o[1] == site and o[2] == 'FIX'], default=-1)
        return bug_t, fix_t

    # git-diff FIX line = '+extensions=request.extensions' (the bug-removing edit, applied to the tree).
    # Not site-attributed (hunks lack line numbers here) -> treated as a global fix-applied signal.
    diff_fix_turn = max([d[0] for d in diff_obs if d[1] == '+' and d[2] == 'FIX'], default=-1)
    diff_bug_readd_turn = max([d[0] for d in diff_obs if d[1] == '+' and d[2] == 'BUG'], default=-1)
    diff_says_fixed = diff_fix_turn >= 0

    def verdict(site):
        bug_t, fix_t = site_turns(site)
        best_fix = max(fix_t, diff_fix_turn)            # latest fix evidence (rg/cat at this site, or diff)
        latest_bug = max(bug_t, diff_bug_readd_turn)    # latest bug evidence
        if best_fix >= 0 and best_fix >= latest_bug:
            return 'FIXED'
        if latest_bug > best_fix:
            return 'BUG'
        return 'UNKNOWN'                                 # no evidence either way

    sync_v = verdict('sync')
    async_v = verdict('async')
    sync_state = 'bug@{0}/fix@{1}'.format(*site_turns('sync'))
    async_state = 'bug@{0}/fix@{1}'.format(*site_turns('async'))

    # tests
    test_files = sorted({tc[2] for tc in test_changes})
    any_test_written = len(test_files) > 0
    pytest_pass = [p for p in pytest_obs if p[1] > 0 and p[2] == 0 and p[3] == 0]
    last_pytest = pytest_obs[-1] if pytest_obs else None
    tests_pass = bool(pytest_pass)

    return {
        'arm': arm, 'run': run, 'turns_present': turns_present,
        'sync': sync_v, 'async': async_v,
        'sync_state': sync_state, 'async_state': async_state,
        'diff_says_fixed': diff_says_fixed,
        'test_files': test_files, 'any_test_written': any_test_written,
        'tests_pass': tests_pass,
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
            r = extract_run(out_dir, arm, run)
            score, conf = endstate_score(r)
            scored.append(score)
            lp = r['last_pytest']
            lpstr = (f"{lp[1]}p/{lp[2]}f" if lp else '-')
            tf = ','.join(r['test_files'])[:34] or '(none)'
            sc = '----' if score is None else f'{score:.2f}'
            print(f"   {run}  | {r['sync']:<6} {r['async']:<6}| {tf:<34} {lpstr:<13}| {sc} {conf:<4}| "
                  f"fixobs={r['n_fix_obs']} diffobs={r['n_diff_obs']} diff_fixed={r['diff_says_fixed']}")
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
