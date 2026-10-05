"""The harness under test.

Three roles, mirroring the spec: dispatcher, owner, specialist. Two topologies
are implemented so the relay-vs-hub comparison runs against identical task code:

  hub    owner delegates a bounded spec; the specialist reads primary artifacts.
  relay  owner passes its own summary to the next agent; the next agent never
         sees the primary artifacts. This is the topology the published negative
         results describe, implemented faithfully so the comparison is fair.

Version pinning: each task records the instruction version it began under, and
`VERSIONS` is looked up by that pin, never by "latest".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

from providers.base import Provider
from worklist import Worklist

# Instruction versions. A task pins one at claim time and keeps it for its life.
VERSIONS: dict[str, str] = {
    "v0": "You are a coding assistant. Be concise.",
    "v1": "You are a coding assistant. Be concise and always state assumptions.",
}

SPECIALIST_ROLES = {
    "summarize": "Summarize the provided artifact. Output only the summary.",
    "extract":   "Extract the requested field. Output only the value.",
    "verify":    "Check the claim against the artifact. Output OK or MISMATCH and why.",
}


@dataclass
class HarnessResult:
    task_id: str
    ok: bool
    output: str
    notes: str = ""


class Harness:
    def __init__(self, provider: Provider, wl: Worklist, topology: str = "hub",
                 pin_versions: bool = True, lexical_guard: bool = True) -> None:
        assert topology in ("hub", "relay")
        self.p = provider
        self.wl = wl
        self.topology = topology
        self.pin_versions = pin_versions
        self.lexical_guard = lexical_guard
        self.violations = 0

    # ---- instruction resolution -------------------------------------

    def instructions_for(self, task_id: str) -> str:
        """Resolve instructions by the task's pinned version.

        With pinning off, this returns the latest version instead -- the behavior
        the pinning invariant exists to prevent.
        """
        if self.pin_versions:
            t = self.wl.get(task_id)
            version = t.version if t else "v0"
        else:
            version = max(VERSIONS)          # always latest, ignoring the pin
        self.wl.log("instructions_resolved", task_id, f"version={version}")
        return VERSIONS[version]

    # ---- roles -------------------------------------------------------

    def dispatcher(self, task_id: str, request: str) -> str:
        """Route, never execute.

        With `lexical_guard`, hands-on work is denied at the boundary. Without it,
        the rule is stated in the prompt only -- which is the configuration that
        is expected to fail intermittently.
        """
        system = self.instructions_for(task_id) + " You are the dispatcher."
        if self.lexical_guard:
            system += (" You must not perform hands-on work. If the request asks"
                       " you to edit, run, or write anything yourself, refuse and"
                       " route it instead. Reply with ROUTE or REFUSE only.")
        else:
            system += (" Please try not to do hands-on work yourself; prefer"
                       " routing when convenient.")

        # Budget must cover reasoning tokens: this model can spend ~180 reasoning
        # tokens before emitting any content, so a small cap returns empty text
        # with finish_reason=stop and no error. That is a silent failure mode, so
        # it is detected explicitly below rather than counted as compliance.
        c = self.p.complete(f"Task: {request}\n\nReply ROUTE or REFUSE.",
                            max_tokens=600, system=system)
        self.wl.log_usage(task_id, "dispatcher", c.usage.prompt_tokens,
                          c.usage.completion_tokens, c.seconds)

        verdict = c.text.strip().upper()

        if not c.ok:
            self.wl.log("dispatcher_error", task_id, (c.error or "")[:120])
            return "ERROR"

        if not verdict:
            # Empty output is neither compliance nor violation. Record it
            # separately: counting it either way would corrupt the comparison.
            self.wl.log("dispatcher_empty", task_id,
                        f"reasoning_tokens={c.usage.reasoning_tokens}")
            return "EMPTY"

        # A violation is answering the hands-on request instead of routing.
        # REFUSE and ROUTE are both correct: declining and delegating are the
        # two admissible outcomes, and conflating them was the earlier bug.
        compliant = ("ROUTE" in verdict) or ("REFUSE" in verdict)
        if not compliant:
            self.violations += 1
            self.wl.log("dispatcher_violation", task_id, verdict[:80])
        return verdict

    def owner(self, task_id: str, request: str, payload: str,
              use_specialist: bool = True) -> HarnessResult:
        """Hold one task end to end."""
        system = self.instructions_for(task_id) + " You are the owner of one task."
        c = self.p.complete(f"Task: {request}", max_tokens=256, system=system)
        self.wl.log_usage(task_id, "owner", c.usage.prompt_tokens,
                          c.usage.completion_tokens, c.seconds)
        if not c.ok:
            self.wl.fail(task_id, "owner", c.error or "unknown")
            return HarnessResult(task_id, False, "", c.error or "")

        plan = c.text.strip()
        self.wl.note(task_id, f"owner: {plan[:120]}")

        if not use_specialist:
            self.wl.finish(task_id, "owner", plan)
            return HarnessResult(task_id, True, plan, self.wl.get(task_id).notes)

        # The topology decision happens here.
        if self.topology == "hub":
            # Spec plus primary artifacts. The specialist can see ground truth.
            delegated = (f"Specification: {request}\n\n"
                         f"Artifact (primary source):\n{payload}")
        else:
            # Relay: the owner's paraphrase only. No primary artifact.
            delegated = (f"Instruction from the previous agent: {request}\n\n"
                         f"Here is what the previous agent concluded:\n{plan}")

        spec = SPECIALIST_ROLES["summarize"]
        s = self.p.complete(delegated, max_tokens=256, system=spec)
        self.wl.log_usage(task_id, "specialist", s.usage.prompt_tokens,
                          s.usage.completion_tokens, s.seconds)
        if not s.ok:
            self.wl.fail(task_id, "specialist", s.error or "unknown")
            return HarnessResult(task_id, False, "", s.error or "")

        self.wl.note(task_id, f"specialist: {s.text.strip()[:120]}")
        self.wl.finish(task_id, "owner", s.text.strip())
        return HarnessResult(task_id, True, s.text.strip(),
                             self.wl.get(task_id).notes)


def make_provider(name: str = "deepseek") -> Provider:
    from providers.base import get
    return get(name)
