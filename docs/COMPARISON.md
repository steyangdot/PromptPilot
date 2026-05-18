# Comparison

PromptPilot is closest to an SLM-powered harness, not a pure token reducer or coding agent.

| Category | Main idea | PromptPilot difference |
|---|---|---|
| Token reducer | Make context smaller | PromptPilot uses an SLM to decide when compression is safe |
| Prompt optimizer | Rewrite prompts | PromptPilot can clarify, answer, pass through, or invoke the agent |
| Model router | Choose cheap or expensive model | PromptPilot routes workflow actions, not only model calls |
| Agent orchestrator | Run agents | PromptPilot controls the context and decisions around agent execution |
| Coding agent | Write/debug code | PromptPilot delegates coding to Codex/Claude-style agents |

## Positioning

PromptPilot's core claim is bounded: a small model can manage the workflow around a large coding model. The small model should not be judged as a replacement coder; it should be judged as a harness for routing, clarification, semantic preservation, and safe context control.
