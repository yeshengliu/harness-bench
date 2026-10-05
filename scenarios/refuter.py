"""Cross-provider refuter scenario.

The invariant under test: a refuter running a DIFFERENT provider's model catches
defects that a same-provider refuter misses, because same-provider errors are
correlated with the producer's.

Design. A producer drafts an answer to a task, and we inject a known defect into
some drafts. A refuter then judges each draft. Two refuter configurations are
compared:

  same-provider       refuter and producer are the same model (deepseek)
  cross-provider      refuter is a different provider (codex / gpt-5.6-sol)

The metric is DETECTION RATE on defective drafts plus FALSE POSITIVE RATE on clean
drafts. Reporting only detection would be misleading: a refuter that rejects
everything scores perfectly on detection, so both numbers are required.

Defects are planted mechanically, not by asking a model to make a mistake, so the
ground truth is exact rather than judged.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from providers.base import Provider, Usage

# A factual document with checkable values.
SOURCE = (
    "Service atlas-7 deployment configuration.\n"
    "- retry budget: 4 attempts\n"
    "- canary window: 900 seconds\n"
    "- error threshold: 0.02\n"
    "- owning team: platform-infra\n"
    "- rollback mode: automatic\n"
)

# Defects are value substitutions: the draft states a value contradicting SOURCE.
DEFECTS = [
    ("retry budget", "4", "7"),
    ("canary window", "900", "1200"),
    ("owning team", "platform-infra", "platform-core"),
]
CLEAN_TEMPLATE = (
    "The retry budget is 4 attempts. The canary window is 900 seconds. "
    "The owning team is platform-infra."
)


@dataclass
class RefuterResult:
    mode: str
    drafts: int
    defective: int
    clean: int
    caught: int = 0
    missed: int = 0
    false_positives: int = 0
    correct_clears: int = 0
    seconds: float = 0.0
    errors: int = 0
    details: list = field(default_factory=list)

    @property
    def detection_rate(self) -> float:
        return self.caught / self.defective if self.defective else 0.0

    @property
    def false_positive_rate(self) -> float:
        return self.false_positives / self.clean if self.clean else 0.0

    def as_dict(self) -> dict:
        return {
            "mode": self.mode, "drafts": self.drafts,
            "defective": self.defective, "clean": self.clean,
            "caught": self.caught, "missed": self.missed,
            "false_positives": self.false_positives,
            "correct_clears": self.correct_clears,
            "detection_rate": round(self.detection_rate, 3),
            "false_positive_rate": round(self.false_positive_rate, 3),
            "seconds": round(self.seconds, 2), "errors": self.errors,
            "details": self.details,
        }


def _drafts() -> list[tuple[str, bool, str]]:
    """Build the draft set: (text, is_defective, label)."""
    out = []
    for label, good, bad in DEFECTS:
        text = CLEAN_TEMPLATE.replace(f"is {good}", f"is {bad}")
        # Guard: the substitution must actually have changed something.
        if text == CLEAN_TEMPLATE:
            raise AssertionError(f"defect injection failed for {label}")
        out.append((text, True, f"defect:{label}"))
    for i in range(len(DEFECTS)):
        out.append((CLEAN_TEMPLATE, False, f"clean:{i}"))
    return out


REFUTER_SYSTEM = (
    "You are a refuter. Your job is to try to prove the draft WRONG by comparing"
    " it against the source document. Do not be agreeable; look for any value that"
    " contradicts the source.\n\n"
    f"SOURCE DOCUMENT:\n{SOURCE}\n\n"
    "Reply with exactly one word: MISMATCH if the draft contradicts the source, or"
    " OK if every value in the draft matches the source."
)


def _judge(provider: Provider, draft: str) -> tuple[str, Usage]:
    c = provider.complete(
        f"Draft to check:\n{draft}\n\nReply MISMATCH or OK.", max_tokens=900,
        system=REFUTER_SYSTEM)
    if not c.ok:
        return "ERROR", c.usage
    verdict = c.text.strip().upper()
    if "MISMATCH" in verdict:
        return "MISMATCH", c.usage
    if "OK" in verdict:
        return "OK", c.usage
    # Neither word present: unusable, counted as an error rather than a verdict.
    return "ERROR", c.usage


def run_refuter(producer: Provider, refuter: Provider, mode: str) -> RefuterResult:
    """Judge every draft with `refuter`. `producer` is unused for judging; it is
    kept in the signature because the same-provider mode passes the same object."""
    drafts = _drafts()
    r = RefuterResult(mode=mode, drafts=len(drafts),
                      defective=sum(1 for _, d, _ in drafts if d),
                      clean=sum(1 for _, d, _ in drafts if not d))
    started = time.time()

    for text, is_defective, label in drafts:
        verdict, usage = _judge(refuter, text)
        if verdict == "ERROR":
            r.errors += 1
            r.details.append(f"{label}: ERROR")
            continue
        if is_defective:
            if verdict == "MISMATCH":
                r.caught += 1
                r.details.append(f"{label}: caught")
            else:
                r.missed += 1
                r.details.append(f"{label}: MISSED")
        else:
            if verdict == "MISMATCH":
                r.false_positives += 1
                r.details.append(f"{label}: false positive")
            else:
                r.correct_clears += 1
                r.details.append(f"{label}: cleared")

    r.seconds = time.time() - started
    return r
