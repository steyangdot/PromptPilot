# Session Memory (`withSession`)

**Cross-invocation memory that feeds the coding agent far fewer tokens than its
native session — on Codex, ~4× fewer total tokens at equal task quality — by
carrying one-line intent summaries instead of re-feeding the whole transcript every
turn.** (On Claude Code, native `--resume` already caches history cheaply, so the win
there is the rewrite, not the bounding — it's tool-dependent; see §1b.)

Each `prpt` call is a separate process, so by default a coding agent has no idea
what the previous call did. Session memory fixes that: PromptPilot persists a
small, bounded record of recent turns and prepends it to the next SLM rewrite, so
follow-ups like *"add a test for that fix"* or *"apply the same change to the async
client"* resolve correctly without you re-explaining. It's on by default; clear it
with `prpt new-session`.

## How it differs from native session

Your coding tool already has a conversation mode — `claude --resume`,
`codex resume` (the benchmark below drives the headless `codex exec resume`
form — same session mechanism). The difference is **what gets carried forward**:

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

### 1b. The tool-aware rule — bound on codex, don't bound on claude

The bounded-vs-native verdict **flips by tool**, and it flips for a concrete
mechanical reason: the two native sessions cache history differently. Holding the
SLM constant (clean session-mechanism isolation, `slm_native` vs `with_session`,
uncached input tokens per run, chain_auth N=5):

| Tool | bounded vs native | Verdict | Why |
|---|---|---|---|
| **codex** | bounded **1.87× cheaper** | **bound the session** | codex native re-feeds the transcript **uncached** — it grows to ~100k/turn by turn 5 |
| **claude** | bounded **1.49× costlier** (0.67×) | **use native `--resume`** | claude native caches history — uncached collapses to ~1.5k/turn by turn 5, so history is near-free |

Same code, same task, same SLM — a **~2.8× swing** with the opposite conclusion.
The driver is the native-cache profile: on claude the prior transcript is read from
cache and barely shows up in uncached tokens, so PromptPilot's bounded window is
*extra* cost on top of an already-cheap native session. On codex there is no such
cache, so every turn re-pays for the whole growing transcript and the bounded
window is a large saving.

> **Rule of thumb:** **bound the session on codex; use native resume (rewrite-only)
> on claude.** PromptPilot picks the right mode per tool by default.

### 1c. It's the SLM distillation, not just the bounding

Would a *dumb* mechanical flatten — raw prompt plus a raw window of recent turns,
no SLM — save just as much? No. A controlled A/B (codex `chain_auth`, N=5,
interleaved) pitted PromptPilot's SLM-distilled session against exactly that
mechanical bounded session: the mechanical version fed **1.27× more uncached /
1.37× more total** tokens for the **same** end-state (5/5 both). Bounding the
session gets you part of the way; the SLM's one-line `memory_record` — materially
leaner than re-feeding raw turns — supplies the rest. Distilling each turn is itself
SLM work; the frontier agent never summarizes itself.

### 2. Cost — product comparison

| Comparison (chain_auth, N=5, total + uncached input tokens) | Result |
|---|---|
| Full PromptPilot vs raw-prompt + native session (**codex**) | **~4.2× fewer total tokens** (cache-independent) · **~1.97× fewer uncached** (observed cache, warmth-sensitive — see [Measurement Methodology](MEASUREMENT_METHODOLOGY.md)); end-state parity |
| Full PromptPilot vs raw-prompt + native session (**claude**) | bounded session **loses** (1.19× costlier); the win is **rewrite-only** (slm_native): **1.25× fewer**, end-state parity |

> **Honest caveat:** the codex product ratio bundles the SLM rewrite's savings with
> the bounded-session savings — a valid "use PromptPilot vs use the tool raw"
> comparison, **not** an isolated session-only number (the clean session-mechanism
> isolation is §1b's `slm_native` vs `with_session`). On claude, bounding the session
> is *counterproductive*; use rewrite-only there. **Lead with total tokens:** the
> *uncached* figure rides the provider's cache, which is non-deterministic and varies
> run-to-run, so it's a warmth-sensitive range, not a fixed point — see
> [Measurement Methodology](MEASUREMENT_METHODOLOGY.md) and the full journey in
> [Testing Strategy](TESTING_STRATEGY.md). The headline figures above come from a
> clean v2 run (N=5, interleaved, 0 censored, end-state 5/5 both arms): **~4.2×
> fewer total tokens / ~1.97× fewer uncached** at parity. These supersede earlier
> per-success-$ figures (a prior
> "8.5× / $0.74-vs-$6.31") on a confounded, cache-inclusive basis.

### 3. Cost is tool-dependent; quality is parity

The bounded session's value flips by tool — and in the clean chain_auth
replication, **quality (end-state) is parity across configs**:

| Tool | Bounded session vs native | End-state |
|---|---|---|
| **claude** | **costs more** — 1.49× vs native `--resume` (1.19× vs vanilla); use native resume | parity |
| **codex** | **~4.2× fewer total tokens** · **~1.97× fewer uncached** (observed cache) — bound it | parity |

The earlier "+60% success on claude-code" figure does **not** reproduce in the
clean chain_auth replication (end-state is parity across configs); it was a
confounded / cached-read artifact. The durable, universal claim is tool-dependent:
**bound the session on codex (large cost win), use native resume on claude** —
both at equal quality.

## When it helps most

- **Referential coding chains** — "fix X" → "add a test for that" → "apply it to the
  async path." Every follow-up after the first benefits.
- **claude-code / Claude Code** — biggest success lift.
- **Long sessions** — where a native transcript would balloon; PromptPilot stays flat.

## When it's marginal

- **Single-shot, self-contained prompts** — nothing to reference.
- **codex** — still a cost win, but no success lift.
- **claude with bounded session** — don't bound it. On claude the native cache
  already makes history near-free, so a bounded window is *1.49× costlier* than
  plain native `--resume`; take the SLM rewrite and let native resume carry the
  history (see §1b).
- For workloads with many self-contained turns, `--gate-session` adds a cheap
  classifier that skips the session load when a prompt doesn't reference prior turns
  (see [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions)).

## Caveats

- **Single workload** (`httpx`; chain1/chain4/chain5 and the cleaner `chain_auth`).
  Your repo will land elsewhere.
- **N=5 noise floor** — success deltas under ~0.2/turn are inside the noise; the
  *cost* gaps are the robust signal.
- **Uncached is not reproducible.** The full-price (uncached) figure depends on the
  provider's server-side cache, which is best-effort and varies run-to-run — so we
  report it as a warmth-sensitive range and lead with **total** tokens
  (cache-independent). See [Measurement Methodology](MEASUREMENT_METHODOLOGY.md).
- **The clean isolation is done.** The earlier caveat (cross-session, raw-vs-rewritten
  confound, "open follow-up") is resolved by `chain_auth`: same-session,
  **interleaved**, with `slm_native` vs `with_session` giving the clean
  session-mechanism number (§1b) and a co-run `builtin` vs `with_session` giving the
  product number (§2), all at end-state parity.
- **Quality is scored on end-state.** The clean `chain_auth` replication checks
  passing `tests/test_auth.py`, sidestepping chain1's T3 per-turn-scorer artifact.

---

**See also:** [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks) · [Testing Strategy](https://github.com/steyangdot/PromptPilot/wiki/Testing-Strategy) · [Measurement Methodology](https://github.com/steyangdot/PromptPilot/wiki/Measurement-Methodology) · [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions) · [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness) · [Comparison](https://github.com/steyangdot/PromptPilot/wiki/Comparison)
