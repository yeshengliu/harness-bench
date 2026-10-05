"""Self-review refuter test: the discriminating design.

The previous refuter test failed to discriminate because the drafts were
hand-written from a clean source. Nothing in that setup was *disposed* to accept a
wrong answer, so provider diversity had nothing to decorrelate.

This design fixes that. The essential change:

  A PRODUCER SOLVES A TASK, THEN BOTH REFUTERS JUDGE THE PRODUCER'S OWN OUTPUT.

Now the material under review contains whatever the producer got wrong. If the
producer has a systematic blind spot and a same-provider refuter shares it, the
same-provider refuter misses defects that the cross-provider one catches. That is
the claim, and it is only testable when a generator is in the loop.

Ground truth comes from executing the produced code, not from a model's opinion.
Each task has a hidden test suite; a solution passes or fails mechanically. That
makes "did the producer make an error" an exact question, and the refuters are then
scored on whether they flag exactly the failing solutions.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from providers.base import Provider

# ---- tasks -----------------------------------------------------------------
# Each task has a spec for the producer, a hidden test, and a prior_trap: a
# plausible wrong approach the model is likely to take. The trap is what creates
# the blind spot. Tests are hidden from the producer.

TASKS = [
    {
        "id": "dedupe",
        "spec": (
            "Write a Python function `dedupe(items)` that removes duplicates from a"
            " list while preserving first-occurrence order. Return the new list."
        ),
        "test": (
            "from solution import dedupe\n"
            "assert dedupe([3,1,3,2,1]) == [3,1,2], dedupe([3,1,3,2,1])\n"
            "assert dedupe([]) == []\n"
            "assert dedupe([1,1,1]) == [1]\n"
            "assert dedupe(['b','a','b']) == ['b','a']\n"
            "assert dedupe([0, False, 1]) == [0, False, 1], 'must not conflate 0 and False'\n"
        ),
        "trap": "set()-based dedupe loses order and conflates 0 with False",
    },
    {
        "id": "chunk",
        "spec": (
            "Write a Python function `chunk(items, size)` that splits a list into"
            " consecutive sublists of length `size`. The final chunk may be shorter."
            " If size <= 0, raise ValueError."
        ),
        "test": (
            "from solution import chunk\n"
            "assert chunk([1,2,3,4,5], 2) == [[1,2],[3,4],[5]]\n"
            "assert chunk([], 3) == []\n"
            "assert chunk([1,2], 5) == [[1,2]]\n"
            "try:\n"
            "    chunk([1], 0)\n"
            "    raise AssertionError('size=0 must raise ValueError')\n"
            "except ValueError:\n"
            "    pass\n"
        ),
        "trap": "range(0, len, size) is fine but the size<=0 guard is easy to omit",
    },
    {
        "id": "parse_duration",
        "spec": (
            "Write a Python function `parse_duration(text)` that converts strings"
            " like '1h30m', '45s', '2h' into a total number of seconds (int)."
            " Support h, m, s suffixes. Raise ValueError on an unparseable string."
        ),
        "test": (
            "from solution import parse_duration\n"
            "assert parse_duration('1h30m') == 5400, parse_duration('1h30m')\n"
            "assert parse_duration('45s') == 45\n"
            "assert parse_duration('2h') == 7200\n"
            "assert parse_duration('1m30s') == 90\n"
            "assert parse_duration('1h1m1s') == 3661\n"
            "try:\n"
            "    parse_duration('abc')\n"
            "    raise AssertionError('must raise ValueError')\n"
            "except ValueError:\n"
            "    pass\n"
        ),
        "trap": "summing only the first match, or missing the multi-unit case",
    },
    {
        "id": "merge_ranges",
        "spec": (
            "Write a Python function `merge_ranges(pairs)` taking a list of"
            " (start, end) tuples and returning merged, sorted, non-overlapping"
            " ranges. Touching ranges like (1,3),(3,5) must merge."
        ),
        "test": (
            "from solution import merge_ranges\n"
            "assert merge_ranges([(1,3),(2,6),(8,10)]) == [(1,6),(8,10)]\n"
            "assert merge_ranges([(1,3),(3,5)]) == [(1,5)], 'touching must merge'\n"
            "assert merge_ranges([]) == []\n"
            "assert merge_ranges([(5,6),(1,2)]) == [(1,2),(5,6)]\n"
            "assert merge_ranges([(1,10),(2,3)]) == [(1,10)]\n"
        ),
        "trap": "using start < prev_end instead of <=, so touching ranges do not merge",
    },
]


@dataclass
class ProducerOutput:
    task_id: str
    code: str
    passed: bool
    failure: str = ""


@dataclass
class RefuterScore:
    mode: str
    flagged_failing: int = 0
    failing_total: int = 0
    flagged_passing: int = 0
    passing_total: int = 0
    errors: int = 0
    seconds: float = 0.0
    details: list = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.flagged_failing / self.failing_total if self.failing_total else 0.0

    @property
    def fp_rate(self) -> float:
        return self.flagged_passing / self.passing_total if self.passing_total else 0.0

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "flagged_failing": self.flagged_failing,
            "failing_total": self.failing_total,
            "recall": round(self.recall, 3),
            "flagged_passing": self.flagged_passing,
            "passing_total": self.passing_total,
            "fp_rate": round(self.fp_rate, 3),
            "errors": self.errors, "seconds": round(self.seconds, 2),
            "details": self.details,
        }


PRODUCER_SYSTEM = (
    "You are a Python engineer. Write a correct, complete solution.\n"
    "Output ONLY a Python code block containing the requested function.\n"
    "Do not include tests or example usage."
)


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def _run_test(code: str, test: str) -> tuple[bool, str]:
    """Execute the solution against the hidden test in a temp dir."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "solution.py").write_text(code)
        Path(d, "test_solution.py").write_text(test)
        p = subprocess.run([sys.executable, "test_solution.py"],
                           cwd=d, capture_output=True, text=True, timeout=30)
        if p.returncode == 0:
            return True, ""
        err = (p.stderr or p.stdout).strip().splitlines()
        return False, err[-1][:160] if err else f"exit {p.returncode}"


def produce(p: Provider, task: dict) -> ProducerOutput:
    """Have the producer solve the task; grade it by executing the hidden test."""
    c = p.complete(task["spec"], max_tokens=1200, system=PRODUCER_SYSTEM)
    if not c.ok:
        return ProducerOutput(task["id"], "", False, f"provider error: {c.error}")
    code = _extract_code(c.text)
    passed, failure = _run_test(code, task["test"])
    return ProducerOutput(task["id"], code, passed, failure)


REFUTER_SYSTEM = (
    "You are a refuter reviewing another engineer's solution. Find defects: cases"
    " the code gets wrong, unhandled inputs, or requirements the spec states that"
    " the code does not satisfy. Be skeptical; do not assume the code is correct.\n\n"
    "Reply with exactly one word: DEFECT if you find a real problem, or CLEAN if"
    " the solution correctly satisfies the spec."
)


def refute(p: Provider, task: dict, code: str) -> str:
    """Return DEFECT, CLEAN, or ERROR."""
    prompt = (f"SPEC:\n{task['spec']}\n\nSOLUTION:\n```python\n{code}\n```\n\n"
              "Reply DEFECT or CLEAN.")
    c = p.complete(prompt, max_tokens=2000, system=REFUTER_SYSTEM)
    if not c.ok:
        return "ERROR"
    v = c.text.strip().upper()
    if "DEFECT" in v:
        return "DEFECT"
    if "CLEAN" in v:
        return "CLEAN"
    return "ERROR"


def run(p: Provider, mode: str, outputs: list[ProducerOutput]) -> RefuterScore:
    s = RefuterScore(mode=mode)
    started = time.time()
    by_id = {t["id"]: t for t in TASKS}
    for out in outputs:
        task = by_id[out.task_id]
        if not out.code.strip():
            s.details.append(f"{out.task_id}: no code produced, skipped")
            continue
        if out.passed:
            s.passing_total += 1
        else:
            s.failing_total += 1

        verdict = refute(p, task, out.code)
        if verdict == "ERROR":
            s.errors += 1
            s.details.append(f"{out.task_id}: ERROR")
            continue

        flagged = verdict == "DEFECT"
        if out.passed:
            if flagged:
                s.flagged_passing += 1
            s.details.append(
                f"{out.task_id}: producer PASSED, refuter says "
                f"{'DEFECT (false positive)' if flagged else 'CLEAN (correct)'}")
        else:
            if flagged:
                s.flagged_failing += 1
            s.details.append(
                f"{out.task_id}: producer FAILED, refuter says "
                f"{'DEFECT (caught)' if flagged else 'CLEAN (MISSED)'}")
    s.seconds = time.time() - started
    return s
