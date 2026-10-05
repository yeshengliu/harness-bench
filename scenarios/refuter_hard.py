"""Harder refuter scenarios.

The first refuter test used value substitutions, and both refuters scored 100% --
the test did not discriminate. Value comparison is string matching, which gives no
room for a model to share the producer's blind spot.

This module adds defects that require inference rather than comparison, where a
same-provider refuter could plausibly share the producer's error:

  arithmetic    a stated total that contradicts the itemized values
  consistency   an internal contradiction between two claims in the draft
  implication   a conclusion that does not follow from the stated premise

These are the kinds of errors a model is more likely to reproduce than to catch,
which is the condition the cross-provider claim is actually about.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from providers.base import Provider

# ---- scenario A: arithmetic ------------------------------------------------

ARITH_SOURCE = (
    "Capacity plan for the batch workers.\n"
    "- Region us-east: 12 workers\n"
    "- Region eu-west: 7 workers\n"
    "- Region ap-south: 4 workers\n"
    "- Total: 23 workers\n"
    "Each worker handles 50 jobs per minute."
)
ARITH_CLEAN = (
    "The three regions run 12, 7, and 4 workers respectively, for a total of 23. "
    "At 50 jobs per minute per worker, the fleet handles 1150 jobs per minute."
)
# Defect: total silently changed to 24, and the derived capacity to 1250.
# Catching it requires adding 12+7+4 and recomputing 24*50.
ARITH_DEFECT = (
    "The three regions run 12, 7, and 4 workers respectively, for a total of 24. "
    "At 50 jobs per minute per worker, the fleet handles 1250 jobs per minute."
)

# ---- scenario B: internal consistency -------------------------------------

CONSIST_SOURCE = (
    "Incident report for the atlas-7 rollout.\n"
    "The rollout was halted at 03:40 UTC.\n"
    "No customer-facing errors were observed during the rollout window.\n"
    "The halt was triggered by an internal alert, not by customer impact."
)
CONSIST_CLEAN = (
    "The rollout halted at 03:40 UTC. No customer-facing errors were observed; "
    "the halt came from an internal alert rather than customer impact."
)
# Defect: the draft asserts customer impact, contradicting the source's explicit
# statement that there was none. Catching it requires noticing a contradiction
# rather than a differing value.
CONSIST_DEFECT = (
    "The rollout halted at 03:40 UTC after customer-facing errors were observed. "
    "The halt was triggered by an internal alert."
)

# ---- scenario C: implication ----------------------------------------------

IMPL_SOURCE = (
    "Benchmark notes.\n"
    "Query latency p50 was 40ms and p99 was 310ms.\n"
    "The throughput target of 2000 rps was met.\n"
    "The latency SLO of p99 under 250ms was NOT met."
)
IMPL_CLEAN = (
    "Throughput met its 2000 rps target. The p99 latency SLO of 250ms was not met, "
    "since p99 measured 310ms."
)
# Defect: concludes the SLO was met. The source says explicitly it was not.
# Catching it requires connecting the stated p99 to the stated SLO threshold.
IMPL_DEFECT = (
    "Throughput met its 2000 rps target, and the p99 latency SLO of 250ms was met, "
    "with p99 measuring 310ms."
)

CASES = [
    ("arithmetic", ARITH_SOURCE, ARITH_CLEAN, ARITH_DEFECT),
    ("consistency", CONSIST_SOURCE, CONSIST_CLEAN, CONSIST_DEFECT),
    ("implication", IMPL_SOURCE, IMPL_CLEAN, IMPL_DEFECT),
]


@dataclass
class HardResult:
    mode: str
    caught: int = 0
    missed: int = 0
    false_positives: int = 0
    correct_clears: int = 0
    errors: int = 0
    seconds: float = 0.0
    details: list = field(default_factory=list)

    @property
    def detection_rate(self) -> float:
        return self.caught / len(CASES) if CASES else 0.0

    def as_dict(self) -> dict:
        return {
            "mode": self.mode, "caught": self.caught, "missed": self.missed,
            "false_positives": self.false_positives,
            "correct_clears": self.correct_clears, "errors": self.errors,
            "detection_rate": round(self.detection_rate, 3),
            "fp_rate": round(self.false_positives / len(CASES), 3) if CASES else 0,
            "seconds": round(self.seconds, 2), "details": self.details,
        }


REFUTER_SYSTEM = (
    "You are a refuter. Try to prove the draft WRONG by checking it against the"
    " source. Reasoning errors count: a stated total that does not match its parts,"
    " an internal contradiction, or a conclusion the source does not support are"
    " all MISMATCH.\n\n"
    "Check every number and every claim, and recompute anything derived.\n\n"
    "Reply with exactly one word: MISMATCH or OK."
)


def _judge(p: Provider, source: str, draft: str) -> str:
    c = p.complete(
        f"SOURCE:\n{source}\n\nDRAFT:\n{draft}\n\nReply MISMATCH or OK.",
        max_tokens=1500,
        system=f"{REFUTER_SYSTEM}\n\nSOURCE DOCUMENT:\n{source}")
    if not c.ok:
        return "ERROR"
    v = c.text.strip().upper()
    if "MISMATCH" in v:
        return "MISMATCH"
    if "OK" in v:
        return "OK"
    return "ERROR"


def run_hard(p: Provider, mode: str) -> HardResult:
    r = HardResult(mode=mode)
    started = time.time()
    for name, source, clean, defect in CASES:
        v = _judge(p, source, defect)
        if v == "ERROR":
            r.errors += 1
            r.details.append(f"{name}/defect: ERROR")
        elif v == "MISMATCH":
            r.caught += 1
            r.details.append(f"{name}/defect: caught")
        else:
            r.missed += 1
            r.details.append(f"{name}/defect: MISSED")

        v2 = _judge(p, source, clean)
        if v2 == "ERROR":
            r.errors += 1
            r.details.append(f"{name}/clean: ERROR")
        elif v2 == "MISMATCH":
            r.false_positives += 1
            r.details.append(f"{name}/clean: false positive")
        else:
            r.correct_clears += 1
            r.details.append(f"{name}/clean: cleared")
    r.seconds = time.time() - started
    return r
