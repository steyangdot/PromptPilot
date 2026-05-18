"""Tests for the stats module and --compare CLI flag."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from promptpilot.stats import load_runs, print_stats
from promptpilot.cli import main


@pytest.fixture
def log_file(tmp_path):
    path = str(tmp_path / "test_runs.jsonl")
    runs = [
        {
            "timestamp_utc": "2026-04-01T10:00:00+00:00",
            "mode": "wrapped_dry_run",
            "tool": "echo",
            "normalizer": "slm",
            "cwd": "/tmp/test",
            "raw_prompt": "fix the bug",
            "final_prompt": "Fix the timeout bug in OrderSyncWorker",
            "exit_code": 0,
            "token_stats": {
                "original_tokens": 200,
                "final_tokens": 150,
                "delta_tokens": 50,
                "haiku_input_tokens": 300,
                "haiku_output_tokens": 80,
                "haiku_cost_usd": 0.000560,
                "target_model": "claude-opus-4-6",
                "gross_savings_usd": 0.000750,
                "net_savings_usd": 0.000190,
                "actual_input_tokens": None,
                "actual_output_tokens": None,
                "actual_total_cost_usd": None,
            },
        },
        {
            "timestamp_utc": "2026-04-02T14:30:00+00:00",
            "mode": "wrapped",
            "tool": "codex",
            "normalizer": "slm",
            "cwd": "/tmp/test",
            "raw_prompt": "add dark mode",
            "final_prompt": "Add dark mode toggle to settings",
            "exit_code": 0,
            "token_stats": {
                "original_tokens": 100,
                "final_tokens": 80,
                "delta_tokens": 20,
                "haiku_input_tokens": 250,
                "haiku_output_tokens": 60,
                "haiku_cost_usd": 0.000440,
                "target_model": "claude-opus-4-6",
                "gross_savings_usd": 0.000300,
                "net_savings_usd": -0.000140,
                "actual_input_tokens": None,
                "actual_output_tokens": None,
                "actual_total_cost_usd": None,
            },
        },
        {
            "timestamp_utc": "2026-04-03T09:00:00+00:00",
            "mode": "pass_through",
            "tool": "echo",
            "normalizer": "heuristic",
            "cwd": "/tmp/test",
            "raw_prompt": "hello",
            "final_prompt": "hello",
            "exit_code": 0,
        },
    ]
    with open(path, "w") as f:
        for run in runs:
            f.write(json.dumps(run) + "\n")
    return path


class TestLoadRuns:
    def test_loads_all_runs(self, log_file):
        runs = load_runs(log_file)
        assert len(runs) == 3

    def test_empty_file(self, tmp_path):
        path = str(tmp_path / "empty.jsonl")
        open(path, "w").close()
        assert load_runs(path) == []

    def test_missing_file(self):
        assert load_runs("/nonexistent/path.jsonl") == []


class TestPrintStats:
    def test_prints_stats(self, log_file, capsys):
        print_stats(log_file)
        output = capsys.readouterr().out
        assert "PROMPTPILOT STATS" in output
        assert "Runs logged:" in output
        assert "Runs with token data:" in output
        assert "slm" in output
        assert "codex" in output

    def test_last_n(self, log_file, capsys):
        print_stats(log_file, last_n=1)
        output = capsys.readouterr().out
        assert "last 1 runs" in output

    def test_no_runs(self, tmp_path, capsys):
        path = str(tmp_path / "empty.jsonl")
        open(path, "w").close()
        print_stats(path)
        output = capsys.readouterr().out
        assert "No runs logged" in output

    def test_prints_stats_dark_theme(self, log_file, capsys):
        print_stats(log_file, theme="dark")
        output = capsys.readouterr().out
        assert "\x1b[" in output


class TestCompareMode:
    def test_compare_heuristic(self, capsys):
        exit_code = main([
            "--compare", "--normalizer", "heuristic",
            "fix the timeout bug in the payment service backend",
        ])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "COMPARE" in output
        assert "RAW" in output
        assert "OPTIMIZED" in output
        assert "DELTA" in output

    def test_compare_shows_prompts(self, capsys):
        exit_code = main([
            "--compare", "--normalizer", "heuristic",
            "refactor auth module, must keep backward compatibility",
        ])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "RAW PROMPT" in output
        assert "FINAL DOWNSTREAM PROMPT" in output


class TestStatsSubcommand:
    def test_stats_no_log(self, tmp_path, capsys):
        log_path = str(tmp_path / "nope.jsonl")
        exit_code = main(["stats", "--log-file", log_path])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "No runs logged" in output

    def test_stats_with_data(self, log_file, capsys):
        exit_code = main(["stats", "--log-file", log_file])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "PROMPTPILOT STATS" in output

    def test_stats_with_dark_theme(self, log_file, capsys):
        exit_code = main(["stats", "--theme", "dark", "--log-file", log_file])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "\x1b[" in output
        assert "Runs logged:" in output
