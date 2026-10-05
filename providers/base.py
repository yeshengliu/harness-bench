"""Provider interface.

One method matters: `complete()` returns text plus the token usage the provider
reported. Token accounting is the primary measurement in this benchmark, so it
must come from the provider, never be estimated locally.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.calls += other.calls


@dataclass
class Completion:
    text: str
    usage: Usage
    seconds: float
    error: Optional[str] = None
    tool_names: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


class Provider:
    """Base. Subclasses implement `_call`."""

    name = "base"
    model = ""

    def __init__(self) -> None:
        self.usage = Usage()

    def complete(self, prompt: str, max_tokens: int = 1024,
                 system: Optional[str] = None) -> Completion:
        started = time.time()
        try:
            text, usage = self._call(prompt, max_tokens, system)
            usage.calls += 1
            self.usage.add(usage)
            return Completion(text=text, usage=usage, seconds=time.time() - started)
        except Exception as exc:                      # noqa: BLE001
            return Completion(text="", usage=Usage(), seconds=time.time() - started,
                              error=f"{type(exc).__name__}: {exc}")

    def complete_with_tools(self, prompt: str, tools: list, max_tokens: int = 1024,
                            system: Optional[str] = None) -> Completion:
        """Offer tools so hands-on compliance is physically possible.

        Without tools a model cannot violate a hands-on boundary even if it wants
        to, which silently makes a boundary test vacuous. Any subclass that does not
        implement tool support returns an error rather than a fake refusal, so a
        missing implementation cannot be mistaken for compliance.
        """
        if not hasattr(self, "_call_with_tools"):
            return Completion(text="", usage=Usage(), seconds=0.0,
                              error="provider does not implement complete_with_tools")
        started = time.time()
        try:
            text, usage, names = self._call_with_tools(prompt, tools, max_tokens, system)
            usage.calls += 1
            self.usage.add(usage)
            return Completion(text=text, usage=usage, seconds=time.time() - started,
                              tool_names=names)
        except Exception as exc:                      # noqa: BLE001
            return Completion(text="", usage=Usage(), seconds=time.time() - started,
                              error=f"{type(exc).__name__}: {exc}")

    def _call(self, prompt: str, max_tokens: int,
              system: Optional[str]) -> tuple[str, Usage]:
        raise NotImplementedError


class DeepSeekProvider(Provider):
    """OpenAI-compatible endpoint. Verified working; reports usage.

    The model is selectable: the concurrency scenario compares models with
    different context windows, since window size is the variable that makes
    context exhaustion reachable in a test rather than hypothetical.
    """

    name = "deepseek"

    def __init__(self, model: str = "deepseek-v4.1-flash") -> None:
        super().__init__()
        self.model = model
        self.base = "https://opencode.ai/zen/go/v1"
        self.key = _read_key()

    def _call(self, prompt, max_tokens, system):
        import urllib.request

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }).encode()

        req = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "x-opencode-session": "harness-bench",
                "User-Agent": "deepseek-harness/0.1.5",
            },
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode())

        if "error" in data:
            raise RuntimeError(data["error"].get("message", str(data["error"])))

        text = data["choices"][0]["message"].get("content") or ""
        u = data.get("usage") or {}
        return text, Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            reasoning_tokens=(u.get("completion_tokens_details") or {})
                            .get("reasoning_tokens", 0) or 0,
        )

    def _call_with_tools(self, prompt, tools, max_tokens, system):
        """Same endpoint, with a tool schema. Returns (text, usage, tool_names)."""
        import urllib.request

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({"model": self.model, "messages": messages,
                           "max_tokens": max_tokens, "tools": tools}).encode()
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.key}",
                     "Content-Type": "application/json",
                     "x-opencode-session": "harness-bench",
                     "User-Agent": "deepseek-harness/0.1.5"})

        last = None
        for _ in range(4):                       # this endpoint 403s intermittently
            try:
                with urllib.request.urlopen(req, timeout=240) as resp:
                    data = json.loads(resp.read().decode())
                break
            except Exception as exc:             # noqa: BLE001
                last = exc
                time.sleep(8)
        else:
            raise RuntimeError(f"tool call failed after retries: {last}")

        if "error" in data:
            raise RuntimeError(data["error"].get("message", str(data["error"])))
        msg = data["choices"][0]["message"]
        names = [t["function"]["name"] for t in (msg.get("tool_calls") or [])]
        u = data.get("usage") or {}
        return (msg.get("content") or ""), Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0)), names


class CodexProvider(Provider):
    """GPT-5.6-sol via the codex CLI. The second provider.

    Requires codex >= 0.157.0 for the `gpt-5.6-sol` slug; older CLIs reject it
    with "requires a newer version of Codex". Verified working on 0.160.0.

    Requires full file access: codex initializes an in-process app-server and
    fails with EPERM under a restricted sandbox.

    Note: codex reports a single token count, not a prompt/completion split, so
    its usage is recorded as completion_tokens only. Do not compare it against
    DeepSeek's split without accounting for that difference.
    """

    name = "codex"

    def __init__(self, model: str = "gpt-5.6-sol") -> None:
        super().__init__()
        self.model = model

    def _call(self, prompt, max_tokens, system):
        full = f"{system}\n\n{prompt}" if system else prompt
        proc = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check",
             "-c", 'sandbox_mode="read-only"',
             "-c", f"model={self.model}"],
            input=full, capture_output=True, text=True, timeout=300,
        )
        out = proc.stdout
        if proc.returncode != 0 and "ERROR" in out:
            raise RuntimeError(out.strip().splitlines()[-1][:200])

        # codex prints "tokens used\n<N>"
        tokens = 0
        for i, line in enumerate(out.splitlines()):
            if line.strip().startswith("tokens used"):
                try:
                    tokens = int(out.splitlines()[i + 1].replace(",", "").strip())
                except (IndexError, ValueError):
                    tokens = 0
                break
        return _strip_codex_framing(out), Usage(completion_tokens=tokens)


def _strip_codex_framing(out: str) -> str:
    """Remove codex's session banner and token footer."""
    lines = out.splitlines()
    keep, skip_next = [], False
    for line in lines:
        if line.startswith("session id:") or line.startswith("reasoning effort"):
            continue
        if line.strip().startswith("tokens used"):
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        if line.strip() == "--------":
            continue
        if line.strip() in ("user", "codex"):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


def _read_key() -> str:
    """Read the provider key from the DSH credential store."""
    env = os.environ.get("OPENCODE_GO_API_KEY")
    if env:
        return env
    creds = Path.home() / ".dsh" / ".credentials.yaml"
    if not creds.exists():
        raise RuntimeError("no OPENCODE_GO_API_KEY and no ~/.dsh/.credentials.yaml")
    import re
    m = re.search(r"OPENCODE_GO_API_KEY\s*:\s*(.+)", creds.read_text())
    if not m:
        raise RuntimeError("OPENCODE_GO_API_KEY not found in credentials file")
    return m.group(1).strip().strip('"').strip("'")


def get(name: str, model: str | None = None) -> Provider:
    if name == "deepseek":
        return DeepSeekProvider(model) if model else DeepSeekProvider()
    if name == "codex":
        return CodexProvider(model) if model else CodexProvider()
    raise ValueError(f"unknown provider: {name}")
