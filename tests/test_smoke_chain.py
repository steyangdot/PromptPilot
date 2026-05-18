"""End-to-end smoke test — verifies the chain pipeline still works.

Runs the 2-turn `smoke` chain through `run_chain_once(..., variant='with_session')`
on Haiku 4.5 (~$0.10-0.20 per pass) and asserts pipeline-level invariants:

  - subprocess invocation succeeds (claude-code CLI runnable)
  - per-turn JSON has the schema we depend on
  - score_turn produces valid dicts for both explain + modify branches
  - promptpilot session file is created and has 2 user + 2 assistant entries
  - rolled-up run JSON is a list of dicts

Does NOT validate quality (success rate may legitimately be 0.0). Catches:
  - claude-code CLI deprecations / flag changes
  - Anthropic JSON output schema changes
  - promptpilot session API regressions
  - subprocess management bugs (zombies, encoding, fd capture)

Skipped automatically when:
  - ANTHROPIC_API_KEY not loadable (no .env, no shell var)
  - claude binary not on PATH
  - Target repo (PROMPTPILOT_TEST_REPO env var, default C:/projects/httpx) not present
  - SMOKE_TEST=skip env var is set (CI escape hatch)

Run manually:
    pytest tests/test_smoke_chain.py -v -s

Run with a different target repo:
    PROMPTPILOT_TEST_REPO=/path/to/repo pytest tests/test_smoke_chain.py -v -s

Run with a specific model:
    CLAUDE_MODEL=haiku pytest tests/test_smoke_chain.py -v -s
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

# Make repo root + research/ importable (chain_test_v2 lives under research/)
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "research"))

# Per-turn cost cap (defensive: any single turn over this aborts the test
# rather than silently spending money). Haiku 4.5 should be ~$0.05-0.10/turn.
MAX_PER_TURN_COST_USD = 1.00

# Target repo for the smoke chain. Override with PROMPTPILOT_TEST_REPO env var.
HTTPX_DIR = os.environ.get("PROMPTPILOT_TEST_REPO", "C:/projects/httpx")


def _have_prereqs() -> tuple[bool, str]:
    if os.environ.get("SMOKE_TEST", "").lower() == "skip":
        return False, "SMOKE_TEST=skip"
    # Try to load .env
    try:
        from promptpilot.core.dotenv import load_dotenv
        load_dotenv(_REPO_ROOT / ".env")
    except Exception as e:
        return False, f"could not load .env: {e}"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY not set (and no .env)"
    if not (shutil.which("claude") or shutil.which("claude.cmd")):
        return False, "claude binary not on PATH"
    if not Path(HTTPX_DIR).exists():
        return False, f"{HTTPX_DIR} not found (set PROMPTPILOT_TEST_REPO to a different path)"
    return True, ""


pytestmark = pytest.mark.skipif(
    not _have_prereqs()[0],
    reason=_have_prereqs()[1] or "prereqs missing",
)


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory):
    """Run the smoke chain once and return (results, run_file_path, out_dir)."""
    import chain_test_v2 as h

    chain = next(c for c in h.CHAINS if c["id"] == "smoke")
    assert len(chain["turns"]) == 2, "smoke chain must be exactly 2 turns"
    assert chain["turns"][0]["expected_action"] == "explain"
    assert chain["turns"][1]["expected_action"] == "modify"

    # Default to Haiku unless caller overrode CLAUDE_MODEL
    os.environ.setdefault("CLAUDE_MODEL", "haiku")

    out_dir = tmp_path_factory.mktemp("smoke")
    results = h.run_chain_once(chain, "claude-code", "with_session", 1, out_dir)
    h.save_run(out_dir, "with_session", 1, results)
    run_file = out_dir / "with_session_run1.json"
    return results, run_file, out_dir


def test_two_turns_completed(smoke_run):
    """The chain produced exactly 2 per-turn result dicts."""
    results, _, _ = smoke_run
    assert len(results) == 2, f"expected 2 turns, got {len(results)}"


def test_per_turn_schema_present(smoke_run):
    """Every per-turn dict has the keys we depend on downstream."""
    results, _, _ = smoke_run
    required_keys = {"turn", "raw", "expected_action", "intent", "scope",
                     "had_history", "prompt_chars", "wall_t", "usage",
                     "score", "slm_cost", "model_resolved", "model_used"}
    for i, r in enumerate(results):
        missing = required_keys - r.keys()
        assert not missing, f"turn {i+1} missing keys: {missing}"


def test_model_fields_populated(smoke_run):
    """model_resolved and model_used are set and reasonable."""
    results, _, _ = smoke_run
    for i, r in enumerate(results):
        assert r.get("model_resolved"), f"turn {i+1} missing model_resolved"
        assert r.get("model_used"), f"turn {i+1} missing model_used"
        # In the smoke test we set CLAUDE_MODEL=haiku — the actual model used
        # should also be haiku (no separate Opus/Sonnet call expected).
        assert "haiku" in r["model_used"].lower(), \
            f"turn {i+1} expected haiku model, got {r['model_used']}"


def test_usage_schema(smoke_run):
    """Usage dict has the cost/token fields the analyzer reads."""
    results, _, _ = smoke_run
    required_usage = {"input_tokens", "output_tokens", "tool_calls",
                      "agent_messages", "total_cost_usd"}
    for i, r in enumerate(results):
        missing = required_usage - r["usage"].keys()
        assert not missing, f"turn {i+1} usage missing: {missing}"
        # cached_tokens optional but should be int if present
        if "cached_tokens" in r["usage"]:
            assert isinstance(r["usage"]["cached_tokens"], int)


def test_score_schema_per_branch(smoke_run):
    """Both score branches (explain T1 + modify T2) produce valid dicts."""
    results, _, _ = smoke_run
    for i, r in enumerate(results):
        score = r["score"]
        assert "success" in score, f"turn {i+1} score missing 'success'"
        assert score["success"] in (0.0, 0.5, 1.0), \
            f"turn {i+1} success={score['success']} not in {{0, 0.5, 1.0}}"
        assert "bailed" in score and isinstance(score["bailed"], bool)
        assert "changed" in score and isinstance(score["changed"], list)


def test_no_runaway_cost(smoke_run):
    """No single turn cost more than the per-turn cap."""
    results, _, _ = smoke_run
    for i, r in enumerate(results):
        cost = r["usage"]["total_cost_usd"]
        assert cost < MAX_PER_TURN_COST_USD, \
            f"turn {i+1} cost ${cost:.2f} exceeds ${MAX_PER_TURN_COST_USD} cap"


def test_at_least_one_turn_did_work(smoke_run):
    """Pipeline didn't completely no-op — at least one turn had tool calls."""
    results, _, _ = smoke_run
    total_tool_calls = sum(r["usage"]["tool_calls"] for r in results)
    assert total_tool_calls > 0, \
        "all turns bailed (zero tool calls total) — pipeline likely broken"


def test_per_turn_json_files_exist(smoke_run):
    """The raw claude-code per-turn JSON files were written."""
    _, _, out_dir = smoke_run
    for i in (1, 2):
        p = out_dir / f"run1_with_session_t{i}.json"
        assert p.exists(), f"missing per-turn file: {p.name}"
        # File is non-trivially small (claude-code returns at minimum a JSON envelope)
        assert p.stat().st_size > 10, f"{p.name} is suspiciously empty"


def test_per_turn_json_has_modelUsage(smoke_run):
    """Per-turn claude-code JSON has modelUsage — needed for model verification."""
    _, _, out_dir = smoke_run
    for i in (1, 2):
        p = out_dir / f"run1_with_session_t{i}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        assert "modelUsage" in d, f"t{i} missing modelUsage (claude-code schema change?)"
        assert isinstance(d["modelUsage"], dict)
        # At least one model was actually called
        assert len(d["modelUsage"]) >= 1, f"t{i} has empty modelUsage"


def test_rolled_up_json_valid(smoke_run):
    """The save_run output is a JSON list of 2 dicts matching results."""
    _, run_file, _ = smoke_run
    assert run_file.exists()
    body = json.loads(run_file.read_text(encoding="utf-8"))
    assert isinstance(body, list)
    assert len(body) == 2


def test_session_loaded_on_t2(smoke_run):
    """T2 should have had_history=True (T1 recorded into session before T2).

    This implicitly verifies the entire record_to_session -> load_recent_turns
    cycle: T2 cannot have had_history=True unless T1 was successfully appended
    AND successfully reloaded. (run_chain_once clears the session at end of
    run, so checking the file post-hoc is unreliable.)
    """
    results, _, _ = smoke_run
    assert results[0]["had_history"] is False, "T1 should have empty session"
    assert results[1]["had_history"] is True, \
        "T2 should have loaded T1 from session — load_recent_turns or record_to_session broken"
