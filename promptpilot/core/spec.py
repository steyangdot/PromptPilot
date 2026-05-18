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
    `promptpilot.judges.extract_json`.
    """
    from promptpilot.judges import extract_json

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
