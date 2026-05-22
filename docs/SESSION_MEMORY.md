# Session Memory (`withSession`)

**Cross-invocation memory that's an order of magnitude cheaper than your coding
tool's native session — at equal task quality — because it carries one-line
intent summaries instead of re-feeding the whole transcript every turn.**

Each `prpt` call is a separate process, so by default a coding agent has no idea
what the previous call did. Session memory fixes that: PromptPilot persists a
small, bounded record of recent turns and prepends it to the next SLM rewrite, so
follow-ups like *"add a test for that fix"* or *"apply the same change to the async
client"* resolve correctly without you re-explaining. It's on by default; clear it
with `prpt new-session`.

## How it differs from native session

Your coding tool already has a conversation mode — `claude --resume`,
`codex exec resume`. The difference is **what gets carried forward**:

| | Native session (`--resume` / `exec resume`) | PromptPilot `withSession` |
|---|---|---|
| Carries | the **entire transcript** (every prior message, tool call, file read, test output) | a **bounded window** (last 4 turns) of one-line `memory_record` summaries |
| Per-turn cost | **grows every turn** as the transcript accumulates | **flat** — fixed window, never balloons |
| Reference resolution | yes | yes (the SLM rewrite resolves "that fix" → a self-contained instruction) |
| Recovery of dropped detail | full-fidelity replay | repo state backstops it (the agent re-reads the actual files) |

## The evidence

All numbers from the in-repo chain harness
([research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py))
against a real target repo (`httpx`). Read the caveats — these are single-workload
results, and the honest story is **tool-dependent**.

### 1. Bounded vs unbounded — the robust finding

On codex chain1 (N=5), native `codex exec resume` re-feeds the growing transcript
every turn. PromptPilot's bounded session stays flat:

| Turn | Native session input | Native tool calls | PromptPilot input |
|---|---:|---:|---:|
| T1 | 465k | 41 | ~505k |
| T2 | 1.60M | 9 | ~214k |
| T3 | 1.89M | 6 | ~225k |
| T4 | 2.10M | 3 | ~85k |
| T5 | **2.36M** | **2** | ~79k |

Native session input **grows 5× across 5 turns** (465k → 2.36M) while its useful
work **collapses** (41 → 2 tool calls — by late turns the agent mostly re-ingests
its own bloated transcript). PromptPilot stays bounded (~44k/turn average). This is
the durable, mechanism-level result; the cost ratios below follow from it.

### 2. Cost — product comparison

| Comparison | Result |
|---|---|
| Full PromptPilot vs raw-prompt + native session (**codex**, N=5) | **~8.5× cheaper per success** ($0.74 vs $6.31), equal quality (1.70 vs 1.50) |
| Full PromptPilot vs raw-prompt + native session (**claude-code**, prior) | **~3× cheaper per success**, 6.1× fewer input tokens, equal quality |

> **Honest caveat:** these compare *full PromptPilot* (SLM rewrite **+** bounded
> session) against a *raw-prompt + native-session* baseline — so the ratio bundles
> the rewrite's exploration savings with the session-mechanism savings. It's a valid
> "use PromptPilot vs use the tool raw" comparison, **not** an isolated session-only
> number. The transcript-growth curve (§1) is the clean session-mechanism evidence.

### 3. Quality — tool-dependent (the nuance that matters)

Session memory does **not** behave the same on every coding tool:

| Tool | Session's effect on success | Why |
|---|---|---|
| **claude-code** | **+60% success** (−28.7% cost-per-success), N=5 chain1, clean isolation | Resolves references for a model that otherwise cold-explores and bails |
| **codex** | **success tied** (cost optimization: −20% input tokens) | codex already resolves references well natively, so session mainly saves cost |

Don't market a single "+60%" number — it's claude-code-specific. The universal
claim is: *session memory is at worst a cost optimization and at best a large
success lift, depending on how well your downstream agent resolves references on
its own.*

## When it helps most

- **Referential coding chains** — "fix X" → "add a test for that" → "apply it to the
  async path." Every follow-up after the first benefits.
- **claude-code / Claude Code** — biggest success lift.
- **Long sessions** — where a native transcript would balloon; PromptPilot stays flat.

## When it's marginal

- **Single-shot, self-contained prompts** — nothing to reference.
- **codex** — still a cost win, but no success lift.
- For workloads with many self-contained turns, `--gate-session` adds a cheap
  classifier that skips the session load when a prompt doesn't reference prior turns
  (see [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions)).

## Caveats

- **Single workload** (`httpx`, chain1/chain4/chain5). Your repo will land elsewhere.
- **N=5 noise floor** — success deltas under ~0.2/turn are inside the noise; the
  *cost* gaps are the robust signal, the success deltas are directional.
- The codex native-session comparison is **cross-session** (~24h apart) and
  **confounded** (raw vs SLM-rewritten prompts) — see §2 caveat. A clean
  session-only isolation (STACKED vs WITH) is an open follow-up.
- "Success" is judged by the harness scorer; chain1's T3 is a known artifact that
  suppresses success on all arms equally.

---

**See also:** [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks) · [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions) · [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness) · [Comparison](https://github.com/steyangdot/PromptPilot/wiki/Comparison)
