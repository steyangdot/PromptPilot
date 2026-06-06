"""SLM normalizer v2 (Anthropic) — emits a JSON ExecutionSpec instead of the
prose ``INTENT:/SCOPE:/---/<rewrite>`` envelope.

This is the Claude Haiku sibling of ``slm-openai-v2``. It inherits everything
from the v1 :class:`SLMNormalizer` (classify, answer, suggest, referential,
token accounting, prompt caching) and overrides only the rewrite path so the
model returns a single JSON object describing how to *route* the request
(answer / act / clarify / passthrough) plus the rewritten downstream prompt.

The clarify route is the headline capability v1 cannot express: on an
underspecified prompt the model returns ``route="clarify"`` with the clarifying
question in ``downstream_prompt``, and the CLI prints it and exits 0 instead of
spending a downstream agent run on a guess.

Failure discipline (identical to slm-openai-v2, per the v2 parser-migration
guardrails):
- JSON parse failure with a prose envelope present -> fall back to the v1 prose
  parser (fail-open).
- Neither parses -> use the original prompt with default intent/scope.
- Never crash, never emit a raw broken JSON blob downstream.
- Always populate ``_last_intent`` and ``_last_scope`` so downstream consumers
  (``base.build_output_suffix``, history-gating) are unaffected by the format.
"""
from __future__ import annotations

from typing import Optional

from prpt.core.spec import SYSTEM_JSON_SPEC, ExecutionSpec, parse_spec_json
from prpt.core.types import RepoMetadata
from prpt.core.utils import write_stderr
from prpt.normalizers.slm_anthropic import (
    _CACHE_MIN_CHARS,
    _HISTORY_INSTRUCTION,
    SLMNormalizer,
    _extract_usage,
    _system_blocks,
    _user_blocks_with_context,
)


class AnthropicSLMNormalizerV2(SLMNormalizer):
    """Claude Haiku normalizer that emits a JSON ExecutionSpec.

    Stores the parsed spec as ``_last_spec`` for callers that want the full
    decision object (routing, target_files, memory_record). Routes
    ``clarify``/``passthrough``/``answer`` are only available on this v2 path.
    """

    # v2 returns a JSON envelope (spec fields + the full rewrite + a memory
    # record), so it needs more headroom than v1's bare rewrite.
    MAX_TOKENS = 1024

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_spec: Optional[ExecutionSpec] = None

    def _rewrite(self, prompt: str, repo: RepoMetadata) -> str:
        self._last_usage = None
        self._last_intent = None
        self._last_scope = None
        self._last_spec = None
        try:
            if self._last_context_block is not None:
                context_block = self._last_context_block
            else:
                # Pass 1: cheap classify to gate context loading (same as v1).
                _, scope = self._classify(prompt)
                context_block = (
                    self._content_loader.build_context_block(prompt, repo, scope=scope)
                    if self._content_loader else ""
                )
                self._last_context_block = context_block

            system_text = SYSTEM_JSON_SPEC
            if "[Recent conversation]" in prompt:
                system_text = system_text + _HISTORY_INSTRUCTION

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
            raw = response.content[0].text if response.content else ""

            # Primary parser: JSON spec.
            spec = parse_spec_json(raw)
            if spec is not None:
                self._last_spec = spec
                self._last_intent = spec.intent
                self._last_scope = spec.scope
                return spec.downstream_prompt or prompt

            # Fall back to the prose parser only if the prose envelope is
            # actually present; otherwise _parse_intent_response would return
            # ("act", "localized", raw_text) and leak a broken blob downstream.
            raw_upper = raw.upper()
            has_prose_envelope = (
                "INTENT:" in raw_upper
                and ("SCOPE:" in raw_upper or "\n---" in raw or raw.startswith("---"))
            )
            if has_prose_envelope:
                write_stderr(
                    "[slm-anthropic-v2] JSON parse failed; falling back to prose parser."
                )
                intent, scope, rewritten = self._parse_intent_response(raw)
                self._last_intent = intent
                self._last_scope = scope
                return rewritten or prompt

            write_stderr(
                "[slm-anthropic-v2] no JSON spec and no prose envelope; "
                "using original prompt with default intent/scope."
            )
            self._last_intent = "act"
            self._last_scope = "localized"
            return prompt

        except Exception as exc:
            write_stderr("[slm-anthropic-v2] rewrite failed ({0}), using original.".format(exc))
            return prompt
