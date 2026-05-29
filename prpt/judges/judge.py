"""Judge abstraction: generic SLM-as-controller call across providers.

Every concrete judge takes a prompt and returns ``(text, cost_usd, walltime_s)``.
Failure-tolerant: timeouts, missing SDKs, auth failures all return
``("", 0.0, walltime)`` so callers can detect empty output and decide.

Four implementations:
- ``MaxHaikuJudge`` — Claude CLI subprocess, Max OAuth (no API key needed).
  Lightest subscription option for users with a Max plan.
- ``CodexCliJudge`` — Codex CLI subprocess, ChatGPT subscription (no API key).
  For users in the OpenAI ecosystem with `codex login`. Heavier per-call than
  MaxHaikuJudge because codex spins up its agent loop even for one-shot
  prompts, but bills shadow $ against the subscription quota.
- ``AnthropicApiJudge`` — anthropic SDK + ``ANTHROPIC_API_KEY``, Haiku model.
- ``OpenAiJudge`` — openai SDK + ``OPENAI_API_KEY``, gpt-5.4-nano (current default).

Selection:
- ``PROMPTPILOT_JUDGE=max|codex|anthropic|openai`` env var if you want to force one.
- ``get_default_judge()`` auto-picks: subscriptions first
  (max > codex), then API keys (anthropic > openai).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Protocol

# Reuse the existing Max-Haiku subprocess implementation rather than
# duplicate it. judge_via_max already strips ANTHROPIC_API_KEY from the
# subprocess env, disables tools, and parses the JSON response.
from prpt.judges.slm import judge_via_max as _judge_via_max


# ---------------------------------------------------------------------------
# Protocol + concrete judges
# ---------------------------------------------------------------------------

class Judge(Protocol):
    """Single-prompt SLM call. Returns (text, cost_usd, walltime_s)."""
    name: str

    def __call__(self, prompt: str, timeout: int = 90) -> tuple[str, float, float]:
        ...


# Module-level flag so the subscription-routing warning fires once per
# process, not per call. Both MaxHaikuJudge and SubscriptionSLMNormalizer
# import this via warn_subscription_tos_once() below.
_subscription_warned = False


def warn_subscription_tos_once() -> None:
    """Emit an informational note about subscription routing, once per process.

    Calibrated to match actual risk: promptpilot invokes the official `claude`
    binary (no token handling, no API spoofing), so this is materially
    different from the OpenClaw/OpenCode pattern enforced Apr 4 2026. The
    note exists because the broader "ordinary use" framing in Anthropic's
    Feb 20 2026 ToS leaves programmatic CLI invocation in an interpretive
    gray zone that depends on how Anthropic reads it. Informational, not
    a "you are doing something wrong" warning.
    """
    global _subscription_warned
    if _subscription_warned:
        return
    _subscription_warned = True
    import sys
    print(
        "[promptpilot] note: SLM routed via Claude subscription "
        "(we invoke the official `claude` CLI, no token handling; "
        "different pattern from the Apr 2026 enforcement targets, "
        "but `claude -p` programmatic use under Anthropic's Feb 2026 "
        "'ordinary use' framing is interpretive -- set ANTHROPIC_API_KEY "
        "to opt into the SDK path).",
        file=sys.stderr,
    )


_codex_subscription_warned = False


def warn_codex_subscription_tos_once() -> None:
    """Same posture as warn_subscription_tos_once, codex-specific text.

    OpenAI hasn't published a Feb-2026-style restriction; the warning is
    purely informational so users know which auth path is being used.
    """
    global _codex_subscription_warned
    if _codex_subscription_warned:
        return
    _codex_subscription_warned = True
    import sys
    print(
        "[promptpilot] note: SLM routed via ChatGPT subscription "
        "(we invoke the official `codex` CLI, no token handling; "
        "no published OpenAI restriction on programmatic use, but each "
        "call burns ~20k input tokens of subscription quota due to codex "
        "agent-loop overhead -- set OPENAI_API_KEY to opt into the SDK path).",
        file=sys.stderr,
    )


class MaxHaikuJudge:
    """Haiku via Claude CLI subprocess; bills against Max OAuth window.

    Invokes the official `claude -p --model haiku` binary as a child process.
    Never reads, transmits, or stores the OAuth token -- the credential
    stays inside the `claude` process. From Anthropic's server logs the
    request originates from the real claude binary with the real
    request shape, because that's what makes the call.

    Compliance posture: this is a materially different pattern from
    OpenClaw/OpenCode (which extracted tokens and made direct API calls,
    subject to Apr 4 2026 enforcement). The Feb 20 2026 ToS clarification
    on "ordinary use of Claude Code" leaves programmatic CLI invocation in
    an interpretive gray zone. See slm_subscription.py docstring + README.md.
    """
    name = "max"

    def __call__(self, prompt: str, timeout: int = 90) -> tuple[str, float, float]:
        warn_subscription_tos_once()
        return _judge_via_max(prompt, timeout=timeout)


# Pricing for SDK-based judges is looked up from MODEL_PRICING per call site
# so model overrides get accurate cost telemetry. The constants below are
# kept as fallbacks for models not in the pricing table.
_HAIKU_INPUT_USD_PER_M = 1.00     # Claude Haiku 4.5 fallback
_HAIKU_OUTPUT_USD_PER_M = 5.00
_GPT_FALLBACK_INPUT_USD_PER_M = 0.15
_GPT_FALLBACK_OUTPUT_USD_PER_M = 0.60


def _gpt_pricing_for(model: str) -> tuple[float, float]:
    """Return (input_usd_per_M, output_usd_per_M) for an OpenAI model.

    Looks up MODEL_PRICING; falls back to gpt-4o-mini rates for unknown models.
    """
    from prpt.core.constants import MODEL_PRICING
    p = MODEL_PRICING.get(model)
    if p is None:
        return _GPT_FALLBACK_INPUT_USD_PER_M, _GPT_FALLBACK_OUTPUT_USD_PER_M
    return p["input"], p["output"]


class AnthropicApiJudge:
    """Haiku via anthropic SDK + ANTHROPIC_API_KEY; bills against API credits."""
    name = "anthropic"

    def __init__(self, model: str = "claude-haiku-4-5"):
        self.model = model

    def __call__(self, prompt: str, timeout: int = 90) -> tuple[str, float, float]:
        t0 = time.time()
        try:
            from anthropic import Anthropic
        except ImportError:
            return "", 0.0, time.time() - t0
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return "", 0.0, time.time() - t0
        try:
            client = Anthropic(timeout=timeout)
            resp = client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if hasattr(b, "text"))
            in_tok = resp.usage.input_tokens
            out_tok = resp.usage.output_tokens
            cost = (in_tok * _HAIKU_INPUT_USD_PER_M / 1_000_000
                    + out_tok * _HAIKU_OUTPUT_USD_PER_M / 1_000_000)
        except Exception:
            return "", 0.0, time.time() - t0
        return text, cost, time.time() - t0


class OpenAiJudge:
    """OpenAI SDK + OPENAI_API_KEY. Default model is the SLM-tier default
    from constants (currently gpt-5.4-nano)."""
    name = "openai"

    def __init__(self, model: str | None = None):
        from prpt.core.constants import DEFAULT_SLM_OPENAI
        self.model = model or DEFAULT_SLM_OPENAI

    def __call__(self, prompt: str, timeout: int = 90) -> tuple[str, float, float]:
        t0 = time.time()
        try:
            from openai import OpenAI
        except ImportError:
            return "", 0.0, time.time() - t0
        if not os.environ.get("OPENAI_API_KEY"):
            return "", 0.0, time.time() - t0
        try:
            client = OpenAI(timeout=timeout)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=2000,
            )
            text = resp.choices[0].message.content or ""
            in_tok = resp.usage.prompt_tokens
            out_tok = resp.usage.completion_tokens
            in_rate, out_rate = _gpt_pricing_for(self.model)
            cost = (in_tok * in_rate + out_tok * out_rate) / 1_000_000
        except Exception:
            return "", 0.0, time.time() - t0
        return text, cost, time.time() - t0


class CodexCliJudge:
    """Codex CLI subprocess; bills against ChatGPT subscription window.

    Invokes ``codex exec -m <model>`` with the prompt on stdin. Same compliance
    posture as MaxHaikuJudge: the OAuth token stays inside the official codex
    binary, no API spoofing, no client replacement.

    Default model is ``gpt-5.4-mini`` -- the smallest SLM-tier model
    available on ChatGPT-subscription auth. Verified accepted via
    ``codex exec -m gpt-5.4-mini`` on codex CLI 0.130 (2026-05-15).

    ChatGPT-auth codex restricts which models can be invoked. Confirmed
    accepted: ``gpt-5.4-mini``, ``gpt-5.4``, ``gpt-5.3-codex``, ``gpt-5.2``,
    plus the unspecified default (``gpt-5.5``). Legacy ``gpt-4o-mini`` /
    ``o4-mini`` names return 400 ("not supported when using Codex with a
    ChatGPT account"). For API-key-backed codex (no ChatGPT-auth
    restriction), pass any OpenAI model name.

    Per-call cost is heavier than MaxHaikuJudge because ``codex exec`` spins
    up the agent loop even for one-shot prompts (~20k input tokens overhead
    per call, ~6.5k of which are cached). Fine for light SLM use within
    subscription quota; not suitable for high-volume chain experiments. For
    sustained programmatic work, prefer ``OpenAiJudge`` (SDK path) or
    ``MaxHaikuJudge`` (lighter).

    Cost reporting: zero for ChatGPT-subscription billing (shadow $ anyway),
    since we don't have published per-token pricing for gpt-5.x-mini models
    on the subscription pool. For API-key-backed codex with explicit
    ``gpt-4o-mini`` the price is computed against legacy gpt-4o-mini rates.
    """
    name = "codex"

    def __init__(self, model: str | None = "gpt-5.4-mini"):
        self.model = model

    def __call__(self, prompt: str, timeout: int = 90) -> tuple[str, float, float]:
        warn_codex_subscription_tos_once()
        t0 = time.time()
        codex_path = shutil.which("codex") or shutil.which("codex.cmd")
        if not codex_path:
            return "", 0.0, time.time() - t0
        # Use cwd as working dir; --skip-git-repo-check avoids the
        # "not in a git repo" gate so this works anywhere.
        cmd = [
            codex_path, "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--cd", os.getcwd(),
            "--json",
        ]
        if self.model:
            cmd.extend(["-m", self.model])
        cmd.append("-")
        # Recursion guard: same rationale as judge_via_max -- mark the spawned
        # codex subprocess so a `.codex/hooks/optimize_prompt.py` UserPromptSubmit
        # hook skips re-running the SLM rewrite instead of recursing.
        env = dict(os.environ)
        env["PROMPTPILOT_SLM_SUBPROCESS"] = "1"
        try:
            proc = subprocess.run(
                cmd, input=prompt.encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            return "", 0.0, time.time() - t0
        walltime = time.time() - t0

        # Parse JSONL events. Pull agent_message text from item.completed
        # events; pull token counts from turn.completed.usage.
        text_parts: list[str] = []
        in_tok = out_tok = cached_in_tok = 0
        try:
            stdout = proc.stdout.decode("utf-8", errors="replace")
        except Exception:
            return "", 0.0, walltime
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "item.completed":
                item = ev.get("item", {})
                if item.get("type") == "agent_message":
                    text_parts.append(item.get("text", ""))
            elif ev.get("type") == "turn.completed":
                usage = ev.get("usage", {}) or {}
                in_tok = usage.get("input_tokens", 0) or 0
                cached_in_tok = usage.get("cached_input_tokens", 0) or 0
                out_tok = usage.get("output_tokens", 0) or 0

        text = "".join(text_parts).strip()

        # Cost: cached input is billed at 50% of base input rate.
        # input_tokens INCLUDES the cached portion, so compute uncached
        # separately. Uses _gpt_pricing_for() which looks up MODEL_PRICING
        # and falls back to gpt-4o-mini rates for unknown models — so this
        # path is safe for both gpt-4o-mini (legacy) and gpt-5.x-* (current).
        in_rate, out_rate = _gpt_pricing_for(self.model)
        uncached_in = max(0, in_tok - cached_in_tok)
        cost = (
            uncached_in * in_rate / 1_000_000
            + cached_in_tok * in_rate * 0.5 / 1_000_000
            + out_tok * out_rate / 1_000_000
        )
        return text, cost, walltime


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _max_logged_in() -> bool:
    """Quick check: is `claude auth status` reporting logged-in?

    Uses ``shutil.which`` to resolve the binary explicitly -- on Windows,
    ``claude`` is ``claude.cmd`` and bare-name subprocess invocation fails
    with WinError 2.
    """
    claude_path = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude_path:
        return False
    try:
        proc = subprocess.run(
            [claude_path, "auth", "status"],
            capture_output=True, timeout=10,
            env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"},
        )
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    out = proc.stdout.decode("utf-8", errors="replace")
    return '"loggedIn": true' in out or '"loggedIn":true' in out


def _codex_logged_in() -> bool:
    """Quick check: is `codex login status` reporting ChatGPT login?

    Mirrors _max_logged_in. `codex login status` returns text like
    `Logged in using ChatGPT` when authenticated -- but the codex CLI
    writes that line to STDERR, not stdout, so we check both streams.
    """
    codex_path = shutil.which("codex") or shutil.which("codex.cmd")
    if not codex_path:
        return False
    try:
        proc = subprocess.run(
            [codex_path, "login", "status"],
            capture_output=True, timeout=10,
        )
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    return "Logged in" in out or "logged in" in out


def get_default_judge() -> Judge:
    """Pick a judge by ``PROMPTPILOT_JUDGE`` env or auto-detect.

    This is a **fallback priority**, not a setup requirement. Users only
    configure the auth(s) they want; auto-detect chooses among what's
    available. Any one of the four paths works on its own.

    Auto-detect order:
        max > codex > anthropic > openai

    Subscriptions first (use credits already paid for) then API keys. The
    explicit overrides ``PROMPTPILOT_JUDGE=max|codex|anthropic|openai`` win over
    auto-detect when set.

    Power users with both an API key and a subscription can opt into the
    hybrid pattern by passing ``--normalizer slm-anthropic`` or
    ``--normalizer slm-openai`` explicitly -- those bypass this factory and
    use the SDK path for the SLM layer regardless of what auto-detect would
    pick. See README "Pick a setup" for the full decision tree.

    Raises RuntimeError if no usable judge is available.
    """
    explicit = os.environ.get("PROMPTPILOT_JUDGE", "").strip().lower()
    if explicit == "max":
        return MaxHaikuJudge()
    if explicit == "codex":
        return CodexCliJudge()
    if explicit == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "PROMPTPILOT_JUDGE=anthropic but ANTHROPIC_API_KEY is not set."
            )
        return AnthropicApiJudge()
    if explicit == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "PROMPTPILOT_JUDGE=openai but OPENAI_API_KEY is not set."
            )
        return OpenAiJudge()
    if explicit and explicit not in ("", "auto"):
        raise RuntimeError(
            f"Unknown PROMPTPILOT_JUDGE={explicit!r}; "
            "use max, codex, anthropic, openai, or auto."
        )

    # Auto-detect: a fallback priority among whatever the user configured,
    # NOT a setup requirement to enable all four.
    #
    # Order: max > codex > anthropic > openai. Subscriptions preferred
    # because they use credits the user has already paid for. Both Max and
    # Codex subprocess paths invoke the official binary -- no token handling,
    # materially different from the OpenClaw/OpenCode pattern enforced Apr 4
    # 2026. Max is preferred over Codex because the codex agent loop has
    # ~19k input-token overhead per call vs claude -p haiku's much lighter
    # footprint (--tools "" strips the agent loop).
    #
    # Users with both a subscription AND a matching API key can opt into
    # the hybrid pattern via explicit --normalizer slm-anthropic /
    # slm-openai, which bypasses this factory entirely for the SLM layer.
    # See README "Pick a setup" + slm_subscription.py docstring.
    if _max_logged_in():
        return MaxHaikuJudge()
    if _codex_logged_in():
        return CodexCliJudge()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicApiJudge()
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAiJudge()
    raise RuntimeError(
        "No judge available. Either:\n"
        "  - Run `claude auth login --claudeai` for Max OAuth (recommended), or\n"
        "  - Run `codex login` for ChatGPT subscription, or\n"
        "  - Set ANTHROPIC_API_KEY in .env, or\n"
        "  - Set OPENAI_API_KEY in .env\n"
        "Optionally force one with PROMPTPILOT_JUDGE=max|codex|anthropic|openai."
    )
