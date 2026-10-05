"""Display labels.

Code must use real model ids to run. Published reports refer to models by label,
because several of the models used here were incidental test subjects rather than a
chosen comparison, and naming them would imply an evaluation of their vendors that
this bench did not perform.

Only the models the discussion actually turns on are named in prose; the rest are
labelled. Keep this mapping in one place so reports and documents stay consistent.
"""
from __future__ import annotations

# Ids for the models the discussion names. Code refers to these by role so that
# published documents and runnable code share one source of truth.
# Ids for the models named in the documents. Labels are STABLE: every recorded
# result already refers to these ids by label, so changing a mapping here would
# silently disagree with FINDINGS.md and the generated reports.
NAMED = {
    "producer": "deepseek-v4.1-flash",     # Model A
    "cross": "gpt-5.6-sol",                # Model B
}
_NAMED = {
    "deepseek-v4.1-flash": "Model A",
    "gpt-5.6-sol": "Model B",
}
_ANON = {
    "glm-5.3-flash": "Model C",
    "minimax-m2.5": "Model D",
    "longcat-2.0": "Model E",
    "qwen3.8-flash": "Model F",
    "mimo-v2.6-flash": "Model G",
}


def label(model_id: str) -> str:
    """Label for prose. Named models keep their label; others are anonymized."""
    if model_id in _NAMED:
        return _NAMED[model_id]
    if model_id in _ANON:
        return _ANON[model_id]
    # Unknown model: derive a stable label rather than leaking the id.
    idx = 8 + (abs(hash(model_id)) % 18)
    return f"Model {chr(ord('A') + idx - 1)}"
