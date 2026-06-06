"""ExecutionSpec — the typed structure emitted by SLM normalizers in v2.

Replaces the prose `INTENT:/SCOPE:/---/<rewrite>` envelope with a JSON object
that carries the same fields plus future control-plane signals (route,
context_policy, target_files, risk, memory_record).

Failure discipline: every parser path must populate `NormalizedRequest`'s
`normalized_prompt` and the normalizer's `_last_intent` + `_last_scope` so
downstream consumers (`base.build_output_suffix`, `cli.py` history-gating)
remain stable. On JSON parse failure, normalizer falls back to the prose
parser at `slm_anthropic._parse_intent_response`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional

# Valid enum values — kept as plain tuples (no Literal/Enum) so cross-version
# defaults stay easy to maintain and serialize.
_VALID_ROUTE = ("answer", "act", "clarify", "passthrough")
_VALID_INTENT = ("explain", "act")
_VALID_SCOPE = ("pinpoint", "localized", "broad", "new")
_VALID_CONTEXT_POLICY = ("none", "tree", "diff", "changed", "targeted", "full")
_VALID_RISK = ("low", "medium", "high")


@dataclass
class ExecutionSpec:
    route: str = "act"
    intent: str = "act"
    scope: str = "localized"
    needs_history: bool = False
    context_policy: str = "targeted"
    target_files: List[str] = field(default_factory=list)
    risk: str = "low"
    downstream_prompt: str = ""
    memory_record: str = ""

    def normalize_enums(self) -> "ExecutionSpec":
        """Clamp enum-valued fields to their allowed sets; defaults on invalid."""
        if self.route not in _VALID_ROUTE:
            self.route = "act"
        if self.intent not in _VALID_INTENT:
            self.intent = "act"
        if self.scope not in _VALID_SCOPE:
            self.scope = "localized"
        if self.context_policy not in _VALID_CONTEXT_POLICY:
            self.context_policy = "targeted"
        if self.risk not in _VALID_RISK:
            self.risk = "low"
        if not isinstance(self.needs_history, bool):
            self.needs_history = bool(self.needs_history)
        if not isinstance(self.target_files, list):
            self.target_files = []
        else:
            # Only keep string entries
            self.target_files = [f for f in self.target_files if isinstance(f, str)]
        return self


def parse_spec_json(raw_text: str) -> Optional[ExecutionSpec]:
    """Attempt to parse raw SLM output as a JSON ExecutionSpec.

    Returns None when the text doesn't contain valid JSON (caller falls back
    to the prose parser). The JSON object must at least contain
    `downstream_prompt`; other fields are optional and get defaults.

    Robust to fenced ```json blocks and bare {...} prefixes/suffixes via
    `prpt.judges.extract_json`.
    """
    from prpt.judges import extract_json

    obj = extract_json(raw_text)
    if not isinstance(obj, dict):
        return None
    if "downstream_prompt" not in obj or not isinstance(obj.get("downstream_prompt"), str):
        return None
    spec = ExecutionSpec(
        route=str(obj.get("route", "act")),
        intent=str(obj.get("intent", "act")),
        scope=str(obj.get("scope", "localized")),
        needs_history=bool(obj.get("needs_history", False)),
        context_policy=str(obj.get("context_policy", "targeted")),
        target_files=obj.get("target_files", []) or [],
        risk=str(obj.get("risk", "low")),
        downstream_prompt=str(obj.get("downstream_prompt", "")),
        memory_record=str(obj.get("memory_record", "")),
    )
    return spec.normalize_enums()


def spec_to_dict(spec: ExecutionSpec) -> dict:
    return asdict(spec)


# ---------------------------------------------------------------------------
# Shared v2 system prompt
# ---------------------------------------------------------------------------
# The JSON-spec instruction is backend-agnostic: both the OpenAI (slm-openai-v2)
# and Anthropic (slm-anthropic-v2) v2 normalizers send it verbatim and parse the
# reply with parse_spec_json(). Keeping a single source here prevents the two
# backends from drifting apart on routing semantics (answer/act/clarify/...).
SYSTEM_JSON_SPEC = (
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
    "- When route is 'clarify', put the clarifying question(s) in "
    "downstream_prompt: at most 3 short, specific questions (or terse "
    "'A vs B vs C' option lists) covering the missing facts. Keep it scannable "
    "-- no lengthy parenthetical examples. Never leave downstream_prompt empty.\n"
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
