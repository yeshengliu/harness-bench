#!/usr/bin/env python3
"""Self-review refuter test.

The discriminating design: a producer solves tasks, ground truth comes from
executing hidden tests, and both a same-provider and a cross-provider refuter judge
the producer's own code. Producer blind spots are therefore present in the material
both refuters review.

Usage:
    python3 selfreview.py
    python3 selfreview.py --repeat 3
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
from scenarios.selfreview import TASKS, produce, run       # noqa: E402

OUT = Path(__file__).parent / "results"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--producer-model", default="deepseek-v4.1-flash")
    ap.add_argument("--cross-model", default="gpt-5.6-sol")
    ap.add_argument("--panel", nargs="*", default=None,
                    help="extra cross-provider refuters to test (same endpoint)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="producer attempts per task; more attempts find more failures")
    args = ap.parse_args()

    producer = get_provider("deepseek", args.producer_model)
    same = producer

    print(f"producer: {label(args.producer_model)}, {args.repeat} attempts/task")

    # ---- produce and grade ------------------------------------------
    outputs = []
    print("\n[producing solutions, graded by hidden tests]")
    for task in TASKS:
        for attempt in range(args.repeat):
            out = produce(producer, task)
            status = "PASS" if out.passed else "FAIL"
            print(f"  {task['id']:<16} attempt {attempt+1}  {status}"
                  + (f"  ({out.failure[:60]})" if not out.passed else ""))
            outputs.append(out)

    n_fail = sum(1 for o in outputs if not o.passed and o.code.strip())
    n_pass = sum(1 for o in outputs if o.passed)
    print(f"\n  produced {len(outputs)}: {n_pass} passing, {n_fail} failing")

    if n_fail == 0:
        print("\n  WARNING: the producer passed everything, so there are no defects")
        print("  for a refuter to catch. The test cannot discriminate. Raise")
        print("  --repeat or pick tasks the producer is more likely to get wrong.")

    # ---- refute -----------------------------------------------------
    results = []

    print(f"\n[same-provider refuter = deepseek/{args.producer_model}]")
    s_same = run(same, "same-provider", outputs)
    print_run(s_same)
    results.append(s_same.as_dict())

    print(f"\n[cross-provider refuter = codex/{args.cross_model}]")
    cross = get_provider("codex", args.cross_model)
    s_cross = run(cross, "cross-provider", outputs)
    print_run(s_cross)
    results.append(s_cross.as_dict())

    # Panel: other providers on the SAME endpoint. Tests whether provider
    # diversity alone matters, independent of how strong the reviewer is.
    for model in (args.panel or []):
        print(f"\n[panel refuter = deepseek-endpoint/{model}]")
        try:
            panel = get_provider("deepseek", model)
            s_panel = run(panel, f"panel:{label(model)}", outputs)
            print_run(s_panel)
            results.append(s_panel.as_dict())
        except Exception as exc:                              # noqa: BLE001
            print(f"  ERROR {type(exc).__name__}: {exc}")
            results.append({"mode": f"panel:{model}", "error": str(exc)[:200]})

    write(results, args, n_pass, n_fail)
    return 0


def print_run(s) -> None:
    print(f"  recall={s.flagged_failing}/{s.failing_total} "
          f"({s.recall:.0%})  false_pos={s.flagged_passing}/"
          f"{s.passing_total} ({s.fp_rate:.0%})  "
          f"errors={s.errors}  secs={s.seconds:.1f}")
    for d in s.details:
        print(f"    {d}")


def write(results, args, n_pass, n_fail) -> None:
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    (OUT / "selfreview.json").write_text(json.dumps(
        {"ran_at": stamp, "producer": args.producer_model, "repeat": args.repeat,
         "passing": n_pass, "failing": n_fail, "results": results}, indent=2))

    lines = [
        "# Self-review refuter results",
        "",
        f"Ran {stamp}. Producer `{label(args.producer_model)}`.",
        f"{n_pass} passing and {n_fail} failing solutions from the producer.",
        "",
        "Ground truth is **execution**: every solution was run against a hidden test",
        "suite. A refuter is scored on whether it flags exactly the failing ones.",
        "",
        "| Refuter | Caught | Recall | False pos | FP rate | Errors | Secs |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(
            f"| {r['mode']} | {r['flagged_failing']}/{r['failing_total']} | "
            f"{r['recall']:.0%} | {r['flagged_passing']}/{r['passing_total']} | "
            f"{r['fp_rate']:.0%} | {r['errors']} | {r['seconds']} |")

    lines += ["", "## Per-solution verdicts", ""]
    for r in results:
        lines.append(f"**{r['mode']}**")
        for d in r["details"]:
            lines.append(f"- {d}")
        lines.append("")

    lines += [
        "## Reading this",
        "",
        "- **Recall** is the fraction of genuinely failing solutions the refuter",
        "  flagged. Low recall on the same-provider side, with higher recall cross-",
        "  provider, is the claim under test.",
        "- **FP rate** is how often a correct solution was wrongly flagged. A refuter",
        "  that flags everything would score perfect recall and useless FPs, so both",
        "  numbers must be read together.",
        "- If the producer passed everything, there were no defects to find and the",
        "  test proves nothing. That condition is reported explicitly.",
        "",
        "## The provider-vs-strength confound",
        "",
        "A cross-provider refuter scoring higher than the same-provider one has two",
        "possible explanations:",
        "",
        "1. **Provider diversity.** Different weights make errors uncorrelated.",
        "2. **Reviewer strength.** The cross-provider model is simply better at",
        "   reviewing, regardless of which provider it came from.",
        "",
        "The `panel:` rows test this. They are *different models on the same endpoint",
        "as the producer* — so they add diversity of weights without changing the",
        "provider. Reading the results:",
        "",
        "- If panel models also catch the defect, reviewer strength (or any second",
        "  opinion) explains it, and 'different provider' is not the active ingredient.",
        "- If panel models miss it while the codex model catches it, provider",
        "  diversity is doing real work.",
        "- If panel models are inconsistent, the effect is about model quality and the",
        "  provider framing is not supported.",
        "",
        "## Limits",
        "",
        "- Small n. Two defects in the default run, both from the same task. This is a",
        "  signal, not a measured rate.",
        "- Four tasks, one producer model.",
        "- The tasks target known-common mistakes, which biases toward producing",
        "  failures; that is deliberate, since without failures the test is vacuous.",
        "- Hidden tests check the stated spec, so a solution can be 'correct' in ways",
        "  the test does not measure. Ground truth is the test, not correctness in",
        "  the abstract.",
        "- Codex needs full file access to initialize its app-server, so codex rows",
        "  execute outside the default sandbox.",
        "- Same author as the harness under test.",
        "",
    ]
    (OUT / "SELFREVIEW.md").write_text("\n".join(lines))
    print(f"\nwrote {OUT/'selfreview.json'} and {OUT/'SELFREVIEW.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
