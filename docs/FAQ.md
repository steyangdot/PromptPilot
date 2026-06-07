# FAQ

## What is PromptPilot?

PromptPilot is an SLM-powered control plane for AI coding agents. It uses a small model as the harness layer around Codex/Claude-style agents to route prompts, detect ambiguity, preserve constraints, compress noisy context, and decide when to pass through unchanged.

## Isn't the SLM weaker than the coding model?

Yes. That is why PromptPilot does not use the SLM as the coding model.

The SLM is used as a bounded harness for workflow decisions: routing, clarification, safe rewriting, constraint extraction, low-risk compression, and passthrough decisions.

The expensive coding agent still does the hard work: reasoning about code, editing files, debugging failures, and fixing tests.

If the SLM is uncertain or transformation risk is high, the correct behavior is passthrough. A slightly more expensive run is better than a cheap but wrong run.

## Is PromptPilot just a token reducer?

No. Token savings are useful, but they are not the goal by themselves. PromptPilot optimizes for semantic-preserving context control: preserve critical facts, compress low-value noise, ask for clarification when intent is ambiguous, and pass through unchanged when transformation risk is high.

## Does PromptPilot replace Codex or Claude Code?

No. PromptPilot sits before Codex/Claude-style agents. The frontier coding agent still performs implementation, debugging, test repair, and deep code reasoning.

## Which normalizer should I use?

Use the default. `prpt --slm` auto-detects a **v2 normalizer** matching your auth — `slm-anthropic-v2` (Anthropic key), `slm-openai-v2` (OpenAI key), or `slm-subscription-v2` (Max / ChatGPT). The v2 normalizers carry the routing decision, including `clarify` and `passthrough`. The legacy v1 prose normalizers (`slm-anthropic` / `slm-openai` / `slm-subscription`) remain available via `--normalizer` for pre-v2 behavior.

## How do I see what route PromptPilot chose, or the JSON spec?

Run `prpt --show-spec "..."` to print the parsed `ExecutionSpec` (route, target files, risk, memory record), or `prpt preview` for an interactive playground that shows the spec + rewrite per prompt. For the raw model output, set `PROMPTPILOT_V2_RAW_LOG=1` (logs to `~/.promptpilot/v2_slm_raw.jsonl`).

## How do I preview a prompt without running the coding agent?

`prpt preview` (interactive) or `prpt preview "your prompt"` (one-shot) shows the routing spec and the rewrite — or the clarifying question — without forwarding anything downstream.

## What should I read first?

If you want to try the tool, start with [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart).

If you want to evaluate the idea, read [Project Overview](https://github.com/steyangdot/PromptPilot/wiki/Project-Overview), then [Architecture](https://github.com/steyangdot/PromptPilot/wiki/Architecture), then [Comparison](https://github.com/steyangdot/PromptPilot/wiki/Comparison).

If you want to understand the safety boundary, read [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation) and [Safety Model](https://github.com/steyangdot/PromptPilot/wiki/Safety-Model).

## How do I update the wiki?

Edit the source markdown in this repository, then run `scripts/publish_wiki.sh`. See [Wiki Publishing](https://github.com/steyangdot/PromptPilot/wiki/Wiki-Publishing).
