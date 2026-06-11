"""CI bridge for the research/ self-tests.

The harness self-tests under research/ are underscore-prefixed so pytest does not
auto-collect them (they build synthetic transcripts / temp git repos on disk). That
means `pytest tests/` — the only thing CI runs — never exercised them, so the
end-state scorer's regression guards (PR #33/#34) and the usage-parser / captured-
end-state tests were effectively un-enforced. This bridge runs each as a subprocess
and asserts exit 0, so they ride the existing CI.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "research"
SELF_TESTS = [
    "_test_usage_parser.py",
    "_test_score_endstate.py",
    "_test_endstate_capture.py",
]


@pytest.mark.parametrize("name", SELF_TESTS)
def test_research_selftest(name):
    path = RESEARCH / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    proc = subprocess.run([sys.executable, str(path)], cwd=str(ROOT),
                          capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"{name} exited {proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
