"""SLM normalizer v2 (subscription) — emits a JSON ExecutionSpec via the judge.

The Max-OAuth / ChatGPT-subscription sibling of ``slm-anthropic-v2`` /
``slm-openai-v2``. It drives the same shared ``SYSTEM_JSON_SPEC`` prompt through
the Judge subprocess (``claude -p`` / ``codex exec``) and parses the reply with
``parse_spec_json``, so the routing decision (answer / act / **clarify** /
passthrough) works on the subscription default — not only the API-key paths.

Failure discipline is identical to the other v2 normalizers:
- JSON parse failure with a prose envelope present -> v1 prose parser.
- Neither parses (or the judge returns empty) -> original prompt with default
  intent/scope.
- Never crash; never forward a raw broken JSON blob.
"""
from __future__ import annotations

from typing import Optional

from prpt.core.spec import SYSTEM_JSON_SPEC, ExecutionSpec, parse_spec_json
from prpt.core.types import RepoMetadata
from prpt.core.utils import log_v2_raw, write_stderr
from prpt.normalizers.slm_anthropic import (
    _HISTORY_INSTRUCTION,
    SLMNormalizer as _AnthropicNorm,
)
from prpt.normalizers.slm_subscription import SubscriptionSLMNormalizer


class SubscriptionSLMNormalizerV2(SubscriptionSLMNormalizer):
    """Subscription normalizer that emits a JSON ExecutionSpec.

    Inherits the judge plumbing, classify/answer/suggest/referential paths, and
    token accounting from v1; overrides only the rewrite path. Stores the parsed
    spec as ``_last_spec``.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_spec: Optional[ExecutionSpec] = None

    def _rewrite(self, prompt: str, repo: RepoMetadata) -> str:
        self._reset_usage()
        self._last_intent = None
        self._last_scope = None
        self._last_spec = None
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

            system = SYSTEM_JSON_SPEC
            if context_block:
                user_content = (
                    "<repository_context>\n{0}\n</repository_context>\n\n"
                    "<developer_prompt>\n{1}\n</developer_prompt>"
                ).format(context_block, prompt)
            else:
                user_content = prompt
            if "[Recent conversation]" in prompt:
                system = system + _HISTORY_INSTRUCTION

            text = self._ask(system, user_content, cwd=repo.cwd)
            log_v2_raw(
                "subscription-v2", text,
                input_chars=self._last_input_chars,
                output_chars=self._last_output_chars,
            )
            if not text:
                write_stderr(
                    "[slm-subscription-v2] judge returned empty, using original prompt."
                )
                self._last_intent = "act"
                self._last_scope = "localized"
                return prompt

            # Primary parser: JSON spec.
            spec = parse_spec_json(text)
            if spec is not None:
                self._last_spec = spec
                self._last_intent = spec.intent
                self._last_scope = spec.scope
                return spec.downstream_prompt or prompt

            # Fall back to the prose parser only if the envelope is present.
            raw_upper = text.upper()
            has_prose_envelope = (
                "INTENT:" in raw_upper
                and ("SCOPE:" in raw_upper or "\n---" in text or text.startswith("---"))
            )
            if has_prose_envelope:
                write_stderr(
                    "[slm-subscription-v2] JSON parse failed; falling back to prose parser."
                )
                intent, scope, rewritten = _AnthropicNorm._parse_intent_response(text)
                self._last_intent = intent
                self._last_scope = scope
                return rewritten or prompt

            write_stderr(
                "[slm-subscription-v2] no JSON spec and no prose envelope; "
                "using original prompt with default intent/scope."
            )
            self._last_intent = "act"
            self._last_scope = "localized"
            return prompt

        except Exception as exc:
            write_stderr(
                "[slm-subscription-v2] rewrite failed ({0}), using original.".format(exc)
            )
            return prompt
