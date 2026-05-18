"""Write B:/LLM/SESSION_STATUS.md with current chain4 N=10 progress.

Runs in a 30s loop. Reads the chain_results_v2_opus/chain4 directory and any
running launcher processes; writes a phone-readable Markdown file the local-
files connector can serve to remote Claude.

    python status_watcher.py            # run once and exit
    python status_watcher.py --loop     # keep refreshing every 30s
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
CHAIN_DIR = ROOT / "chain_results_v2_opus" / "claude-code" / "chain4"
STATUS = ROOT / "SESSION_STATUS.md"
TARGET_N = 10


def load_run(p: Path) -> list[dict]:
    return json.loads(p.read_text(encoding="utf-8"))


def variant_progress(variant: str) -> dict:
    files = sorted(CHAIN_DIR.glob(f"{variant}_run*.json"),
                   key=lambda p: int(p.stem.split("run")[-1]))
    runs = [load_run(p) for p in files]
    if not runs:
        return {"n": 0, "files": []}
    means = [statistics.mean(t["score"]["success"] for t in r) for r in runs]
    costs = [sum(t["usage"]["total_cost_usd"] + t.get("slm_cost", 0) for t in r) for r in runs]
    skips = [sum(1 for t in r if t.get("gate_skipped")) for r in runs]
    return {
        "n": len(runs),
        "files": [p.name for p in files],
        "per_run_mean": [round(m, 3) for m in means],
        "per_run_cost": [round(c, 3) for c in costs],
        "per_run_skips": skips,
        "overall_mean": statistics.mean(means),
        "overall_sigma": statistics.stdev(means) if len(means) >= 2 else 0.0,
        "total_cost": sum(costs),
        "total_skips": sum(skips),
        "total_turns": len(runs) * len(runs[0]),
    }


def in_progress_turns(variant: str) -> list[str]:
    """Find run<N>_<variant>_t<i>.json files newer than the matching run<N>.json (=in flight)."""
    out: list[str] = []
    for p in sorted(CHAIN_DIR.glob(f"run*_{variant}_t*.json")):
        run_n = p.name.split("_")[0].replace("run", "")
        roll = CHAIN_DIR / f"{variant}_run{run_n}.json"
        if not roll.exists():
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            out.append(f"{p.name} ({size}B)")
    return out


def _ps_json(cmd: str) -> list[dict]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        body = r.stdout.strip()
        if not body:
            return []
        d = json.loads(body)
        return d if isinstance(d, list) else [d]
    except Exception:
        return []


def claude_processes() -> list[str]:
    rows = _ps_json(
        "Get-Process claude -ErrorAction SilentlyContinue | "
        "Where-Object { $_.StartTime -gt (Get-Date).AddMinutes(-30) } | "
        "Select-Object Id, @{N='Start';E={$_.StartTime.ToString('HH:mm:ss')}}, "
        "@{N='RSSMB';E={[int]($_.WorkingSet64/1MB)}} | ConvertTo-Json -Compress"
    )
    return [f"pid {r['Id']} started {r['Start']} RSS={r['RSSMB']}MB" for r in rows]


def launcher_processes() -> list[str]:
    rows = _ps_json(
        "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'extra_(gated|with_session)_runs' } | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    return [f"pid {r['ProcessId']}: {r['CommandLine']}" for r in rows]


def render() -> str:
    g = variant_progress("gated_session")
    w = variant_progress("with_session")
    inflight_g = in_progress_turns("gated_session")
    inflight_w = in_progress_turns("with_session")
    procs = claude_processes()
    launchers = launcher_processes()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    lines.append(f"# Chain4 N=10 progress (Opus 4.7)\n")
    lines.append(f"**Last refresh:** {now}\n")

    def block(label: str, v: dict, inflight: list[str]) -> None:
        lines.append(f"## {label}")
        lines.append(f"- Runs complete: **{v['n']}/{TARGET_N}**")
        if v["n"]:
            lines.append(f"- Per-run means: `{v['per_run_mean']}`")
            lines.append(f"- Per-run cost:  `{[f'${c}' for c in v['per_run_cost']]}`")
            lines.append(f"- Overall mean: **{v['overall_mean']:.3f}**  sigma={v['overall_sigma']:.3f}")
            lines.append(f"- Total cost so far: **${v['total_cost']:.2f}**")
            if "total_skips" in v and v["total_skips"]:
                lines.append(f"- Gate skips: {v['total_skips']}/{v['total_turns']}")
        if inflight:
            lines.append(f"- In-flight per-turn files: {inflight}")
        lines.append("")

    block("gated_session", g, inflight_g)
    block("with_session", w, inflight_w)

    lines.append("## Live processes")
    if launchers:
        lines.append("Launcher(s):")
        for ln in launchers:
            lines.append(f"  - `{ln}`")
    else:
        lines.append("Launcher: **none running**")
    if procs:
        lines.append(f"Recent claude.exe ({len(procs)}):")
        for p in procs[:5]:
            lines.append(f"  - `{p}`")
    lines.append("")

    g_done = g["n"] >= TARGET_N
    w_done = w["n"] >= TARGET_N
    if g_done and w_done:
        lines.append("## Status: **BOTH ARMS COMPLETE** -- ready to compute final stats.")
    elif g_done:
        lines.append("## Status: gated done, with_session in flight (or queued)")
    elif w_done:
        lines.append("## Status: with_session done, gated in flight")
    else:
        lines.append("## Status: in flight")

    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true",
                    help="Keep refreshing every 30s until interrupted")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()

    while True:
        try:
            STATUS.write_text(render(), encoding="utf-8")
        except Exception as e:
            STATUS.write_text(f"# Watcher error\n\n```\n{e}\n```\n", encoding="utf-8")
        if not args.loop:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
