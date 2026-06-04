"""SLM normalizer that routes through the Judge abstraction.

Works with any backend ``get_default_judge()`` selects (auto-detect order:
``max > codex > anthropic > openai``; see ``judges/judge.py:get_default_judge``).
The default path for users with a Max or ChatGPT subscription -- no API key
needed, calls bill against the subscription quota.

Compliance posture
------------------
Anthropic clarified on **Feb 20, 2026** that subscription OAuth credentials
are intended for "ordinary use of Claude Code and other native Anthropic
applications." On **Apr 4, 2026** technical enforcement landed against
third-party tools (OpenClaw, OpenCode, etc.) that extracted OAuth tokens and
made direct API calls while spoofing Claude Code's request shape.

promptpilot is structurally different from those targets:

- We invoke the official ``claude`` binary as a subprocess.
- We never read, store, or transmit the OAuth token -- the credential lives
  inside the ``claude`` process. From Anthropic's server logs the request
  originates from the real ``claude`` binary with the real user-agent and
  request shape, because that's what made the call.
- We don't impersonate Claude Code; we *are* a user driving Claude Code from
  a script, which is what ``claude -p`` exists to support.

The literal token-clause prohibition does not apply. What remains
interpretive is the broader "ordinary use" framing in the Feb 20 statement:
whether driving ``claude -p`` programmatically counts as "ordinary use"
depends on Anthropic's interpretation, and the answer is probably "yes for
modest interactive use, ambiguous for high-volume automation."

The once-per-process note emitted by ``warn_subscription_tos_once()`` (in
``judges/judge.py``) tells users what's happening so they can make their own
call. We intentionally don't gate this behind a panic-switch env var (e.g.
``PROMPTPILOT_ALLOW_SUBSCRIPTION_ROUTING=1``); ``--normalizer slm-subscription``
or ``PROMPTPILOT_JUDGE=max`` is explicit enough. Held in reserve if Anthropic
broadens enforcement.

A separate monthly credit pool for Agent SDK / ``claude -p`` is expected
**Jun 15, 2026**, which may formalize programmatic subscription use under a
sanctioned billing model. Revisit this docstring after the launch.

Tradeoffs vs the API-key paths
------------------------------
- Each call ~5-7s via subprocess (vs ~1-2s SDK with caching)
- No prompt caching (Judge protocol returns text only)
- Token counts approximated via tiktoken / len(text)//4
- Cost reporting comes directly from the Judge backend; for Max, this is
  "shadow" dollars (real billing is against the Max usage window)
- Net wins: no API charges, no key management, simpler setup
"""
from __future__ import annotations

from typing import Optional

from prpt.core.constants import DEFAULT_TARGET_MODEL, MODEL_PRICING
from prpt.core.types import NormalizedRequest, RepoMetadata, RewriteMode, TokenStats
from prpt.core.utils import write_stderr
from prpt.judges.judge import Judge, get_default_judge
from prpt.normalizers.base import Normalizer
from prpt.normalizers.heuristic import HeuristicNormalizer
from prpt.normalizers.slm_anthropic import (
    _HISTORY_INSTRUCTION, _SYSTEM_ANSWER_DIRECTLY, _SYSTEM_CLASSIFY_ONLY,
    _SYSTEM_CLASSIFY_REFERENTIAL, _SYSTEM_NO_CONTEXT, _SYSTEM_SUGGEST_ACTIONS,
    _SYSTEM_WITH_CONTEXT, SLMNormalizer as _AnthropicNorm,
)
from prpt.repo.loader import RepoContentLoader


def _join(system: str, user: str) -> str:
    """Flatten system+user into a single prompt for the Judge call."""
    return "{0}\n\n{1}".format(system, user)


class SubscriptionSLMNormalizer(Normalizer):
    """SLM normalizer routed through `get_default_judge()`.

    Auth: requires ANY of -- Max OAuth (`claude auth login --claudeai`),
    `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`. Pick explicitly with
    `PROMPTPILOT_JUDGE=max|anthropic|openai`; otherwise auto-detected.
    """

    def __init__(
        self,
        judge: Optional[Judge] = None,
        load_repo_content: bool = True,
    ) -> None:
        self._judge = judge if judge is not None else get_default_judge()
        # Warn once per process when the resolved judge is the subscription
        # path. Anthropic-API and OpenAI-API judges are exempt -- they're the
        # supported routes. See judges/judge.py:warn_subscription_tos_once.
        if getattr(self._judge, "name", "") == "max":
            from prpt.judges.judge import warn_subscription_tos_once
            warn_subscription_tos_once()
        self._heuristic = HeuristicNormalizer()
        self._content_loader = RepoContentLoader() if load_repo_content else None
        self._last_context_block: Optional[str] = None
        self._last_intent: Optional[str] = None
        self._last_scope: Optional[str] = None
        # Working directory the judge subprocess runs in. Set from repo.cwd on
        # every repo-bearing call so the Claude/Codex CLI resolves project
        # context (CLAUDE.md / project memory) from the --cwd target, not the
        # process cwd. Leaving it unset would bleed the launch repo's files
        # into the rewrite when --cwd differs from where prpt was launched.
        self._cwd: Optional[str] = None
        # Sum across all judge calls in a single normalize() invocation.
        self._last_cost_usd: float = 0.0
        self._last_walltime_s: float = 0.0
        # Approximated input/output char counts so compute_token_stats works.
        self._last_input_chars: int = 0
        self._last_output_chars: int = 0

    # ------------------------------------------------------------------
    # Internal: single judge call with bookkeeping
    # ------------------------------------------------------------------

    def _ask(
        self, system: str, user: str, *, timeout: int = 90, cwd: Optional[str] = None,
    ) -> str:
        prompt = _join(system, user)
        # Resolve cwd from the explicit arg, falling back to the last repo.cwd
        # seen. Never silently inherits the process cwd for grounded calls.
        run_cwd = cwd if cwd is not None else self._cwd
        text, cost, walltime = self._judge(prompt, timeout=timeout, cwd=run_cwd)
        self._last_cost_usd += cost
        self._last_walltime_s += walltime
        self._last_input_chars += len(prompt)
        self._last_output_chars += len(text)
        return text

    def _reset_usage(self) -> None:
        self._last_cost_usd = 0.0
        self._last_walltime_s = 0.0
        self._last_input_chars = 0
        self._last_output_chars = 0

    # ------------------------------------------------------------------
    # Normalizer interface
    # ------------------------------------------------------------------

    def normalize(
        self, prompt: str, repo: RepoMetadata, high_stakes: bool = False,
    ) -> NormalizedRequest:
        rewritten = self._rewrite(prompt, repo)
        meta = self._heuristic.normalize(rewritten, repo, high_stakes=high_stakes)
        return NormalizedRequest(
            original_prompt=prompt,
            task_type=meta.task_type, objective=meta.objective,
            explicit_context=meta.explicit_context, hard_constraints=meta.hard_constraints,
            soft_preferences=meta.soft_preferences, requested_output=meta.requested_output,
            protected_spans=meta.protected_spans, ambiguities=meta.ambiguities,
            assumptions=meta.assumptions, omissions=[],
            confidence=meta.confidence, needs_review=meta.needs_review,
            rewrite_mode=RewriteMode.EXTRACT_PLUS_LIGHT_REWRITE.value,
            normalized_prompt=rewritten,
        )

    def answer_directly(self, prompt: str, repo: RepoMetadata) -> str:
        self._cwd = repo.cwd
        context_block = (
            self._last_context_block
            if self._last_context_block is not None
            else (self._content_loader.build_context_block(prompt, repo)
                  if self._content_loader else "")
        )
        if context_block:
            user_content = (
                "<repository_context>\n{0}\n</repository_context>\n\n"
                "<question>\n{1}\n</question>"
            ).format(context_block, prompt)
        else:
            user_content = prompt
        try:
            text = self._ask(_SYSTEM_ANSWER_DIRECTLY, user_content, timeout=120, cwd=repo.cwd)
            return text.strip()
        except Exception as exc:
            write_stderr("[slm-subscription] Direct answer failed: {0}".format(exc))
            return ""

    def suggest_actions(self, explanation: str, repo: RepoMetadata) -> list[str]:
        self._cwd = repo.cwd
        context_block = (
            self._last_context_block
            if self._last_context_block is not None
            else (self._content_loader.build_context_block(explanation, repo)
                  if self._content_loader else "")
        )
        if context_block:
            user_content = (
                "<repository_context>\n{0}\n</repository_context>\n\n"
                "<explanation_given>\n{1}\n</explanation_given>"
            ).format(context_block, explanation)
        else:
            user_content = explanation
        try:
            raw = self._ask(_SYSTEM_SUGGEST_ACTIONS, user_content, cwd=repo.cwd).strip()
            actions: list[str] = []
            for line in raw.splitlines():
                line = line.strip()
                if line and line[0].isdigit():
                    action = line.lstrip("0123456789").lstrip(".)").strip()
                    if action:
                        actions.append(action)
            return actions[:3]
        except Exception as exc:
            write_stderr("[slm-subscription] suggest_actions failed: {0}".format(exc))
            return []

    def _classify(self, prompt: str, cwd: Optional[str] = None) -> tuple[str, str]:
        """Pass 1: classify intent and scope from raw prompt -- no context loaded."""
        try:
            text = self._ask(_SYSTEM_CLASSIFY_ONLY, prompt, timeout=30, cwd=cwd)
            intent, scope, _ = _AnthropicNorm._parse_intent_response(text)
            return intent, scope
        except Exception as exc:
            write_stderr(
                "[slm-subscription] classify failed ({0}), defaulting act/localized.".format(exc)
            )
            return "act", "localized"

    def is_referential(self, prompt: str, cwd: Optional[str] = None) -> bool:
        """Cheap referential check: does this prompt need prior conversation context?

        ``cwd`` is the --cwd target so the classifier subprocess resolves
        project context from the right repo (see __init__ note on _cwd).

        Fail-safe: on any classifier error, returns True so we DO load history.
        """
        if cwd is not None:
            self._cwd = cwd
        try:
            text = self._ask(_SYSTEM_CLASSIFY_REFERENTIAL, prompt, timeout=30, cwd=cwd)
            upper = text.strip().upper()
            if "NO" in upper and "YES" not in upper:
                return False
            return True
        except Exception as exc:
            write_stderr(
                "[slm-subscription] referential classify failed ({0}), "
                "defaulting to load history.".format(exc)
            )
            return True

    def _rewrite(self, prompt: str, repo: RepoMetadata) -> str:
        self._reset_usage()
        self._last_intent = None
        self._last_scope = None
        self._cwd = repo.cwd
        try:
            if self._last_context_block is not None:
                context_block = self._last_context_block
            else:
                _, scope = self._classify(prompt, cwd=repo.cwd)
                context_block = (
                    self._content_loader.build_context_block(prompt, repo, scope=scope)
                    if self._content_loader else ""
                )
                self._last_context_block = context_block

            if context_block:
                system = _SYSTEM_WITH_CONTEXT
                user_content = (
                    "<repository_context>\n{0}\n</repository_context>\n\n"
                    "<developer_prompt>\n{1}\n</developer_prompt>"
                ).format(context_block, prompt)
            else:
                system = _SYSTEM_NO_CONTEXT
                user_content = prompt

            if "[Recent conversation]" in prompt:
                system = system + _HISTORY_INSTRUCTION

            text = self._ask(system, user_content, cwd=repo.cwd)
            if not text:
                # Judge returned empty (timeout, missing SDK, auth fail).
                # Fall back to original so the wrapper still does something useful.
                write_stderr(
                    "[slm-subscription] judge returned empty, using original prompt."
                )
                return prompt
            intent, scope, rewritten = _AnthropicNorm._parse_intent_response(text)
            self._last_intent = intent
            self._last_scope = scope
            return rewritten
        except Exception as exc:
            write_stderr(
                "[slm-subscription] rewrite failed ({0}), using original.".format(exc)
            )
            return prompt

    # ------------------------------------------------------------------
    # Token / cost stats
    # ------------------------------------------------------------------

    def compute_token_stats(
        self, original_prompt: str, final_prompt: str,
        target_model: str = DEFAULT_TARGET_MODEL,
    ) -> Optional[TokenStats]:
        if self._last_input_chars == 0 and self._last_output_chars == 0:
            return None
        original_tokens = self._count_tokens(original_prompt)
        final_tokens = self._count_tokens(final_prompt)

        # Approximate token counts from char counts (rough 4 chars/token).
        slm_in = self._last_input_chars // 4
        slm_out = self._last_output_chars // 4

        target_price = MODEL_PRICING.get(
            target_model, {"input": 15.00, "output": 75.00}
        )
        delta = original_tokens - final_tokens
        gross = delta * target_price["input"] / 1_000_000
        return TokenStats(
            original_tokens=original_tokens, final_tokens=final_tokens, delta_tokens=delta,
            haiku_input_tokens=slm_in, haiku_output_tokens=slm_out,
            haiku_cost_usd=self._last_cost_usd,
            target_model=target_model,
            gross_savings_usd=gross, net_savings_usd=gross - self._last_cost_usd,
        )

    @staticmethod
    def _count_tokens(text: str) -> int:
        """Approximate token count. Tries tiktoken; falls back to len/4."""
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            return len(text) // 4
