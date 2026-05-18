"""SLM normalizer v2 — emits a JSON ExecutionSpec instead of the prose
`INTENT:/SCOPE:/---/<rewrite>` envelope.

Inherits from OpenAISLMNormalizer; overrides only the rewrite path. The
classify/answer/suggest/referential paths are unchanged from v1.

Failure discipline (per the v2 roadmap parser-migration guardrails):
- JSON parse failure -> fall back to v1 prose parser (fail-open)
- Neither parses    -> use heuristic defaults (`intent=act, scope=localized`)
- Never crash, never emit raw broken JSON downstream
- Always populate `_last_intent`, `_last_scope`, and the returned downstream
  prompt so consumers (`base.build_output_suffix`, chain harness scorer) are
  unaffected by the format change.
"""
from __future__ import annotations

import os
from typing import Optional

from promptpilot.core.spec import ExecutionSpec, parse_spec_json
from promptpilot.core.types import RepoMetadata
from promptpilot.core.utils import write_stderr
from promptpilot.normalizers.slm_openai import OpenAISLMNormalizer


_SYSTEM_JSON_SPEC = (
    "You are a prompt optimizer for AI coding assistants.\n\n"
    "Given a developer's raw coding task prompt (and optional repository "
    "context), output a single JSON object describing both how to route the "
    "request and the rewritten prompt to send downstream.\n\n"
    "Schema (emit JSON only -- no preamble, no fences, no commentary):\n"
    "{\n"
    '  "route":           "answer | act | clarify | passthrough",\n'
    '  "intent":          "explain | act",\n'
    '  "scope":           "pinpoint | localized | broad | new",\n'
    '  "needs_history":   true | false,\n'
    '  "context_policy":  "none | tree | diff | changed | targeted | full",\n'
    '  "target_files":    ["path/one.py", "path/two.py"],\n'
    '  "risk":            "low | medium | high",\n'
    '  "downstream_prompt": "<the rewritten prompt to send to the downstream coding agent>",\n'
    '  "memory_record":   "<one short sentence summarizing intent + constraints for future turns>"\n'
    "}\n\n"
    "Field guidance:\n"
    "- route: pick 'answer' if you can fully answer from context (explanations); "
    "'act' if a code change is needed; 'clarify' if the prompt is underspecified "
    "and asking the user is cheaper than guessing; 'passthrough' if rewriting "
    "is risky (highly specific, already-precise prompts).\n"
    "- intent: 'explain' for understanding questions, 'act' for code changes.\n"
    "- scope: surgical (pinpoint) -> 50%+ of a file (broad) -> new files (new).\n"
    "- context_policy: how much repo context the downstream agent needs to do "
    "the job. Default 'targeted' (specific files only).\n"
    "- target_files: relative paths the downstream agent will likely need.\n"
    "- risk: 'high' if change touches public API, security, schemas, or large "
    "blast radius.\n"
    "- downstream_prompt: the actual rewritten prompt -- precise, unambiguous, "
    "preserves identifiers and hard constraints exactly. No commentary.\n"
    "- memory_record: a single sentence describing what the user wanted and "
    "what constraints applied, for future referential turns.\n\n"
    "Rules:\n"
    "- Output ONE valid JSON object and nothing else.\n"
    "- Never invent requirements not present in the original.\n"
    "- Preserve identifiers, file names, technical terms verbatim.\n"
    "- Keep hard constraints verbatim (e.g. 'do not touch X')."
)


class OpenAISLMNormalizerV2(OpenAISLMNormalizer):
    """Emits a JSON ExecutionSpec; falls back to prose parser on failure.

    Stores the parsed spec as `_last_spec` for callers that want the full
    decision object (routing, target_files, memory_record).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_spec: Optional[ExecutionSpec] = None

    def _rewrite(self, prompt: str, repo: RepoMetadata) -> str:
        from promptpilot.normalizers.slm_anthropic import SLMNormalizer as _AnthropicNorm

        self._last_usage = None
        self._last_intent = None
        self._last_scope = None
        self._last_spec = None
        try:
            if self._last_context_block is not None:
                context_block = self._last_context_block
            else:
                # Pass 1: cheap classify to gate context loading (same as v1)
                _, scope = self._classify(prompt)
                context_block = (
                    self._content_loader.build_context_block(prompt, repo, scope=scope)
                    if self._content_loader else ""
                )
                self._last_context_block = context_block

            if context_block:
                user_content = (
                    "<repository_context>\n{0}\n</repository_context>\n\n"
                    "<developer_prompt>\n{1}\n</developer_prompt>"
                ).format(context_block, prompt)
            else:
                user_content = prompt

            response = self._client.chat.completions.create(
                model=self.MODEL,
                max_completion_tokens=self.MAX_TOKENS,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_JSON_SPEC},
                    {"role": "user", "content": user_content},
                ],
            )
            self._last_usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
            raw = response.choices[0].message.content or ""

            # TEMP diagnostic: when PROMPTPILOT_V2_RAW_LOG=1, capture every
            # call's user_content + raw response + finish_reason so failed
            # turns can be inspected post-hoc. Safe to leave on (best-effort,
            # no exceptions surface). Remove after v2 stabilizes.
            if os.environ.get("PROMPTPILOT_V2_RAW_LOG") == "1":
                import json as _json, time as _time
                from pathlib import Path as _Path
                try:
                    p = _Path.home() / ".promptpilot" / "v2_slm_raw.jsonl"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with p.open("a", encoding="utf-8") as fh:
                        fh.write(_json.dumps({
                            "ts": _time.time(),
                            "user_content_len": len(user_content),
                            "user_content_tail": user_content[-1500:],
                            "raw": raw,
                            "raw_len": len(raw),
                            "finish_reason": response.choices[0].finish_reason,
                            "in_tok": response.usage.prompt_tokens,
                            "out_tok": response.usage.completion_tokens,
                        }) + "\n")
                except Exception:
                    pass

            # Primary parser: JSON spec
            spec = parse_spec_json(raw)
            if spec is not None:
                self._last_spec = spec
                self._last_intent = spec.intent
                self._last_scope = spec.scope
                return spec.downstream_prompt or prompt

            # Fall back to prose parser only if the prose envelope is actually
            # present. Otherwise _parse_intent_response returns
            # ("act", "localized", raw_text) -- the line-302 footgun called out
            # by parser-migration guardrails -- which would leak the raw broken
            # blob downstream. When neither parser sees structure, use the
            # original prompt (fail open, but to a safe value).
            raw_upper = raw.upper()
            has_prose_envelope = (
                "INTENT:" in raw_upper
                and ("SCOPE:" in raw_upper or "\n---" in raw or raw.startswith("---"))
            )
            if has_prose_envelope:
                write_stderr(
                    "[slm-openai-v2] JSON parse failed; falling back to prose parser."
                )
                intent, scope, rewritten = _AnthropicNorm._parse_intent_response(raw)
                self._last_intent = intent
                self._last_scope = scope
                return rewritten or prompt

            write_stderr(
                "[slm-openai-v2] no JSON spec and no prose envelope; "
                "using original prompt with default intent/scope."
            )
            # Guardrail: every parser path must populate _last_intent +
            # _last_scope (build_output_suffix and history-gating depend on
            # them). Use the same safe defaults the prose fallback uses.
            self._last_intent = "act"
            self._last_scope = "localized"
            return prompt

        except Exception as exc:
            write_stderr("[slm-openai-v2] rewrite failed ({0}), using original.".format(exc))
            return prompt
