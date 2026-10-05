# Self-review refuter results

Ran 2026-10-04 21:40:49. Producer `Model A` (DeepSeek V4.1 Flash).
6 passing and 2 failing solutions from the producer.

Ground truth is **execution**: every solution was run against a hidden test
suite. A refuter is scored on whether it flags exactly the failing ones.

| Refuter | Caught | Recall | False pos | FP rate | Errors | Secs |
| --- | --- | --- | --- | --- | --- | --- |
| same-provider | 0/2 | 0% | 0/6 | 0% | 0 | 5.38 |
| cross-provider | 2/2 | 100% | 0/6 | 0% | 0 | 55.02 |
| panel:Model C | 0/2 | 0% | 0/6 | 0% | 0 | 54.05 |
| panel:Model G | 2/2 | 100% | 1/6 | 17% | 1 | 94.28 |
| panel:Model F | 1/2 | 50% | 1/6 | 17% | 0 | 175.78 |
| panel:Model D | 0/2 | 0% | 0/6 | 0% | 1 | 131.86 |
| panel:Model E | 1/2 | 50% | 0/6 | 0% | 3 | 137.51 |

## Per-solution verdicts

**same-provider**
- dedupe: producer FAILED, refuter says CLEAN (MISSED)
- dedupe: producer FAILED, refuter says CLEAN (MISSED)
- chunk: producer PASSED, refuter says CLEAN (correct)
- chunk: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)

**cross-provider**
- dedupe: producer FAILED, refuter says DEFECT (caught)
- dedupe: producer FAILED, refuter says DEFECT (caught)
- chunk: producer PASSED, refuter says CLEAN (correct)
- chunk: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)

**panel:Model C**
- dedupe: producer FAILED, refuter says CLEAN (MISSED)
- dedupe: producer FAILED, refuter says CLEAN (MISSED)
- chunk: producer PASSED, refuter says CLEAN (correct)
- chunk: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)

**panel:Model G**
- dedupe: producer FAILED, refuter says DEFECT (caught)
- dedupe: producer FAILED, refuter says DEFECT (caught)
- chunk: producer PASSED, refuter says CLEAN (correct)
- chunk: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- parse_duration: ERROR
- merge_ranges: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says DEFECT (false positive)

**panel:Model F**
- dedupe: producer FAILED, refuter says DEFECT (caught)
- dedupe: producer FAILED, refuter says CLEAN (MISSED)
- chunk: producer PASSED, refuter says CLEAN (correct)
- chunk: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says DEFECT (false positive)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)

**panel:Model D**
- dedupe: producer FAILED, refuter says CLEAN (MISSED)
- dedupe: producer FAILED, refuter says CLEAN (MISSED)
- chunk: producer PASSED, refuter says CLEAN (correct)
- chunk: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: ERROR
- merge_ranges: producer PASSED, refuter says CLEAN (correct)

**panel:Model E**
- dedupe: producer FAILED, refuter says DEFECT (caught)
- dedupe: ERROR
- chunk: producer PASSED, refuter says CLEAN (correct)
- chunk: producer PASSED, refuter says CLEAN (correct)
- parse_duration: ERROR
- parse_duration: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: producer PASSED, refuter says CLEAN (correct)
- merge_ranges: ERROR

## Reading this

- **Recall** is the fraction of genuinely failing solutions the refuter
  flagged. Low recall on the same-provider side, with higher recall cross-
  provider, is the claim under test.
- **FP rate** is how often a correct solution was wrongly flagged. A refuter
  that flags everything would score perfect recall and useless FPs, so both
  numbers must be read together.
- If the producer passed everything, there were no defects to find and the
  test proves nothing. That condition is reported explicitly.

## The provider-vs-strength confound

A cross-provider refuter scoring higher than the same-provider one has two
possible explanations:

1. **Provider diversity.** Different weights make errors uncorrelated.
2. **Reviewer strength.** The cross-provider model is simply better at
   reviewing, regardless of which provider it came from.

The `panel:` rows test this. They are *different models on the same endpoint
as the producer* — so they add diversity of weights without changing the
provider. Reading the results:

- If panel models also catch the defect, reviewer strength (or any second
  opinion) explains it, and 'different provider' is not the active ingredient.
- If panel models miss it while the codex model catches it, provider
  diversity is doing real work.
- If panel models are inconsistent, the effect is about model quality and the
  provider framing is not supported.

## Limits

- Small n. Two defects in the default run, both from the same task. This is a
  signal, not a measured rate.
- Four tasks, one producer model.
- The tasks target known-common mistakes, which biases toward producing
  failures; that is deliberate, since without failures the test is vacuous.
- Hidden tests check the stated spec, so a solution can be 'correct' in ways
  the test does not measure. Ground truth is the test, not correctness in
  the abstract.
- Codex needs full file access to initialize its app-server, so codex rows
  execute outside the default sandbox.
- Same author as the harness under test.
