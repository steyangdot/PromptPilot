"""SLM normalizer backed by GPT-5.4-nano (OpenAI).

Auth: ``OPENAI_API_KEY`` only -- this normalizer uses the openai SDK,
which doesn't accept ChatGPT subscription credentials. There is intentionally
no codex-subscription-routed equivalent (no ``CodexCliJudge`` mirroring
``MaxHaikuJudge``); see slm_subscription.py docstring + README.md for the
ToS-driven reasoning. Codex CLI users still need an API key (OpenAI or
Anthropic) to power the SLM normalizer / handoff judge layer.
"""
from __future__ import annotations

import os
from typing import Optional

from prpt.core.constants import DEFAULT_SLM_OPENAI, DEFAULT_TARGET_MODEL, MODEL_PRICING
from prpt.core.types import NormalizedRequest, RepoMetadata, RewriteMode, TokenStats
from prpt.core.utils import write_stderr
from prpt.normalizers.base import Normalizer
from prpt.normalizers.heuristic import HeuristicNormalizer
from prpt.repo.loader import RepoContentLoader

# Reuse the same system prompts — they're model-agnostic
from prpt.normalizers.slm_anthropic import (
    _HISTORY_INSTRUCTION, _SYSTEM_ANSWER_DIRECTLY, _SYSTEM_CLASSIFY_ONLY,
    _SYSTEM_NO_CONTEXT, _SYSTEM_SUGGEST_ACTIONS, _SYSTEM_WITH_CONTEXT,
)


class OpenAISLMNormalizer(Normalizer):
    """
    Rewrites the prompt via GPT-5.4-nano (default; configurable), then extracts metadata heuristically.

    Requires: pip install prpt[codex]
    Requires: OPENAI_API_KEY env var (or api_key kwarg)
    """

    MODEL = DEFAULT_SLM_OPENAI
    MAX_TOKENS = 512

    def __init__(self, api_key: Optional[str] = None, load_repo_content: bool = True) -> None:
        try:
            import openai as _openai
            resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
            if not resolved_key:
                raise RuntimeError(
                    "slm-openai normalizer requires OPENAI_API_KEY "
                    "(SDK auth, bills against OpenAI API credits).\n"
                    "If you're on a ChatGPT subscription, use one of:\n"
                    "  - --normalizer slm               (auto-detect: routes via "
                    "CodexCliJudge against subscription)\n"
                    "  - --normalizer slm-subscription  (explicit subscription routing)\n"
                    "Or set OPENAI_API_KEY in .env to use the SDK path."
                )
            self._client = _openai.OpenAI(api_key=resolved_key)
        except ImportError:
            raise ImportError(
                "slm-openai normalizer requires the openai SDK.\n"
                "  Run: pip install prpt[codex]"
            )
        self._heuristic = HeuristicNormalizer()
        self._content_loader = RepoContentLoader() if load_repo_content else None
        self._last_usage: Optional[dict] = None
        self._last_context_block: Optional[str] = None
        self._last_intent: Optional[str] = None
        self._last_scope: Optional[str] = None

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
        """Answer an explanation/question prompt using the SLM + repo context directly."""
        context_block = (
            self._last_context_block
            if self._last_context_block is not None
            else (self._content_loader.build_context_block(prompt, repo) if self._content_loader else "")
        )
        if context_block:
            user_content = (
                "<repository_context>\n{0}\n</repository_context>\n\n"
                "<question>\n{1}\n</question>"
            ).format(context_block, prompt)
        else:
            user_content = prompt
        try:
            response = self._client.chat.completions.create(
                model=self.MODEL,
                max_completion_tokens=2048,
                messages=[
                    {"role": "system", "content": _SYSTEM_ANSWER_DIRECTLY},
                    {"role": "user", "content": user_content},
                ],
            )
            self._last_usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
            return response.choices[0].message.content.strip()
        except Exception as exc:
            write_stderr("[slm-openai] Direct answer failed: {0}".format(exc))
            return ""

    def suggest_actions(self, explanation: str, repo: RepoMetadata) -> list[str]:
        """Suggest 3 follow-up action prompts based on the explanation and repo context."""
        context_block = (
            self._last_context_block
            if self._last_context_block is not None
            else (self._content_loader.build_context_block(explanation, repo) if self._content_loader else "")
        )
        user_content = (
            "<repository_context>\n{0}\n</repository_context>\n\n"
            "<explanation_given>\n{1}\n</explanation_given>"
        ).format(context_block, explanation) if context_block else explanation
        try:
            response = self._client.chat.completions.create(
                model=self.MODEL,
                max_completion_tokens=256,
                messages=[
                    {"role": "system", "content": _SYSTEM_SUGGEST_ACTIONS},
                    {"role": "user", "content": user_content},
                ],
            )
            raw = response.choices[0].message.content.strip()
            actions = []
            for line in raw.splitlines():
                line = line.strip()
                if line and line[0].isdigit():
                    action = line.lstrip("0123456789").lstrip(".)")
                    action = action.strip()
                    if action:
                        actions.append(action)
            return actions[:3]
        except Exception as exc:
            write_stderr("[slm-openai] suggest_actions failed: {0}".format(exc))
            return []

    def _classify(self, prompt: str) -> tuple[str, str]:
        """Pass 1: classify intent and scope from raw prompt only — no context loaded.

        Returns (intent, scope). Falls back to ("act", "localized") on any failure.
        """
        from prpt.normalizers.slm_anthropic import SLMNormalizer as _AnthropicNorm
        try:
            response = self._client.chat.completions.create(
                model=self.MODEL,
                max_completion_tokens=16,
                messages=[
                    {"role": "system", "content": _SYSTEM_CLASSIFY_ONLY},
                    {"role": "user", "content": prompt},
                ],
            )
            intent, scope, _ = _AnthropicNorm._parse_intent_response(
                response.choices[0].message.content
            )
            return intent, scope
        except Exception as exc:
            write_stderr("[slm-openai] classify failed ({0}), defaulting act/localized.".format(exc))
            return "act", "localized"

    def is_referential(self, prompt: str) -> bool:
        """Cheap referential check: does this prompt need prior conversation context?

        Returns True if the prompt back-references prior turns ('that fix',
        'the same', 'also', etc.). Returns False for self-contained prompts.

        Fail-safe: on any classifier error, returns True so we DO load history
        rather than silently dropping memory on a turn that needed it.

        Costs ~$0.00004 (200 input + 5 output gpt-5.4-nano tokens at $0.20/M in, $1.25/M out).
        """
        from prpt.normalizers.slm_anthropic import _SYSTEM_CLASSIFY_REFERENTIAL
        try:
            response = self._client.chat.completions.create(
                model=self.MODEL,
                max_completion_tokens=8,
                messages=[
                    {"role": "system", "content": _SYSTEM_CLASSIFY_REFERENTIAL},
                    {"role": "user", "content": prompt},
                ],
            )
            text = (response.choices[0].message.content or "").strip().upper()
            # Accept "REFERENTIAL: NO" or "NO" or "REFERENTIAL:NO" — robust to spacing
            if "NO" in text and "YES" not in text:
                return False
            return True
        except Exception as exc:
            write_stderr(
                "[slm-openai] referential classify failed ({0}), defaulting to load history.".format(exc)
            )
            return True

    def _rewrite(self, prompt: str, repo: RepoMetadata) -> str:
        from prpt.normalizers.slm_anthropic import SLMNormalizer as _AnthropicNorm

        self._last_usage = None
        self._last_intent = None
        self._last_scope = None
        try:
            if self._last_context_block is not None:
                context_block = self._last_context_block
            else:
                # Pass 1: classify scope cheaply before loading any context
                _, scope = self._classify(prompt)
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

            response = self._client.chat.completions.create(
                model=self.MODEL,
                max_completion_tokens=self.MAX_TOKENS,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
            )
            self._last_usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
            intent, scope, rewritten = _AnthropicNorm._parse_intent_response(
                response.choices[0].message.content
            )
            self._last_intent = intent
            self._last_scope = scope
            return rewritten
        except Exception as exc:
            write_stderr("[slm-openai] GPT-5.4-nano rewrite failed ({0}), using original.".format(exc))
            return prompt

    def compute_token_stats(
        self, original_prompt: str, final_prompt: str, target_model: str = DEFAULT_TARGET_MODEL,
    ) -> Optional[TokenStats]:
        if self._last_usage is None:
            return None
        original_tokens = self._count_tokens(original_prompt, target_model)
        final_tokens = self._count_tokens(final_prompt, target_model)
        slm_in = self._last_usage["input_tokens"]
        slm_out = self._last_usage["output_tokens"]
        slm_price = MODEL_PRICING.get(self.MODEL, {"input": 0.15, "output": 0.60})
        target_price = MODEL_PRICING.get(target_model, {"input": 15.00, "output": 75.00})
        slm_cost = (slm_in * slm_price["input"] + slm_out * slm_price["output"]) / 1_000_000
        delta = original_tokens - final_tokens
        gross = delta * target_price["input"] / 1_000_000
        return TokenStats(
            original_tokens=original_tokens, final_tokens=final_tokens, delta_tokens=delta,
            haiku_input_tokens=slm_in, haiku_output_tokens=slm_out, haiku_cost_usd=slm_cost,
            target_model=target_model, gross_savings_usd=gross, net_savings_usd=gross - slm_cost,
        )

    def _count_tokens(self, text: str, model: str) -> int:
        """Count tokens using tiktoken (offline, no API call)."""
        try:
            import tiktoken
            try:
                enc = tiktoken.encoding_for_model(model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            return len(text) // 4
