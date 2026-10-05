# Cross-provider refuter: NOT DISCRIMINATING

Ran 2026-10-05. Producer `deepseek-v4.1-flash`. Cross-provider refuter
`gpt-5.6-sol` via Codex 0.160.0.

**Result: the invariant was tested three ways and the test never discriminated.
Both refuter configurations scored identically and near-perfectly in every
variant.** This is a negative result about the *test*, recorded as such rather
than as support for the invariant.

---

## What was run

| Variant | Defect type | Same-provider | Cross-provider |
| --- | --- | --- | --- |
| v1, explicit prompt | 3 value substitutions | 3/3 caught, 0/3 FP | 3/3 caught, 0/3 FP |
| v2, explicit prompt | arithmetic, consistency, implication | 3/3 caught, 0/3 FP | 3/3 caught, 0/3 FP |
| v3, neutral prompt | arithmetic, consistency, implication | 3/3 caught, 0/3 FP | 3/3 caught, 0/3 FP |

Every configuration detected every planted defect and raised no false positives.
Latency differed substantially (deepseek ~3.6s, codex ~27.8s for the same work);
accuracy did not.

## Why the test failed

Three reasons:

**1. The defects were too easy.** Value substitution is string comparison.
Arithmetic (12+7+4 against a stated 24) and internal contradiction are one-step
inference. Both models are strong enough that neither has a blind spot here, so
there is no correlated error for provider diversity to decorrelate.

**2. The refuter prompt did the work.** In v1 and v2 the prompt instructs the model
to check arithmetic, recompute derived values, and look for contradictions. That
converts a hard task into a mechanical one. Removing those hints in v3 changed
nothing, which shows the hints were not the binding constraint.

**3. There is no producer with a blind spot.** Here the "producer" is a hand-written
draft from a clean source. In the real harness the producer is an agent that has
done work and is invested in its own output. Anthropic's finding is about exactly
that: agents "confidently praise" their own work. This test has no generator, so it
does not reproduce the condition the cross-provider claim is about.

## What would actually test the invariant

The claim is that a same-provider refuter shares the producer's blind spots. To
test it, the producer must *have* a blind spot, and both refuters must face the
same material:

- **Use the producer's real output**, not hand-written drafts. Have the agent solve
  a task, then refute its own solution. Self-review leniency is the documented
  failure mode, and it cannot appear when a human writes the draft.
- **Choose defects correlated with model priors** rather than with logic:
  plausible-but-wrong API usage, a subtly wrong library idiom, a hallucinated
  function signature. These are errors a model is disposed to make *and* disposed
  to accept.
- **Raise n and vary the domain** until the same-provider refuter misses something.
  If it never misses, the invariant is unsupported in this setting and that is the
  finding.

## What this run does establish

- The cross-provider path **works end to end**. Codex 0.160.0 with `gpt-5.6-sol`
  acts as a refuter, parses correctly, and returns usable verdicts.
- **Cross-provider refutation is roughly 8x slower** on identical work (27.8s
  vs 3.6s). That cost is real and belongs in the tradeoffs document regardless of
  whether an accuracy benefit materializes.
- **Neither model produced a false positive**, so both are usable as refuters
  without polluting clean work.

## Honest status of the invariant

| Aspect | Status |
| --- | --- |
| Infrastructure works | **Yes** — both providers act as refuters |
| Cross-provider catches more defects | **Not demonstrated** — both scored 3/3 |
| Cross-provider is slower | **Yes** — roughly 8x here |
| Same-provider refutation is unreliable | **Not tested** — no generator in the loop |

The spec asserts that a same-model refuter is "theater" and that provider diversity
is what makes refutation meaningful. **This bench did not demonstrate that.** The
claim may still be true; it rests on reasoning about correlated errors and on
Anthropic's self-evaluation finding. But it is currently supported by argument, not
by measurement here.

Treat the cross-provider invariant as **stated but unverified**, and its cost as
verified.

## Limits

- One producer model, one defect family per variant, 3 defective and 3 clean drafts
  per variant. Small n; rates are indicative, not statistical.
- Same author as the harness under test.
- Codex needs full file access to initialize its app-server, so these runs execute
  outside the default sandbox.
