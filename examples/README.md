# Examples

## Runnable demo — [`demo.py`](demo.py)

See PromptPilot's SLM control layer reshape a raw prompt, with **zero setup**:
no API key, no coding-agent install, no network.

```bash
python examples/demo.py            # offline heuristic — runs anywhere
python examples/demo.py --slm      # live small-model rewrite (needs an API key or subscription)
python examples/demo.py --offline  # force the offline path (never touches the network)
python examples/demo.py --only 2   # run just one of the examples
python examples/demo.py --full     # print the full structured prompt for every example
python examples/demo.py --ascii    # ASCII-only box glyphs for limited terminals
```

By default the demo uses the rule-based heuristic normalizer so anyone (and CI)
can run it. If an SLM backend is detected (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
in the environment) it auto-upgrades to the real small-model rewrite; `--slm`
forces it (including for Max / ChatGPT subscriptions). Every field shown —
route, task type, confidence, protected spans, hard constraints, ambiguities —
is computed by PromptPilot's real pipeline against a fixed synthetic repo, so
the output is deterministic and reproducible wherever you run it.

The three built-in examples walk from a precise request (constraints pinned) to
an under-specified one (ambiguities flagged) to a mixed refactor (hard vs soft
constraints separated).

## Worked-example categories

These directories describe the behaviors the demo illustrates, with guidance for
building your own fixtures:

- [`passthrough/`](passthrough/) — inputs where rewriting is risky, so the raw
  prompt is forwarded unchanged.
- [`semantic-preservation/`](semantic-preservation/) — prompts where explicit
  constraints, file paths, and symbols must survive any rewrite.
- [`tool-output-compression/`](tool-output-compression/) — noisy tool output
  (pytest / grep / diff) compressed before the coding agent sees it.

## Related

- [QUICKSTART.md](../QUICKSTART.md) — five-minute onboarding for the `prpt` CLI.
- [docs/RECORDING.md](../docs/RECORDING.md) — how the README demo asset is produced.
