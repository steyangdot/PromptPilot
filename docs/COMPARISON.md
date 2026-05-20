# Comparison

PromptPilot is closest to an SLM-powered harness, not a pure token reducer or coding agent.

| Category | Main idea | PromptPilot difference |
|---|---|---|
| Token reducer | Make context smaller | PromptPilot uses an SLM to decide when compression is safe |
| Prompt optimizer | Rewrite prompts | PromptPilot can clarify, answer, pass through, or invoke the agent |
| Model router | Choose cheap or expensive model | PromptPilot routes workflow actions, not only model calls |
| Agent orchestrator | Run agents | PromptPilot controls the context and decisions around agent execution |
| Coding agent | Write/debug code | PromptPilot delegates coding to Codex/Claude-style agents |
| Third-party Claude harnesses (OpenClaw, OpenCode) | Extract Claude OAuth tokens and make direct API calls | PromptPilot does not handle the token at all — see [Compliance posture](#compliance-posture-vs-openclaw--opencode) below |

## Positioning

PromptPilot's core claim is bounded: a small model can manage the workflow around a large coding model. The small model should not be judged as a replacement coder; it should be judged as a harness for routing, clarification, semantic preservation, and safe context control.

## Compliance posture vs OpenClaw / OpenCode

The April 2026 enforcement action against **OpenClaw** and **OpenCode** targeted
tools that read OAuth credentials out of `~/.claude/.credentials.json` and made
direct calls to `api.anthropic.com` while spoofing Claude Code's request shape.
That violates Anthropic's **Feb 20, 2026** clarification: subscription OAuth
credentials are scoped to "ordinary use of Claude Code and other native
Anthropic applications," and the literal prohibition is on using those tokens
**in any other product, tool, or service**.

PromptPilot is structurally different. When you run `prpt` against the Max
subscription, PromptPilot invokes the official `claude` binary as a child
process and sends it a rewritten prompt. The credential never enters our
process; from Anthropic's server logs the request originates from the real
`claude` binary with its real user-agent and request shape, because that's
what made the call.

| Axis | OpenClaw / OpenCode | PromptPilot |
|---|---|---|
| **OAuth token handling** | Read `~/.claude/.credentials.json`; sent the bearer directly to the API | Never reads, stores, or transmits the token; lives inside the official `claude` binary process |
| **API endpoint** | Direct HTTPS to `api.anthropic.com/v1/messages` | No direct Anthropic API calls from our process; the official binary makes them |
| **Request shape** | Spoofed Claude Code's headers, system prompt, and tool definitions | The real `claude` binary builds the request with its real shape |
| **Client identity** | Client *replacement* — pretended to be Claude Code | A local user invoking their own already-logged-in CLI from a parent process |
| **Fan-out** | Shared service letting many users plug accounts in | Local process, one user, one machine |
| **Orchestration vs replacement** | Replaced Claude Code with a third-party client | Drives the official Claude Code with a rewritten prompt |

The literal token-clause prohibition does not apply to PromptPilot's
subprocess pattern. What remains interpretive is the broader **"ordinary use"**
phrasing in the Feb 20 statement — whether driving `claude -p` programmatically
counts as "ordinary use" depends on Anthropic's interpretation, and the answer
is probably "yes for modest interactive use, ambiguous for high-volume
automation." For sustained automation (multi-hundred-call experiments, CI
loops, etc.) the conservative path is `ANTHROPIC_API_KEY` with the SDK
normalizer, which is documented as a first-class option in
[Authentication and Providers](https://github.com/steyangdot/PromptPilot/wiki/Authentication-and-Providers).

PromptPilot prints a once-per-process informational note when the subscription
path is used, so users can make their own call about which authentication mode
fits their workload. The same reasoning applies to ChatGPT/Codex subscription
routing via `codex`; OpenAI has not published a Feb-2026-style clarification,
but the structural pattern (subprocess of the official binary, no token
handling) is the same.

A **separate monthly credit pool** for the Agent SDK and `claude -p` is
expected **June 15, 2026** — that launch may formalize programmatic
subscription use under a sanctioned billing model.

## When PromptPilot fits

- You already use Codex or Claude-style coding agents.
- You want fewer wasted frontier-model calls on ambiguous or simple requests.
- You want noisy tool output compressed without losing debugging facts.
- You care about preserving explicit constraints more than maximizing token reduction.

## When it may not fit

- You want a standalone coding agent.
- You want the small model to make implementation decisions.
- You need fully automatic context deletion with no passthrough fallback.

---

**See also:** [Project Overview](https://github.com/steyangdot/PromptPilot/wiki/Project-Overview) · [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness) · [FAQ](https://github.com/steyangdot/PromptPilot/wiki/FAQ)
