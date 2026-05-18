"""SLM normalizer backed by Claude Haiku (Anthropic)."""
from __future__ import annotations

import os
from typing import Optional

from prpt.core.constants import DEFAULT_SLM_ANTHROPIC, DEFAULT_TARGET_MODEL, MODEL_PRICING
from prpt.core.types import NormalizedRequest, RepoMetadata, RewriteMode, TokenStats
from prpt.core.utils import write_stderr
from prpt.normalizers.base import Normalizer
from prpt.normalizers.heuristic import HeuristicNormalizer
from prpt.repo.loader import RepoContentLoader

_INTENT_INSTRUCTION = (
    "First output two classification lines, then a separator:\n\n"
    "  INTENT: explain   — developer wants understanding/explanation/investigation\n"
    "  INTENT: act       — developer wants code changes, fix, refactor, tests, or new feature\n"
    "  (When in doubt, choose 'act'.)\n\n"
    "  SCOPE: pinpoint   — surgical 1-10 line change, very targeted fix\n"
    "  SCOPE: localized  — 10-50 lines concentrated in one function/area\n"
    "  SCOPE: broad      — major refactor, cross-file, or 50%+ of a file changes\n"
    "  SCOPE: new        — creating new file(s) or large new feature from scratch\n"
    "  (For 'explain' intent, always use SCOPE: new.)\n\n"
    "Then output a '---' separator, followed by the rewritten prompt.\n\n"
    "Example header:\n"
    "  INTENT: act\n"
    "  SCOPE: pinpoint\n"
    "  ---\n\n"
)

_SYSTEM_NO_CONTEXT = (
    "You are a prompt optimizer for AI coding assistants.\n\n"
    "Given a developer's raw coding task prompt, rewrite it to be:\n"
    "- Precise and unambiguous about what needs to be done\n"
    "- Explicit about any constraints (what not to change, backward compat, etc.)\n"
    "- Clear about the expected output (patch, explanation, tests, etc.)\n"
    "- As concise as possible while preserving ALL intent\n\n"
    + _INTENT_INSTRUCTION +
    "Rules:\n"
    "- After the INTENT/separator lines, output ONLY the rewritten prompt — no commentary\n"
    "- Never invent requirements not present in the original\n"
    "- Preserve identifiers, file names, and technical terms exactly\n"
    "- Keep hard constraints verbatim (e.g. 'do not touch X', 'minimal patch only')"
)

_SYSTEM_ANSWER_DIRECTLY = (
    "You are a knowledgeable coding assistant with access to a repository's source code.\n\n"
    "Given a developer's question and relevant file excerpts, provide a clear, direct answer.\n\n"
    "Rules:\n"
    "- Reference specific file paths, class/function names, and line numbers where relevant\n"
    "- Be concise but complete — write prose, not bullet soup\n"
    "- Do not fabricate code that is not present in the provided context\n"
    "- If the context is insufficient to fully answer, say so and answer what you can"
)

_SYSTEM_SUGGEST_ACTIONS = (
    "You are a coding assistant that has just explained part of a codebase to a developer.\n\n"
    "Given the repository context and the explanation you provided, suggest 3 concrete "
    "follow-up actions the developer is most likely to want. Each suggestion should be a "
    "short, specific action prompt that could be sent directly to an AI coding tool.\n\n"
    "Rules:\n"
    "- Output exactly 3 suggestions, one per line, numbered 1-3\n"
    "- Each suggestion must be a single imperative sentence (e.g. 'Fix the ...', 'Add tests for ...', 'Refactor ...')\n"
    "- Reference specific file paths, functions, or classes from the context\n"
    "- Focus on the most impactful, likely next steps — bug fixes first, then improvements, then tests\n"
    "- Do not add commentary, headers, or explanations — just the 3 numbered lines"
)

# ---------------------------------------------------------------------------
# Prompt-caching helpers
# ---------------------------------------------------------------------------
# Haiku's minimum cacheable block is 2048 tokens.  Below that the
# `cache_control` marker is a silent no-op, which is fine — but we skip it
# to avoid cluttering requests needlessly.
_CACHE_MIN_CHARS = 2048 * 4   # rough: ~2k tokens ≈ 8k chars
_EPHEMERAL = {"type": "ephemeral"}


def _system_blocks(system_text: str, enable_cache: bool):
    """Build a system message. Marks it cacheable when caching is enabled."""
    block = {"type": "text", "text": system_text}
    if enable_cache:
        block["cache_control"] = _EPHEMERAL
    return [block]


def _user_blocks_with_context(context_block: str, prompt: str, enable_cache: bool):
    """
    Build a user message split into (cacheable context) + (fresh prompt).

    The cache breakpoint sits at the end of the context block, so on repeat
    calls Anthropic returns `cache_read_input_tokens` for the context and
    only bills the uncached tail (the new prompt).
    """
    ctx = (
        "<repository_context>\n{0}\n</repository_context>"
    ).format(context_block)
    tail = "<developer_prompt>\n{0}\n</developer_prompt>".format(prompt)

    ctx_block = {"type": "text", "text": ctx}
    if enable_cache and len(context_block) >= _CACHE_MIN_CHARS:
        ctx_block["cache_control"] = _EPHEMERAL

    return [ctx_block, {"type": "text", "text": tail}]


def _extract_usage(response) -> dict:
    """Pull Anthropic usage numbers into a flat dict, including cache metrics."""
    u = response.usage
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }


_SYSTEM_CLASSIFY_ONLY = (
    "You are a coding-task classifier.\n\n"
    "Given a developer's prompt, output exactly two lines — nothing else:\n\n"
    "  INTENT: explain   — developer wants understanding/explanation/investigation\n"
    "  INTENT: act       — developer wants code changes, fix, refactor, tests, or new feature\n"
    "  (When in doubt, choose 'act'.)\n\n"
    "  SCOPE: pinpoint   — surgical 1-10 line change, very targeted fix\n"
    "  SCOPE: localized  — 10-50 lines concentrated in one function/area\n"
    "  SCOPE: broad      — major refactor, cross-file, or 50%+ of a file changes\n"
    "  SCOPE: new        — creating new file(s) or large new feature from scratch\n"
    "  (For 'explain' intent, always use SCOPE: new.)\n\n"
    "Output format — exactly this, no other text:\n"
    "  INTENT: <intent>\n"
    "  SCOPE: <scope>"
)

_SYSTEM_CLASSIFY_REFERENTIAL = (
    "You are a coding-prompt classifier.\n\n"
    "Decide whether a developer's prompt REFERS BACK to a prior conversation turn "
    "(and therefore needs prior context to resolve), or stands ALONE.\n\n"
    "REFERENTIAL signals — say YES if the prompt contains any of:\n"
    "  - back-pointing pronouns: 'that', 'it', 'this', 'those', 'the same'\n"
    "  - additive references:    'also', 'as well', 'too', 'now do X', 'and X'\n"
    "  - repeats/extensions:     'the fix', 'the change', 'the function we wrote',\n"
    "                            'what you just did', 'extend that'\n"
    "  - 'apply / mirror / repeat the same X to Y'\n\n"
    "SELF-CONTAINED — say NO if the prompt:\n"
    "  - names specific files/functions/classes and describes a complete task\n"
    "  - is a fresh standalone question or fresh code change\n"
    "  - does NOT use any backref words listed above\n\n"
    "Output exactly one line, nothing else:\n"
    "  REFERENTIAL: yes\n"
    "  REFERENTIAL: no"
)

_HISTORY_INSTRUCTION = (
    "\nThe prompt may begin with a [Recent conversation] block showing prior turns.\n"
    "If present, use it ONLY to resolve ambiguous references in the current request "
    "('that', 'it', 'the fix', 'the same thing', 'also', 'now do X too', etc.).\n\n"
    "Rules for conversation history:\n"
    "- Do NOT summarize, quote, or include prior turns in the rewritten prompt\n"
    "- Write the rewritten prompt as a fully self-contained instruction — as if the "
    "resolved context were always known\n"
    "- If the current request is unrelated to the history, ignore the history entirely\n"
    "- Never expand scope based on history — rewrite only what is being asked NOW"
)

_SYSTEM_WITH_CONTEXT = (
    "You are a prompt optimizer for AI coding assistants.\n\n"
    "You will receive:\n"
    "1. Repository context: the file tree and contents of relevant/changed files\n"
    "2. A developer's raw coding task prompt\n\n"
    "Using the repository context, rewrite the prompt to be:\n"
    "- Grounded in the actual code — reference real file paths, class/function names, "
    "and line-level detail where relevant\n"
    "- Precise about exactly what needs to change and where\n"
    "- Explicit about constraints (what not to touch, backward compat, etc.)\n"
    "- Clear about the expected output (patch, explanation, tests, etc.)\n"
    "- As concise as possible while preserving ALL intent\n\n"
    + _INTENT_INSTRUCTION +
    "Rules:\n"
    "- After the INTENT/separator lines, output ONLY the rewritten prompt — no commentary\n"
    "- Never invent requirements not present in the original\n"
    "- Preserve identifiers exactly as they appear in the code\n"
    "- Keep hard constraints verbatim (e.g. 'do not touch X', 'minimal patch only')\n"
    "- If the repo context does not contain enough information, still produce the best "
    "rewrite you can without inventing details"
)


class SLMNormalizer(Normalizer):
    """
    Rewrites the prompt via Claude Haiku, then extracts metadata heuristically.

    Requires: pip install prpt[claude]
    Requires: ANTHROPIC_API_KEY env var (or api_key kwarg)
    """

    MODEL = DEFAULT_SLM_ANTHROPIC
    MAX_TOKENS = 512

    def __init__(self, api_key: Optional[str] = None, load_repo_content: bool = True) -> None:
        try:
            import anthropic as _anthropic
            self._client = _anthropic.Anthropic(
                api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
                timeout=60.0,       # hard cap on a single HTTP call (default is 600s)
                max_retries=2,      # retry transient failures, but bounded
            )
        except ImportError:
            raise ImportError(
                "slm-anthropic normalizer requires the anthropic SDK.\n"
                "  Run: pip install prpt[claude]\n"
                "Or use --normalizer slm-subscription if you have Max OAuth."
            )
        self._heuristic = HeuristicNormalizer()
        self._content_loader = RepoContentLoader() if load_repo_content else None
        self._last_usage: Optional[dict] = None
        self._last_context_block: Optional[str] = None
        self._last_intent: Optional[str] = None   # "explain" or "act"
        self._last_scope: Optional[str] = None    # "pinpoint", "localized", "broad", or "new"

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
        enable_cache = bool(context_block) and len(context_block) >= _CACHE_MIN_CHARS
        if context_block:
            messages = [{
                "role": "user",
                "content": _user_blocks_with_context(context_block, prompt, enable_cache),
            }]
        else:
            messages = [{"role": "user", "content": prompt}]
        try:
            response = self._client.messages.create(
                model=self.MODEL, max_tokens=2048,
                system=_system_blocks(_SYSTEM_ANSWER_DIRECTLY, enable_cache),
                messages=messages,
            )
            self._last_usage = _extract_usage(response)
            return response.content[0].text.strip()
        except Exception as exc:
            write_stderr("[slm-anthropic] Direct answer failed: {0}".format(exc))
            return ""

    def suggest_actions(self, explanation: str, repo: RepoMetadata) -> list[str]:
        """Suggest 3 follow-up action prompts based on the explanation and repo context."""
        context_block = (
            self._last_context_block
            if self._last_context_block is not None
            else (self._content_loader.build_context_block(explanation, repo) if self._content_loader else "")
        )
        enable_cache = bool(context_block) and len(context_block) >= _CACHE_MIN_CHARS
        if context_block:
            messages = [{
                "role": "user",
                "content": _user_blocks_with_context(context_block, explanation, enable_cache),
            }]
        else:
            messages = [{"role": "user", "content": explanation}]
        try:
            response = self._client.messages.create(
                model=self.MODEL, max_tokens=256,
                system=_system_blocks(_SYSTEM_SUGGEST_ACTIONS, enable_cache),
                messages=messages,
            )
            raw = response.content[0].text.strip()
            # Parse numbered lines: "1. Fix ...", "2. Add ...", "3. Refactor ..."
            actions = []
            for line in raw.splitlines():
                line = line.strip()
                if line and line[0].isdigit():
                    # Strip leading "1. " or "1) " prefix
                    action = line.lstrip("0123456789").lstrip(".)")
                    action = action.strip()
                    if action:
                        actions.append(action)
            return actions[:3]
        except Exception as exc:
            write_stderr("[slm-anthropic] suggest_actions failed: {0}".format(exc))
            return []

    @staticmethod
    def _parse_intent_response(raw_text: str) -> tuple[str, str, str]:
        """Parse 'INTENT: act\\nSCOPE: pinpoint\\n---\\n<rewrite>' format.

        Returns (intent, scope, rewritten_prompt).
        Falls back to ("act", "localized", raw_text) if the format is absent.
        """
        text = raw_text.strip()
        intent = "act"
        scope = "localized"

        lines = text.splitlines()
        consume = 0
        for line in lines:
            s = line.strip()
            upper = s.upper()
            if upper.startswith("INTENT:"):
                val = s.split(":", 1)[1].strip().lower()
                if val in ("explain", "act"):
                    intent = val
                consume += 1
            elif upper.startswith("SCOPE:"):
                val = s.split(":", 1)[1].strip().lower()
                if val in ("pinpoint", "localized", "broad", "new"):
                    scope = val
                consume += 1
            elif s == "---":
                consume += 1
                break
            else:
                break  # non-header line before separator

        rest = "\n".join(lines[consume:]).strip()
        return intent, scope, rest if rest else text

    def _classify(self, prompt: str) -> tuple[str, str]:
        """Pass 1: classify intent and scope from raw prompt only — no context loaded.

        Returns (intent, scope). Falls back to ("act", "localized") on any failure.
        Costs ~$0.00017 (200 input + 10 output Haiku tokens).
        """
        try:
            response = self._client.messages.create(
                model=self.MODEL, max_tokens=16,
                system=_SYSTEM_CLASSIFY_ONLY,
                messages=[{"role": "user", "content": prompt}],
            )
            intent, scope, _ = self._parse_intent_response(response.content[0].text)
            return intent, scope
        except Exception as exc:
            write_stderr("[slm-anthropic] classify failed ({0}), defaulting act/localized.".format(exc))
            return "act", "localized"

    def is_referential(self, prompt: str) -> bool:
        """Cheap referential check: does this prompt need prior conversation context?

        Returns True if the prompt back-references prior turns ('that fix',
        'the same', 'also', etc.). Returns False for self-contained prompts.

        Fail-safe: on any classifier error, returns True so we DO load history
        rather than silently dropping memory on a turn that needed it.

        Costs ~$0.00017 (200 input + 5 output Haiku tokens).
        """
        try:
            response = self._client.messages.create(
                model=self.MODEL, max_tokens=8,
                system=_SYSTEM_CLASSIFY_REFERENTIAL,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip().upper()
            # Accept "REFERENTIAL: NO" or "NO" or "REFERENTIAL:NO" — robust to spacing
            if "NO" in text and "YES" not in text:
                return False
            return True
        except Exception as exc:
            write_stderr("[slm-anthropic] referential classify failed ({0}), defaulting to load history.".format(exc))
            return True

    def _rewrite(self, prompt: str, repo: RepoMetadata) -> str:
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
                system_text = _SYSTEM_WITH_CONTEXT
            else:
                system_text = _SYSTEM_NO_CONTEXT

            if "[Recent conversation]" in prompt:
                system_text = system_text + _HISTORY_INSTRUCTION

            # Enable caching whenever we have a sizeable context block.
            # Haiku requires ≥2048 tokens in the cached prefix; below that
            # Anthropic silently ignores the marker (so this is safe to set).
            enable_cache = bool(context_block) and len(context_block) >= _CACHE_MIN_CHARS

            if context_block:
                messages = [{
                    "role": "user",
                    "content": _user_blocks_with_context(context_block, prompt, enable_cache),
                }]
            else:
                messages = [{"role": "user", "content": prompt}]

            response = self._client.messages.create(
                model=self.MODEL, max_tokens=self.MAX_TOKENS,
                system=_system_blocks(system_text, enable_cache),
                messages=messages,
            )
            self._last_usage = _extract_usage(response)
            intent, scope, rewritten = self._parse_intent_response(response.content[0].text)
            self._last_intent = intent
            self._last_scope = scope
            return rewritten
        except Exception as exc:
            write_stderr("[slm-anthropic] Haiku rewrite failed ({0}), using original.".format(exc))
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
        cache_read = self._last_usage.get("cache_read_input_tokens", 0)
        cache_write = self._last_usage.get("cache_creation_input_tokens", 0)

        slm_price = MODEL_PRICING.get(self.MODEL, {"input": 0.80, "output": 4.00})
        target_price = MODEL_PRICING.get(target_model, {"input": 15.00, "output": 75.00})

        # Anthropic pricing:  cache-write = 1.25× input,  cache-read = 0.10× input
        slm_cost = (
            slm_in * slm_price["input"]
            + slm_out * slm_price["output"]
            + cache_write * slm_price["input"] * 1.25
            + cache_read * slm_price["input"] * 0.10
        ) / 1_000_000

        # Cache savings: what the cache_read tokens would have cost at full price,
        # minus what we actually paid (0.10×).  Writes break even over ≥1 reuse.
        cache_savings = cache_read * slm_price["input"] * 0.90 / 1_000_000

        delta = original_tokens - final_tokens
        gross = delta * target_price["input"] / 1_000_000
        return TokenStats(
            original_tokens=original_tokens, final_tokens=final_tokens, delta_tokens=delta,
            haiku_input_tokens=slm_in, haiku_output_tokens=slm_out, haiku_cost_usd=slm_cost,
            target_model=target_model, gross_savings_usd=gross, net_savings_usd=gross - slm_cost,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
            cache_savings_usd=cache_savings,
        )

    def _count_tokens(self, text: str, model: str) -> int:
        try:
            result = self._client.messages.count_tokens(
                model=model, messages=[{"role": "user", "content": text}],
            )
            return result.input_tokens
        except Exception:
            return len(text) // 4
