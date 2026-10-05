# Concurrency results

Ran 2026-10-04 19:21:30. 6 tasks per mode. Budget 700 tokens/call.

Same tasks and payload in both modes. `serial` is one session carrying its
transcript forward; `concurrent` is N owners in isolated contexts.

**Peak context** is the largest prompt the provider reported for any single
call. It is the metric that matters: cumulative tokens can be higher while
every individual context stays small.

| Model | Mode | Peak ctx | Final ctx | Total ctx | Calls | Secs | Fail |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Model A | serial | 4649 | 4649 | 16050 | 6 | 8.67 | 0 |
| Model A | concurrent | 712 | 712 | 4272 | 6 | 1.29 | 0 |
| Model F | serial | 4837 | 4837 | 16861 | 6 | 29.87 | 0 |
| Model F | concurrent | 786 | 786 | 4716 | 6 | 8.31 | 0 |

## Per-call context growth

- **Model A / serial**: [714, 1487, 2276, 3066, 3858, 4649]
  - growth from first to last call: **6.5x**
- **Model A / concurrent**: [712, 712, 712, 712, 712, 712]
- **Model F / serial**: [788, 1584, 2406, 3218, 4028, 4837]
  - growth from first to last call: **6.1x**
- **Model F / concurrent**: [786, 786, 786, 786, 786, 786]

## Limits

- Same author as the harness under test. A mechanism check, not an
  independent benchmark.
- `serial` is a deliberately naive single-session baseline: it re-sends the
  full transcript with no compaction. A production single-agent harness would
  compact, which would reduce the growth shown here. The comparison is
  therefore against the unmanaged case, not against a well-built single agent.
- Task count and document size are fixed; neither is varied to find the
  threshold at which serial mode hits a window limit.
