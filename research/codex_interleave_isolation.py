"""Codex WITH-vs-slm_native clean isolation, INTERLEAVED in one quota window.

Removes the cross-timing confound found in the 2026-06-07 re-test (where codex
WITH ran in the morning and slm_native in an evening resume, ~5h apart across a
quota reset). By interleaving (WITH r1, slm_native r1, WITH r2, ...), if the
ChatGPT quota caps mid-way BOTH arms have ~equal runs from the SAME window.

SLM = slm-openai (gpt-5.4-nano, DEFAULT_SLM_OPENAI) via the OpenAI API key —
runs OFF the ChatGPT quota, so only the gpt-5.5 AGENT consumes ChatGPT quota.
This is the realistic same-provider codex hybrid (gpt-5.4-nano SLM + gpt-5.5
agent), vs the prior run's cross-provider Claude-SLM + codex-agent.

Env: PROMPTPILOT_OUT_DIR (out base), ISO_RUNS (default 5). Needs OPENAI_API_KEY
(.env) for the SLM and codex ChatGPT auth for the agent.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chain_test_v2 as C  # noqa: E402  (runs dotenv load + DISABLE_MICROCOMPACT scrub at import)

# Force the OpenAI-API SLM (gpt-5.4-nano default) — off the ChatGPT quota.
C._NORMALIZER_NAME = "slm-openai"


def _run_one_variant(chain, tool, variant, r, out_dir, use_builtin):
    prev = os.environ.get("USE_BUILTIN_SESSION")
    if use_builtin:
        os.environ["USE_BUILTIN_SESSION"] = "1"
    else:
        os.environ.pop("USE_BUILTIN_SESSION", None)
    try:
        res = C.run_chain_once(chain, tool, variant, r, out_dir)
        C.save_run(out_dir, variant, r, res)
        return True
    finally:
        if prev is None:
            os.environ.pop("USE_BUILTIN_SESSION", None)
        else:
            os.environ["USE_BUILTIN_SESSION"] = prev


def main():
    n_runs = int(os.environ.get("ISO_RUNS", "5"))
    tool = "codex"
    chain = next(c for c in C.CHAINS if c["id"] == "chain1")
    out_dir = C.OUT_DIR / tool / chain["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[interleave] start: WITH + slm_native INTERLEAVED, N={n_runs}, tool={tool}, "
          f"SLM={C._NORMALIZER_NAME} (gpt-5.4-nano), out={out_dir}", flush=True)
    done = {"with_session": 0, "slm_native": 0}
    try:
        for r in range(1, n_runs + 1):
            # Resume: skip a round whose BOTH per-run files already exist (lets a
            # quota-capped run be re-triggered after reset and continue from where
            # it stopped; WITH_r and slm_native_r remain an adjacent matched pair).
            wf = out_dir / f"with_session_run{r}.json"
            sf = out_dir / f"slm_native_run{r}.json"
            if wf.exists() and sf.exists():
                done["with_session"] += 1; done["slm_native"] += 1
                print(f"[interleave] round {r}/{n_runs} already complete -> skip", flush=True)
                continue
            # WITH = SLM rewrite + bounded session (no native resume)
            _run_one_variant(chain, tool, "with_session", r, out_dir, use_builtin=False)
            done["with_session"] += 1
            # slm_native = SLM rewrite + native resume (no bounded session)
            _run_one_variant(chain, tool, "slm_native", r, out_dir, use_builtin=True)
            done["slm_native"] += 1
            print(f"[interleave] round {r}/{n_runs} complete: "
                  f"with={done['with_session']} slm_native={done['slm_native']}", flush=True)
    except C.QuotaExhausted as e:
        print(f"[interleave] QUOTA EXHAUSTED: {e}", flush=True)
        print(f"[interleave] completed (same-window) rounds: "
              f"with={done['with_session']} slm_native={done['slm_native']}", flush=True)
    print(f"[interleave] DONE: {done}", flush=True)


if __name__ == "__main__":
    main()
