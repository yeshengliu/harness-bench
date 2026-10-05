# Harness bench

A sandbox that tests the invariants claimed by the `harness-pattern` spec under
fault injection, with real model calls and provider-reported token accounting.

Separate from the spec repo on purpose: the spec is a design document, this is the
evidence for it. Results live in [`FINDINGS.md`](FINDINGS.md).

**The spec cites these numbers** in `docs/02-self-evolving-loop.md` and
`docs/03-tradeoffs.md`. If a measurement here changes, update it there — the two
repositories are meant to stay consistent, and a stale figure in a design document
is worse than no figure.

## What it tests

| Scenario | Fault | Measures |
| --- | --- | --- |
| `invariant_kill` | `SIGKILL` a worker mid-task | Does the record survive a process that runs no cleanup? |
| `invariant_lease` | Let a lease lapse | Can another owner recover it, and does a live lease block intruders? |
| `invariant_concurrent` | 5 processes race | Does exactly one win? |
| `invariant_version_pin` | Mutate instructions mid-run | Does a running task keep its version? |
| `invariant_lexical_guard` | Request hands-on work | Does a code-enforced boundary beat a prose rule? |
| `compare_topologies` | — | Hub (spec + artifacts) vs relay (paraphrase) on identical tasks |
| `concurrency` | N tasks serial vs N isolated | Peak context, growth curve, speed, total tokens |
| `selfreview` | Producer generates defects | Same-provider vs cross-provider refuter recall |

## Running

```bash
python3 bench.py                  # invariants + topology comparison
python3 bench.py --invariants     # mechanism tests only (~1 min)
python3 concurrency.py            # serial vs concurrent, two models
python3 concurrency.py --models Model F --tasks 14
python3 refuter.py                # planted-defect refuter (did not discriminate)
python3 selfreview.py --repeat 2  # self-review refuter (discriminates)
```

Writes to `results/`. Requires a working provider credential (see below). No
third-party packages.

## Providers

`providers/base.py` implements two:

- **deepseek** — Vendor 2-compatible HTTP endpoint. Default, verified working,
  reports `prompt_tokens` / `completion_tokens` / `reasoning_tokens`.
- **codex** — `Model B` via the `codex` CLI. The second provider, used for the
  cross-provider refuter test. Requires **codex ≥ 0.157.0** (older CLIs reject that
  slug) and **full file access** — the CLI initializes an in-process app-server and
  otherwise fails with `EPERM`.

Codex reports a single token total rather than a prompt/completion split, so its
numbers are recorded as completion tokens only. Do not compare them against
Vendor 1's split without accounting for that.

The Vendor 1 credential is read from `$OPENCODE_GO_API_KEY` or
`~/.dsh/.credentials.yaml`. Nothing is hardcoded. Codex uses its own CLI auth.

Token counts always come from the provider's `usage` field, never estimated
locally.

## Design notes

**Faults, not happy paths.** Every scenario breaks something. A durability design
demonstrated only on the happy path has demonstrated nothing.

**Negative controls.** Two tests assert that they *can* fail: the lease test
requires a live lease to reject a second claimant, and the pinning test requires
the mutation to be observable when pinning is disabled. Without these, a harness
that does nothing would pass.

**Empty output is not compliance.** This model can spend its whole token budget on
reasoning tokens and return empty text with `finish_reason=stop` and no error.
The harness logs that as `dispatcher_empty` rather than scoring it either way.

**Processes, not threads.** Workers are module-level functions so `spawn` can
pickle them, and each opens its own connection. Shared memory would make the
concurrency test meaningless.

## Limits

Read these before citing any number here.

- **Not an independent benchmark.** The harness under test and the comparison are
  by the same author. `FINDINGS.md` records this as a mechanism check.
- **The serial baseline is naive.** It re-sends the full transcript with no
  compaction. A competent single-agent harness would compact and the growth curve
  would flatten, so the concurrency result measures the cost of *unmanaged* context
  accumulation, not a deficit inherent to single-agent designs.
- **The concurrency speedup is not purely architectural.** The concurrent mode
  runs in a thread pool; serial is strictly sequential. Part of the gap is
  parallelism, not context size.
- **One provider for the main scenarios.** The cross-provider refuter test does run
  (two providers available), but it did not discriminate: see `results/REFUTER.md`.
- **Small n.** 3–6 trials per scenario (14 for the concurrency check). Enough to
  catch mechanism failures, not enough for statistical claims.
