"""
prpt.compress.tool_output
============================
Command-type-aware compression of Bash tool output.

Intercepts large tool responses before they enter the LLM context window,
stripping noise (passing tests, blank context lines, boilerplate banners)
while preserving every actionable byte (failures, errors, change lines).

Public API
----------
    compress(command, output) -> str
        Main entry point.  Returns compressed text, or original if savings
        are below MIN_SAVINGS_RATIO (avoids penalising already-terse output).

    detect_command_type(command) -> str
        Returns a tag string; useful for logging / unit tests.

Supported command families
--------------------------
    pytest          python -m pytest / pytest
    test_generic    cargo test / go test / npm test / jest / vitest
    linter          tsc / eslint / ruff / mypy / flake8 / pylint
    installer       pip install / npm install / cargo build / yarn add
    grep            grep / rg (ripgrep)
    git_diff        git diff [options]
    git_status      git status
    git_log         git log [options]
    find            find . [options]
    ls              ls / dir
    generic         everything else → smart head+tail truncation
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MIN_SAVINGS_RATIO = 0.20          # only apply if we save ≥20 % of chars
GREP_MAX_PER_FILE = 5             # show at most N matches per file
GREP_MAX_FILES_BEFORE_DIR = 20    # beyond this, collapse to dir-level counts
GIT_DIFF_MAX_CONTEXT = 3          # context lines kept around each change line
FIND_MAX_FULL_PATHS = 30          # full paths before switching to dir counts
SMART_HEAD = 200                  # lines kept from the top in fallback mode
SMART_TAIL = 50                   # lines kept from the bottom in fallback mode
LINTER_MAX_PER_FILE = 10          # linter diagnostics kept per file
INSTALLER_MAX_LINES = 40          # installer progress kept (errors always kept)


# ---------------------------------------------------------------------------
# ANSI escape sequence stripper
# ---------------------------------------------------------------------------
# Matches CSI sequences (\x1b[...m), OSC (\x1b]...\x07), and a few stray forms
_ANSI_RE = re.compile(
    r'\x1b\[[0-9;?]*[A-Za-z]'        # CSI (colors, cursor)
    r'|\x1b\][^\x07]*\x07'           # OSC (titles, hyperlinks)
    r'|\x1b[=>]'                     # keypad mode
    r'|\r(?!\n)'                     # bare CR (progress-bar rewind)
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and bare carriage returns."""
    return _ANSI_RE.sub('', text)


def _parent_dir(path: str) -> str:
    """
    Cross-platform parent-directory string.

    Normalises backslashes to forward slashes so Windows paths
    (`C:\\foo\\bar.py`) produce stable keys; returns "." when no parent.
    """
    norm = path.replace('\\', '/').rstrip('/')
    if '/' not in norm:
        return '.'
    return str(PurePosixPath(norm).parent)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

# Regex catalogue used by both the detector and the command-chain splitter
_CMD_PATTERNS = [
    # Order matters: more specific patterns first
    ('pytest',     re.compile(r'\bpytest\b|python\s+-m\s+pytest\b', re.I)),
    ('linter',     re.compile(r'\b(tsc|eslint|ruff|mypy|flake8|pylint|ts-node\s+--?type-check)\b'
                              r'|python\s+-m\s+(mypy|ruff|flake8|pylint)\b', re.I)),
    ('test_generic', re.compile(r'\bcargo\s+test\b|\bgo\s+test\b|\bnpm\s+(run\s+)?test\b'
                                r'|\byarn\s+test\b|\bjest\b|\bvitest\b|\bmocha\b', re.I)),
    ('installer',  re.compile(r'\b(pip\s+install|npm\s+install|npm\s+ci|yarn\s+(add|install)'
                              r'|cargo\s+(build|install)|poetry\s+(install|add)|pnpm\s+(install|add))\b',
                              re.I)),
    ('grep',       re.compile(r'(?:^|[\s|&;])(rg|grep)\b', re.I)),
    ('git_diff',   re.compile(r'\bgit\s+diff\b', re.I)),
    ('git_status', re.compile(r'\bgit\s+status\b', re.I)),
    ('git_log',    re.compile(r'\bgit\s+log\b', re.I)),
    ('find',       re.compile(r'(?:^|[\s|&;])find\s+', re.I)),
    ('ls',         re.compile(r'(?:^|[\s|&;])(ls|dir)\b', re.I)),
]

# Splits a command string on pipe / && / ; / || respecting single+double quotes.
_CHAIN_SPLIT_RE = re.compile(
    r'''(?:[^|&;"']|"[^"]*"|'[^']*')+''',
)


def _strip_env_prefix(cmd: str) -> str:
    """Strip leading `FOO=bar BAZ="x y" …` env-var assignments."""
    # Handles unquoted, double-quoted, single-quoted values.
    return re.sub(
        r'''^(\s*\w+=(?:"[^"]*"|'[^']*'|\S*)\s+)+''',
        '',
        cmd,
    )


def _split_command_chain(command: str) -> list[str]:
    """Break `cmd1 && cmd2 | tee x` into ['cmd1', 'cmd2', 'tee x']."""
    parts = _CHAIN_SPLIT_RE.findall(command)
    return [p.strip() for p in parts if p.strip()]


def detect_command_type(command: str) -> str:
    """
    Return a stable tag for the command family.

    Command chains (`a && b | c`) are scanned component-by-component; the
    first component whose pattern matches wins — tied to pattern order, so
    specific families (pytest, linter) outrank generic ones (ls, grep).
    """
    chain = _split_command_chain(command.strip())
    if not chain:
        return 'generic'

    candidates = [_strip_env_prefix(p) for p in chain]

    for tag, pat in _CMD_PATTERNS:
        for piece in candidates:
            if pat.search(piece):
                return tag
    return 'generic'


def compress(command: str, output: str) -> str:
    """
    Compress *output* produced by *command*.

    Returns the compressed string if it is at least MIN_SAVINGS_RATIO shorter
    than the original; otherwise returns the original unchanged.
    """
    if not output or not output.strip():
        return output

    # Pre-process: strip ANSI escapes so regex patterns match reliably
    clean = strip_ansi(output)

    kind = detect_command_type(command)

    try:
        if kind == 'pytest':
            result = compress_pytest(clean)
        elif kind == 'test_generic':
            result = compress_test_generic(clean)
        elif kind == 'linter':
            result = compress_linter(clean)
        elif kind == 'installer':
            result = compress_installer(clean)
        elif kind == 'grep':
            result = compress_grep(clean)
        elif kind == 'git_diff':
            result = compress_git_diff(clean)
        elif kind == 'git_status':
            result = compress_git_status(clean)
        elif kind == 'git_log':
            result = compress_git_log(clean)
        elif kind == 'find':
            result = compress_find(clean)
        elif kind == 'ls':
            result = compress_ls(clean)
        else:
            result = truncate_smart(clean)
    except Exception:
        return output  # fail open

    # Compare against original (ANSI-included) length so savings gate isn't
    # artificially inflated by just stripping colour codes.
    savings = 1.0 - len(result) / max(len(output), 1)
    return result if savings >= MIN_SAVINGS_RATIO else output


# ---------------------------------------------------------------------------
# pytest compressor
# ---------------------------------------------------------------------------
_RE_SECTION = re.compile(r'^={3,}(.+?)={3,}$')
_RE_FINAL_SUMMARY = re.compile(
    r'={3,}.*?(\d+\s+(?:failed|passed|error|warning)).*?={3,}', re.I
)


def compress_pytest(output: str) -> str:
    """
    Keep:
      • The entire FAILURES / ERRORS sections (tracebacks + assertion details)
      • The "short test summary info" section
      • The final === N failed, M passed … === line
      • Any standalone FAILED/ERROR lines before the FAILURES section
    Drop:
      • Test session header (platform, plugins, rootdir)
      • Collected N items lines
      • PASSED lines
      • Deprecation warning blocks
    """
    lines = output.splitlines()
    kept: list[str] = []

    # Locate where structured sections start
    failures_idx: int | None = None
    for i, line in enumerate(lines):
        s = line.strip()
        if re.match(r'={3,}\s*(?:FAILURES|ERRORS)\s*={3,}', s):
            failures_idx = i
            break

    in_keep_section = False

    for i, line in enumerate(lines):
        s = line.strip()

        # Once we hit the FAILURES/ERRORS section keep everything to the end
        if failures_idx is not None and i >= failures_idx:
            in_keep_section = True

        if in_keep_section:
            kept.append(line)
            continue

        # Before the FAILURES section: only keep actionable lines
        if s.startswith('FAILED') or re.match(r'ERROR\s+', s) or s.startswith('ERROR:'):
            kept.append(line)
            continue

        # Final summary banner (e.g. "====  1 failed, 47 passed in 2.3s  ====")
        if _RE_FINAL_SUMMARY.match(s):
            kept.append(line)
            continue

    # If nothing actionable found (all passed), emit just the final summary line
    if not kept:
        for line in reversed(lines):
            if _RE_FINAL_SUMMARY.match(line.strip()):
                kept = [line]
                break
        if not kept:
            kept = lines[-1:]  # last line as fallback

    return '\n'.join(kept)


# ---------------------------------------------------------------------------
# Generic test runner compressor  (cargo test, go test, jest …)
# ---------------------------------------------------------------------------
_RE_GENERIC_FAIL = re.compile(
    r'\b(FAIL|FAILED|FAILING|error|Error|ERROR|✗|✘|×)\b', re.I
)
_RE_GENERIC_PASS = re.compile(
    r'\b(ok|PASS|PASSED|PASSING|✓|✔|·)\b'
)


def compress_test_generic(output: str) -> str:
    """
    Heuristic for non-pytest test runners:
      keep lines that contain failure/error keywords,
      keep the last 10 lines (summary),
      drop pure-pass lines.
    """
    lines = output.splitlines()
    tail = lines[-10:]
    kept: list[str] = []
    for line in lines[:-10]:
        if _RE_GENERIC_FAIL.search(line):
            kept.append(line)
        elif not _RE_GENERIC_PASS.search(line):
            # Non-pass, non-fail lines (build output, progress bars) — keep
            if line.strip():
                kept.append(line)
    kept.extend(tail)
    return '\n'.join(kept)


# ---------------------------------------------------------------------------
# grep / rg compressor
# ---------------------------------------------------------------------------
_RE_GREP_LINE = re.compile(r'^(.+?)(?::(\d+))?:(.*)$')


def compress_grep(output: str) -> str:
    """
    Group matches by file.  Show at most GREP_MAX_PER_FILE matches per file,
    then a "… N more in <path>" line.  If total unique files exceeds
    GREP_MAX_FILES_BEFORE_DIR, collapse to directory-level counts.
    """
    lines = output.splitlines()
    # Separate match lines from header/context lines (grep -C context lines
    # use '--' separators and lines with no leading file path).
    file_matches: dict[str, list[str]] = defaultdict(list)
    non_match: list[str] = []

    for line in lines:
        m = _RE_GREP_LINE.match(line)
        path = m.group(1) if m else ''
        has_path_sep = bool(path) and ('/' in path or '\\' in path)
        if has_path_sep:
            file_matches[path].append(line)
        elif line.startswith('Binary file') or line.startswith('--'):
            non_match.append(line)
        else:
            # Could be a bare match (no filename prefix) or noise
            if line.strip():
                non_match.append(line)

    # If file-keyed parsing captured nothing, fall back to smart truncate
    if not file_matches:
        return truncate_smart(output, head=100, tail=20)

    unique_files = list(file_matches.keys())
    kept: list[str] = []

    if len(unique_files) > GREP_MAX_FILES_BEFORE_DIR:
        # Collapse to directory-level summary
        dir_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {'files': 0, 'matches': 0}
        )
        for path, matches in file_matches.items():
            parent = _parent_dir(path)
            dir_counts[parent]['files'] += 1
            dir_counts[parent]['matches'] += len(matches)
        for d, c in sorted(dir_counts.items()):
            kept.append(f"{d}/ ({c['files']} files, {c['matches']} matches)")
    else:
        for path, matches in file_matches.items():
            shown = matches[:GREP_MAX_PER_FILE]
            remaining = len(matches) - len(shown)
            kept.extend(shown)
            if remaining > 0:
                kept.append(f"  … {remaining} more match(es) in {path}")

    kept.extend(non_match[:5])  # keep a few non-match lines (binary notices etc.)
    total = sum(len(v) for v in file_matches.values())
    kept.append(f"\n[promptpilot] {total} total match(es) across {len(unique_files)} file(s)")
    return '\n'.join(kept)


# ---------------------------------------------------------------------------
# git diff compressor
# ---------------------------------------------------------------------------

def compress_git_diff(output: str) -> str:
    """
    Keep all +/- change lines and hunk headers.
    Trim context lines (lines starting with a space) to at most
    GIT_DIFF_MAX_CONTEXT lines before/after each change block.
    Cap each file's diff at 150 lines; emit "… N lines omitted" if exceeded.
    """
    lines = output.splitlines()
    # Group into per-file sections separated by "diff --git" headers
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith('diff --git'):
            if current:
                sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)

    kept: list[str] = []
    for section in sections:
        kept.extend(_trim_diff_section(section))

    return '\n'.join(kept)


def _trim_diff_section(lines: list[str]) -> list[str]:
    """Trim one file's diff section."""
    MAX_LINES = 150
    result: list[str] = []
    context_buffer: list[str] = []   # pending context lines
    after_change: int = 0            # context lines emitted after last change

    for line in lines:
        is_change = line.startswith('+') or line.startswith('-')
        is_header = (line.startswith('diff ') or line.startswith('@@')
                     or line.startswith('index ') or line.startswith('---')
                     or line.startswith('+++'))
        is_context = line.startswith(' ') or (not is_change and not is_header)

        if is_header:
            context_buffer = []
            after_change = 0
            result.append(line)
        elif is_change:
            # Flush buffered context (last N lines before this change)
            flush = context_buffer[-GIT_DIFF_MAX_CONTEXT:]
            if len(context_buffer) > GIT_DIFF_MAX_CONTEXT:
                result.append(f'  … {len(context_buffer) - GIT_DIFF_MAX_CONTEXT} context line(s) omitted')
            result.extend(flush)
            context_buffer = []
            after_change = 0
            result.append(line)
        elif is_context:
            if after_change < GIT_DIFF_MAX_CONTEXT:
                result.append(line)
                after_change += 1
            else:
                context_buffer.append(line)
        else:
            result.append(line)

    if len(result) > MAX_LINES:
        omitted = len(result) - MAX_LINES
        result = result[:MAX_LINES]
        result.append(f'  … {omitted} more line(s) omitted in this file')

    return result


# ---------------------------------------------------------------------------
# git status compressor
# ---------------------------------------------------------------------------
_RE_UNTRACKED_HEADER = re.compile(r'Untracked files:', re.I)


def compress_git_status(output: str) -> str:
    """
    Keep staged and unstaged change lines as-is (usually short).
    Collapse untracked files that exceed 20 entries into directory counts.
    """
    lines = output.splitlines()
    if len(lines) <= 30:
        return output  # already short enough, skip

    untracked_start: int | None = None
    for i, line in enumerate(lines):
        if _RE_UNTRACKED_HEADER.search(line):
            untracked_start = i
            break

    if untracked_start is None:
        return truncate_smart(output, head=60, tail=5)

    header_lines = lines[:untracked_start + 1]
    untracked_raw = [l for l in lines[untracked_start + 1:] if l.strip()
                     and not l.strip().startswith('(')]

    if len(untracked_raw) <= 20:
        return output  # not worth collapsing

    dir_counts: dict[str, int] = defaultdict(int)
    for path in untracked_raw:
        p = path.strip()
        parent = _parent_dir(p)
        dir_counts[parent] += 1

    kept = list(header_lines)
    kept.append(f'  ({len(untracked_raw)} untracked files, collapsed by directory:)')
    for d, c in sorted(dir_counts.items()):
        kept.append(f'  {d}/ ({c} file(s))')
    return '\n'.join(kept)


# ---------------------------------------------------------------------------
# git log compressor
# ---------------------------------------------------------------------------

def compress_git_log(output: str) -> str:
    """Keep first 30 log entries; truncate the rest with a count."""
    lines = output.splitlines()
    # Each commit block starts with "commit <sha>"
    commits: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith('commit ') and current:
            commits.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        commits.append(current)

    MAX_COMMITS = 30
    if len(commits) <= MAX_COMMITS:
        return output

    kept_commits = commits[:MAX_COMMITS]
    omitted = len(commits) - MAX_COMMITS
    result_lines: list[str] = []
    for c in kept_commits:
        result_lines.extend(c)
    result_lines.append(f'\n[promptpilot] … {omitted} older commit(s) omitted')
    return '\n'.join(result_lines)


# ---------------------------------------------------------------------------
# find compressor
# ---------------------------------------------------------------------------

def compress_find(output: str) -> str:
    """
    Show FIND_MAX_FULL_PATHS full paths then switch to per-directory counts.
    """
    lines = [l for l in output.splitlines() if l.strip()]
    if len(lines) <= FIND_MAX_FULL_PATHS:
        return output  # already short

    shown = lines[:FIND_MAX_FULL_PATHS]
    remaining = lines[FIND_MAX_FULL_PATHS:]

    dir_counts: dict[str, int] = defaultdict(int)
    for path in remaining:
        parent = _parent_dir(path.strip())
        dir_counts[parent] += 1

    kept = list(shown)
    kept.append(f'\n[promptpilot] … {len(remaining)} more path(s), by directory:')
    for d, c in sorted(dir_counts.items()):
        kept.append(f'  {d}/ ({c})')
    return '\n'.join(kept)


# ---------------------------------------------------------------------------
# ls / dir compressor
# ---------------------------------------------------------------------------

def compress_ls(output: str) -> str:
    """
    For recursive ls (-R) or very long listings, collapse deep directories.
    Depth-0/1 entries kept in full; deeper entries summarised as dir counts.
    """
    lines = output.splitlines()
    if len(lines) <= 80:
        return output

    # Detect recursive ls format: directory headers look like "./foo/bar:"
    dir_header = re.compile(r'^\.?/[\w./\\-]+:$|^[A-Z]:[/\\][\w./\\-]+:$')
    in_deep = False
    deep_counts: dict[str, int] = defaultdict(int)
    kept: list[str] = []
    current_dir = ''
    depth = 0

    for line in lines:
        s = line.strip()
        if dir_header.match(s):
            current_dir = s.rstrip(':')
            depth = current_dir.count('/') + current_dir.count('\\')
            in_deep = depth >= 3
            if not in_deep:
                kept.append(line)
        elif in_deep:
            if s:
                deep_counts[current_dir] += 1
        else:
            kept.append(line)

    if deep_counts:
        kept.append(f'\n[promptpilot] … deep directories (≥3 levels) collapsed:')
        for d, c in sorted(deep_counts.items()):
            kept.append(f'  {d}/ ({c} entries)')

    return '\n'.join(kept)


# ---------------------------------------------------------------------------
# Smart truncation fallback
# ---------------------------------------------------------------------------

def truncate_smart(output: str, head: int = SMART_HEAD, tail: int = SMART_TAIL) -> str:
    """
    Keep the first *head* lines and last *tail* lines with a separator.
    Preserves both the start (often shows what was invoked) and the end
    (often the summary / final error message).
    """
    lines = output.splitlines()
    total = len(lines)
    if total <= head + tail:
        return output

    omitted = total - head - tail
    kept = (
        lines[:head]
        + [f'\n[promptpilot] … {omitted} line(s) omitted …\n']
        + lines[-tail:]
    )
    return '\n'.join(kept)


# ---------------------------------------------------------------------------
# Linter compressor  (tsc / eslint / ruff / mypy / flake8 / pylint)
# ---------------------------------------------------------------------------
# All six common linters converge on a roughly similar line shape:
#   path/file.ext:LINE:COL: severity: message   (mypy, ruff, flake8, pylint)
#   path/file.ts(LINE,COL): error TSxxxx: message  (tsc)
#   /abs/path.js:LINE:COL:  X  message  rule-id  (eslint)
#
# Strategy:
#   • Treat any line containing ": error" or ": warning" (case-insensitive) or
#     a numeric `path:line:col` as a diagnostic.
#   • Group by file, cap LINTER_MAX_PER_FILE per file, emit "… N more in file".
#   • Keep the last ~10 lines (summary: "Found 42 errors in 7 files.")
_RE_LINTER_DIAG = re.compile(
    r'^(?P<path>[^\s:()]+\.[A-Za-z0-9]+)'         # path/to/file.ext
    r'(?:[:(](?P<line>\d+)(?:[,:](?P<col>\d+))?\)?)?'  # :LINE[:COL] or (LINE,COL)
    r'[:\s-]+.+',                                  # remainder
)


def compress_linter(output: str) -> str:
    """Group diagnostics by file; cap per-file; keep tail summary."""
    lines = output.splitlines()
    if len(lines) <= 15:
        return output

    # Keep the final 12 lines verbatim — they usually carry totals
    tail = lines[-12:]
    body = lines[:-12]

    by_file: dict[str, list[str]] = defaultdict(list)
    other: list[str] = []

    for line in body:
        stripped = line.strip()
        if not stripped:
            continue
        m = _RE_LINTER_DIAG.match(stripped)
        if m:
            by_file[m.group('path')].append(line)
        else:
            # Keep non-diagnostic lines that look like section headers or errors
            if re.search(r'\b(error|warning|hint|note)\b', stripped, re.I) \
               or stripped.startswith('>') or len(other) < 10:
                other.append(line)

    kept: list[str] = []
    if other:
        kept.extend(other[:10])

    for path, diags in sorted(by_file.items()):
        shown = diags[:LINTER_MAX_PER_FILE]
        kept.extend(shown)
        if len(diags) > LINTER_MAX_PER_FILE:
            kept.append(
                f'  … {len(diags) - LINTER_MAX_PER_FILE} more diagnostic(s) in {path}'
            )

    total_diags = sum(len(v) for v in by_file.values())
    kept.append(
        f'\n[promptpilot] {total_diags} diagnostic(s) across {len(by_file)} file(s)'
    )
    kept.extend(tail)
    return '\n'.join(kept)


# ---------------------------------------------------------------------------
# Installer / build-tool compressor (pip / npm / cargo / yarn / poetry)
# ---------------------------------------------------------------------------
_RE_INSTALL_NOISE = re.compile(
    # Progress bars, download percentage lines, "Collecting foo", "Downloading bar"
    r'^\s*('
    r'(Collecting|Downloading|Requirement already satisfied|Using cached'
    r'|Building wheel|Preparing metadata|Installing collected packages)\b'
    r'|\[[=\- ]+\]\s*\d+%'
    r'|[━│▓░]+\s*\d+%'
    r'|\d+/\d+ \[.*?\]'
    r'|added \d+ packages'
    r'|audited \d+ packages'
    r')',
    re.I,
)
_RE_INSTALL_SIGNAL = re.compile(
    r'\b(error|failed|warning|deprecat|vulnerab|conflict|ERR!|fatal)\b',
    re.I,
)


def compress_installer(output: str) -> str:
    """
    Keep:
      • Any line mentioning error/fail/warn/conflict/vuln
      • Last INSTALLER_MAX_LINES lines (final summary)
    Drop:
      • Download progress, "Collecting X", cached / already-satisfied notes
    """
    lines = output.splitlines()
    if len(lines) <= INSTALLER_MAX_LINES:
        return output

    tail = lines[-INSTALLER_MAX_LINES:]
    head_signals: list[str] = []
    dropped = 0

    for line in lines[:-INSTALLER_MAX_LINES]:
        if _RE_INSTALL_SIGNAL.search(line):
            head_signals.append(line)
        elif _RE_INSTALL_NOISE.match(line):
            dropped += 1
        elif line.strip():
            dropped += 1  # still drop non-signal lines above the tail

    kept: list[str] = []
    if head_signals:
        kept.append('[promptpilot] Signal lines from installer output:')
        kept.extend(head_signals)
        kept.append('')
    if dropped:
        kept.append(f'[promptpilot] … {dropped} progress/info line(s) dropped …')
    kept.extend(tail)
    return '\n'.join(kept)
