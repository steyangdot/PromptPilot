"""All static constants, patterns, and pricing tables."""
from __future__ import annotations

# --- Prompt parsing ---

PROTECTED_PATTERNS = [
    r"\bminimal patch(?: only)?\b",
    r"\bno refactor\b",
    r"\bbackward compatible\b",
    r"\bno schema changes?\b",
    r"\bno schema migration\b",
    r"\bpreserve API\b",
    r"\bread-only\b",
    r"\bdo not touch\s+[A-Za-z0-9_./:-]+",
    r"\bdo not change behavior\b",
    r"\broot cause\b",
]

HARD_CONSTRAINT_HINTS = [
    "must", "must not", "do not", "avoid", "only", "exactly", "preserve",
    "no downtime", "backward compatible", "read-only", "minimal patch",
    "no schema", "do not touch", "do not change",
]

REQUESTED_OUTPUT_HINTS = [
    "explain", "root cause", "provide patch", "diff", "tests",
    "step-by-step", "plan", "list", "summary",
]

CLAUSE_SPLIT_PATTERN = r"[,;]|\n|\band\b"
PREFERENCE_SPLIT_PATTERN = r"[,;]|\n"

# --- Repo detection ---

LANGUAGE_MARKERS: dict = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".java": "Java", ".go": "Go", ".rs": "Rust", ".cs": "C#", ".cpp": "C++",
    ".c": "C", ".rb": "Ruby", ".php": "PHP", ".kt": "Kotlin", ".swift": "Swift",
}

TEST_FRAMEWORK_HINTS: dict = {
    "pytest": "pytest", "vitest": "vitest", "jest": "jest", "mocha": "mocha",
    "xunit": "xUnit", "nunit": "NUnit", "junit": "JUnit",
}

# --- Pricing: USD per million tokens (input, output) ---

MODEL_PRICING: dict = {
    # Anthropic - Haiku
    "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00},
    # Anthropic - Sonnet
    "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4-7":         {"input": 3.00,  "output": 15.00},
    # Anthropic - Opus
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
    "claude-opus-4-7":           {"input": 15.00, "output": 75.00},
    # OpenAI - GPT-5.4 family (current gen, 2026). Public API list rates;
    # verify before invoicing.
    "gpt-5.4-nano":              {"input": 0.20,  "output": 1.25},
    "gpt-5.4-mini":              {"input": 0.75,  "output": 4.50},
    "gpt-5.4":                   {"input": 2.50,  "output": 10.00},
    "gpt-5.5":                   {"input": 5.00,  "output": 30.00},
    # OpenAI - legacy
    "gpt-4o-mini":               {"input": 0.15,  "output": 0.60},
    "gpt-4o-mini-2024-07-18":    {"input": 0.15,  "output": 0.60},
    "gpt-4o":                    {"input": 2.50,  "output": 10.00},
    "gpt-4o-2024-08-06":         {"input": 2.50,  "output": 10.00},
    "o3":                        {"input": 10.00, "output": 40.00},
    "o4-mini":                   {"input": 1.10,  "output": 4.40},
}

# Prompt-cache multipliers (Anthropic ephemeral cache)
CACHE_WRITE_MULTIPLIER = 1.25   # cache creation billed at 1.25x input
CACHE_READ_MULTIPLIER  = 0.10   # cache reads billed at 0.10x input

DEFAULT_TARGET_MODEL = "claude-opus-4-7"
# Direct OpenAI SDK calls default to the current published GPT-5.5
# Chat Completions model; see tests for the compatibility guard.
DEFAULT_OPENAI_TARGET_MODEL = "gpt-5.5"
DEFAULT_SLM_ANTHROPIC = "claude-haiku-4-5-20251001"
DEFAULT_SLM_OPENAI = "gpt-5.4-nano"
DEFAULT_LOG_FILE = ".promptpilot_runs.jsonl"

HELP_TEXT = (
    "No prompt provided.\n"
    "Usage examples:\n"
    "  prpt --dry-run \"fix flaky test in payments\"\n"
    "  prpt --normalizer slm \"fix timeout in OrderSyncWorker\"\n"
    "  prpt --normalizer slm --tool anthropic --model claude-opus-4-7 \"add dark mode\"\n"
    "  prpt --normalizer slm --tool anthropic --model claude-sonnet-4-7 \"refactor auth\"\n"
    "  prpt install-hook          # wire into Claude Code automatically\n"
)
