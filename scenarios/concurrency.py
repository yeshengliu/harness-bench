"""Concurrency scenario.

Tests the spec's central operational claim: that isolating N tasks into N owners
keeps each owner's context bounded, whereas running the same N tasks through one
session accumulates context and eventually fails.

Two modes, identical tasks and payloads:

  concurrent  N owners, each in its own context. Measures PEAK context per owner.
  serial      one agent processes all N tasks in sequence, carrying the whole
              transcript forward. Measures the GROWING context.

The metric that matters is peak context, not cumulative tokens. A design that
spends more tokens total but keeps any single context small is the design the spec
claims wins -- so cumulative totals alone would measure the wrong thing.

Context is measured as the prompt_tokens the provider reports, which is the actual
window occupancy at call time. It is not estimated locally.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from providers.base import Provider
from worklist import Worklist


@dataclass
class RoundMetrics:
    mode: str
    model: str
    n_tasks: int
    peak_context: int = 0
    final_context: int = 0
    total_prompt: int = 0
    total_completion: int = 0
    total_context: int = 0          # sum of every prompt across every call
    calls: int = 0
    seconds: float = 0.0
    failures: int = 0
    per_task_peak: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "mode": self.mode, "model": self.model, "n_tasks": self.n_tasks,
            "peak_context": self.peak_context,
            "final_context": self.final_context,
            "total_prompt": self.total_prompt,
            "total_completion": self.total_completion,
            "total_context": self.total_context,
            "calls": self.calls, "seconds": round(self.seconds, 2),
            "failures": self.failures,
            "per_task_peak": self.per_task_peak,
        }


# A task large enough that N of them clearly exceed a small window when serialized.
DOC = (
    "Service atlas-7 deployment notes.\n"
    + "\n".join(
        f"Step {i}: run `atlasctl deploy --stage {i} --retry-budget 4 "
        f"--canary-window 900 --team platform-infra` and verify "
        f"error_rate <= 0.02 over 5 minutes before promoting stage {i}."
        for i in range(1, 13)
    )
    + "\nOwning team: platform-infra. Rollback trigger: error_rate > 0.02."
)
TASK = ("Summarize the deployment notes. State the retry budget and the owning"
        " team in your answer.")


def _one_task(p: Provider, idx: int, doc: str, budget: int) -> tuple[bool, int, int, int, float]:
    """One isolated task in its own context. Returns (ok, peak, prompt, completion, secs)."""
    started = time.time()
    system = ("You are a coding assistant. Be concise. You are the owner of one"
              " task, working in your own context.")
    c = p.complete(f"{TASK}\n\nDocument:\n{doc}", max_tokens=budget, system=system)
    secs = time.time() - started
    ok = c.ok and bool(c.text.strip())
    return ok, c.usage.prompt_tokens, c.usage.prompt_tokens, \
        c.usage.completion_tokens, secs


def run_concurrent(p: Provider, n: int, budget: int = 700) -> RoundMetrics:
    """N owners, each with its own context, running in parallel."""
    m = RoundMetrics("concurrent", p.model, n)
    started = time.time()

    def work(i: int):
        return _one_task(p, i, DOC, budget)

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(work, range(n)))

    for ok, peak, prompt, completion, secs in results:
        m.per_task_peak.append(peak)
        m.peak_context = max(m.peak_context, peak)
        m.total_prompt += prompt
        m.total_completion += completion
        m.total_context += prompt
        m.calls += 1
        if not ok:
            m.failures += 1

    m.final_context = m.peak_context
    m.seconds = time.time() - started
    return m


def run_serial(p: Provider, n: int, budget: int = 700) -> RoundMetrics:
    """One session processing every task in sequence, carrying its transcript.

    This is the configuration the spec says degrades: each call re-sends the whole
    accumulated history, so prompt_tokens grows monotonically and eventually hits
    the window.
    """
    m = RoundMetrics("serial", p.model, n)
    started = time.time()

    history: list[dict] = [{"role": "system",
                            "content": ("You are a coding assistant. Be concise."
                                        " You are working through a backlog of"
                                        " tasks in this session.")}]
    for i in range(n):
        history.append({"role": "user", "content": f"Task {i+1}: {TASK}\n\nDocument:\n{DOC}"})
        c = _serial_call(p, history, budget)
        m.calls += 1
        m.total_prompt += c["prompt"]
        m.total_completion += c["completion"]
        m.total_context += c["prompt"]
        m.peak_context = max(m.peak_context, c["prompt"])
        m.per_task_peak.append(c["prompt"])
        if not c["ok"]:
            m.failures += 1
        history.append({"role": "assistant", "content": c["text"] or "(empty)"})

    m.final_context = m.per_task_peak[-1] if m.per_task_peak else 0
    m.seconds = time.time() - started
    return m


def _serial_call(p: Provider, history: list[dict], budget: int) -> dict:
    """One call carrying the full history. Bypasses Provider.complete to control
    the message list directly -- the accumulation is the point of this mode."""
    import json
    import urllib.request

    body = json.dumps({"model": p.model, "messages": history,
                       "max_tokens": budget}).encode()
    req = urllib.request.Request(
        f"{p.base}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {p.key}",
                 "Content-Type": "application/json",
                 "x-opencode-session": "harness-bench",
                 "User-Agent": "deepseek-harness/0.1.5"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            d = json.loads(resp.read().decode())
        if "error" in d:
            return {"ok": False, "prompt": 0, "completion": 0, "text": "",
                    "err": str(d["error"])[:150]}
        u = d.get("usage") or {}
        text = d["choices"][0]["message"].get("content") or ""
        return {"ok": bool(text.strip()), "prompt": u.get("prompt_tokens", 0),
                "completion": u.get("completion_tokens", 0), "text": text}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "prompt": 0, "completion": 0, "text": "",
                "err": f"{type(exc).__name__}: {exc}"[:150]}
