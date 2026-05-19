# Contributing to PromptPilot

Thanks for your interest. PromptPilot is a small project; contributions of all sizes are welcome.

## Quick start

1. Clone and install in editable mode:
   ```
   python quickstart.py     # full bootstrap: editable install + checks
   ```
   This auto-detects which coding CLI (`claude` / `codex`) you have and installs the matching extras. Once `prpt` is on your PATH you can re-run the same checks with `prpt doctor` or repair a broken install with `prpt setup`.

2. Run the tests:
   ```
   pytest tests/ -v
   ```
   The unit tests (`test_loader`, `test_slm`, `test_compress`, `test_heuristic`, `test_stats`, `test_cli`, `test_adapters`) run offline.

3. The end-to-end smoke test (`tests/test_smoke_chain.py`) requires a real coding agent CLI on PATH, an API key, and a target repo. It's skipped automatically when prereqs are missing — see the file's docstring.

## What we want

- **Bug fixes**: especially around the SLM normalizers, context loading, or output capture.
- **New SLM normalizer backends**: see [prpt/normalizers/](prpt/normalizers/) for the existing implementations.
- **Compression heuristics**: [prpt/compress/tool_output.py](prpt/compress/tool_output.py) is command-type-aware; new command families (e.g. `cargo`, `bazel`, custom CI tools) are welcome.
- **Adapters**: new coding agent CLIs can be supported by adding to [prpt/adapters/](prpt/adapters/).
- **Documentation improvements**: especially around real-world auth setups.

## Code style

- Python 3.9+ compatible. Type hints encouraged but not enforced.
- Prefer small, focused changes. One feature/fix per PR.
- New behavior should come with at least one test.
- Don't break the public CLI surface (`prpt --help` flags) without discussion.

## Research scripts

The `research/` directory holds chain experiments, benchmarking harnesses, and one-off analysis scripts used during development. These are not part of the OSS package and may move, change, or break without notice. PRs touching `research/` are welcome but lower priority.

## Reporting issues

Open a GitHub issue with:
- What you ran (exact command line)
- What you expected
- What happened instead
- Relevant environment: Python version, OS, which coding CLI, auth mode

For security-sensitive issues, see [SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
