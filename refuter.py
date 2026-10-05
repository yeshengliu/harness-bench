#!/usr/bin/env python3
"""Cross-provider refuter comparison.

Compares a same-provider refuter against a cross-provider one on drafts with
mechanically planted defects.

Usage:
    python3 refuter.py
    python3 refuter.py --cross-model gpt-5.6-sol
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from providers.base import get as get_provider            # noqa: E402
from scenarios.refuter import run_refuter                 # noqa: E402

OUT = Path(__file__).parent / "results"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--producer-model", default="deepseek-v4.1-flash")
    ap.add_argument("--cross-model", default="gpt-5.6-sol")
    ap.add_argument("--same-provider", default="deepseek")
    ap.add_argument("--cross-provider", default="codex")
    args = ap.parse_args()

    results = []

    print(f"producer: {args.same_provider}/{args.producer_model}")
    prod = get_provider(args.same_provider, args.producer_model)

    print(f"\n[same-provider] refuter = {args.same_provider}/{args.producer_model}")
    try:
        same = run_refuter(prod, prod, "same-provider")
        print(f"  detection={same.caught}/{same.defective} "
              f"({same.detection_rate:.0%})  false_positives="
              f"{same.false_positives}/{same.clean} "
              f"({same.false_positive_rate:.0%})  errors={same.errors} "
              f"secs={same.seconds:.1f}")
        for d in same.details:
            print(f"    {d}")
        results.append(same.as_dict())
    except Exception as exc:                                  # noqa: BLE001
        print(f"  ERROR {type(exc).__name__}: {exc}")
        results.append({"mode": "same-provider", "error": str(exc)[:200]})

    print(f"\n[cross-provider] refuter = {args.cross_provider}/{args.cross_model}")
    try:
        cross = get_provider(args.cross_provider, args.cross_model)
        cross_run = run_refuter(prod, cross, "cross-provider")
        print(f"  detection={cross_run.caught}/{cross_run.defective} "
              f"({cross_run.detection_rate:.0%})  false_positives="
              f"{cross_run.false_positives}/{cross_run.clean} "
              f"({cross_run.false_positive_rate:.0%})  errors={cross_run.errors} "
              f"secs={cross_run.seconds:.1f}")
        for d in cross_run.details:
            print(f"    {d}")
        results.append(cross_run.as_dict())
    except Exception as exc:                                  # noqa: BLE001
        print(f"  ERROR {type(exc).__name__}: {exc}")
        results.append({"mode": "cross-provider", "error": str(exc)[:200]})

    write(results, args)
    return 0


def write(results: list[dict], args) -> None:
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    (OUT / "refuter.json").write_text(json.dumps(
        {"ran_at": stamp, "producer": args.producer_model, "results": results},
        indent=2))

    lines = [
        "# Cross-provider refuter results",
        "",
        f"Ran {stamp}. Producer `{args.producer_model}`.",
        "",
        "Drafts contain mechanically planted value substitutions, so ground truth is",
        "exact. Both detection rate and false-positive rate are reported: a refuter",
        "that rejects everything scores perfectly on detection alone.",
        "",
        "| Refuter | Detected | Detection | False pos | FP rate | Errors | Secs |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['mode']} | ERROR | | | | | {r['error'][:50]} |")
            continue
        lines.append(
            f"| {r['mode']} | {r['caught']}/{r['defective']} | "
            f"{r['detection_rate']:.0%} | {r['false_positives']}/{r['clean']} | "
            f"{r['false_positive_rate']:.0%} | {r['errors']} | {r['seconds']} |")

    lines += ["", "## Per-draft verdicts", ""]
    for r in results:
        if "error" in r:
            continue
        lines.append(f"**{r['mode']}**")
        for d in r.get("details", []):
            lines.append(f"- {d}")
        lines.append("")

    lines += [
        "## Limits",
        "",
        "- One producer model and one task pattern (value verification against a",
        "  short document). This is a narrow test of a general claim.",
        "- 3 defective and 3 clean drafts. Small n; the rates are indicative, not",
        "  statistical.",
        "- The defects are value substitutions, which are the easiest kind to catch.",
        "  A stronger test would use defects requiring reasoning rather than comparison.",
        "- Same author as the harness under test.",
        "",
    ]
    (OUT / "REFUTER.md").write_text("\n".join(lines))
    print(f"\nwrote {OUT/'refuter.json'} and {OUT/'REFUTER.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
