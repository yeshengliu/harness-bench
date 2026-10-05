#!/usr/bin/env python3
"""Concurrency comparison.

Runs the same N tasks two ways -- N isolated owners in parallel, versus one
session processing them serially with an accumulated transcript -- and compares
peak context, speed, and total token cost.

The headline metric is PEAK context, not cumulative tokens. The spec's claim is
that isolation keeps each context bounded; cumulative totals alone would not show
that.

Models with different context windows are compared because the serial mode
degrades with window size, which makes the effect visible rather than theoretical.

Usage:
    python3 concurrency.py
    python3 concurrency.py --tasks 6
    python3 concurrency.py --models <id-a> <id-b> --tasks 6
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from labels import label
from providers.base import get as get_provider            # noqa: E402
from scenarios.concurrency import run_concurrent, run_serial  # noqa: E402

OUT = Path(__file__).parent / "results"

# Real ids: the runner must pass these to the API. Published reports refer to
# models by label -- see the note at the top of labels.py for why the two differ.
DEFAULT_MODELS = ["deepseek-v4.1-flash", "qwen3.8-flash"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--tasks", type=int, default=6)
    ap.add_argument("--budget", type=int, default=700)
    args = ap.parse_args()

    rounds: list[dict] = []

    for model in args.models:
        print(f"\n=== {label(model)} ({args.tasks} tasks) ===")
        p = get_provider("deepseek", model)

        print("  serial ...", flush=True)
        try:
            s = run_serial(p, args.tasks, args.budget)
            print(f"    peak_context={s.peak_context} final={s.final_context} "
                  f"total_context={s.total_context} secs={s.seconds:.1f} "
                  f"failures={s.failures}")
            rounds.append(s.as_dict())
        except Exception as exc:                              # noqa: BLE001
            print(f"    ERROR {type(exc).__name__}: {exc}")
            rounds.append({"mode": "serial", "model": model, "error": str(exc)[:200]})

        print("  concurrent ...", flush=True)
        try:
            c = run_concurrent(p, args.tasks, args.budget)
            print(f"    peak_context={c.peak_context} final={c.final_context} "
                  f"total_context={c.total_context} secs={c.seconds:.1f} "
                  f"failures={c.failures}")
            rounds.append(c.as_dict())
        except Exception as exc:                              # noqa: BLE001
            print(f"    ERROR {type(exc).__name__}: {exc}")
            rounds.append({"mode": "concurrent", "model": model, "error": str(exc)[:200]})

    write(rounds, args)
    return 0


def write(rounds: list[dict], args) -> None:
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    (OUT / "concurrency.json").write_text(json.dumps(
        {"ran_at": stamp, "tasks": args.tasks, "rounds": rounds}, indent=2))

    lines = [
        "# Concurrency results",
        "",
        f"Ran {stamp}. {args.tasks} tasks per mode. Budget {args.budget} tokens/call.",
        "",
        "Same tasks and payload in both modes. `serial` is one session carrying its",
        "transcript forward; `concurrent` is N owners in isolated contexts.",
        "",
        "**Peak context** is the largest prompt the provider reported for any single",
        "call. It is the metric that matters: cumulative tokens can be higher while",
        "every individual context stays small.",
        "",
        "| Model | Mode | Peak ctx | Final ctx | Total ctx | Calls | Secs | Fail |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rounds:
        if "error" in r:
            lines.append(f"| {r['model']} | {r['mode']} | ERROR | | | | | "
                         f"{r['error'][:60]} |")
            continue
        lines.append(
            f"| {r['model']} | {r['mode']} | {r['peak_context']} | "
            f"{r['final_context']} | {r['total_context']} | {r['calls']} | "
            f"{r['seconds']} | {r['failures']} |")

    lines += ["", "## Per-call context growth", ""]
    for r in rounds:
        if "error" in r:
            continue
        peaks = ", ".join(str(x) for x in r["per_task_peak"])
        lines.append(f"- **{r['model']} / {r['mode']}**: [{peaks}]")
        if r["mode"] == "serial" and len(r["per_task_peak"]) > 1:
            growth = r["per_task_peak"][-1] / max(1, r["per_task_peak"][0])
            lines.append(f"  - growth from first to last call: **{growth:.1f}x**")

    lines += [
        "",
        "## Limits",
        "",
        "- Same author as the harness under test. A mechanism check, not an",
        "  independent benchmark.",
        "- `serial` is a deliberately naive single-session baseline: it re-sends the",
        "  full transcript with no compaction. A production single-agent harness would",
        "  compact, which would reduce the growth shown here. The comparison is",
        "  therefore against the unmanaged case, not against a well-built single agent.",
        "- Task count and document size are fixed; neither is varied to find the",
        "  threshold at which serial mode hits a window limit.",
        "",
    ]
    (OUT / "CONCURRENCY.md").write_text("\n".join(lines))
    print(f"\nwrote {OUT/'concurrency.json'} and {OUT/'CONCURRENCY.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
