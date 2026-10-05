"""Fault injection.

Each fault targets one invariant. A test that only runs the happy path proves
nothing about a durability design, so every scenario here breaks something.

Worker functions are module-level: `spawn` must pickle them, so they cannot be
closures. Each worker opens its own connection, which is also what makes the
concurrency test meaningful -- separate processes, no shared memory.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time
from dataclasses import dataclass

from worklist import Worklist


@dataclass
class FaultOutcome:
    name: str
    detail: str
    recovered: bool


# ---- module-level workers (picklable) ------------------------------------

def _killable_worker(db: str, tid: str, who: str) -> None:
    """Claims a task, records progress, then blocks until killed."""
    wl = Worklist(db)
    wl.claim(tid, who, lease_seconds=60)
    wl.note(tid, "did work before dying")
    wl.db.commit()
    time.sleep(30)


def _racer(db: str, tid: str, idx: int, out) -> None:
    """Attempts one claim; reports whether it won."""
    try:
        wl = Worklist(db)
        got = wl.claim(tid, f"racer-{idx}", lease_seconds=60)
        out.put((idx, bool(got)))
    except Exception as exc:                              # noqa: BLE001
        out.put((idx, f"error: {type(exc).__name__}: {exc}"))


# ---- faults --------------------------------------------------------------

def kill_during_claim(db_path: str, task_id: str, owner: str) -> FaultOutcome:
    """Kill a worker with SIGKILL mid-task, then check the record survived.

    SIGKILL runs no cleanup handler, so anything that survived came from the
    record rather than an in-process buffer.
    """
    proc = mp.Process(target=_killable_worker, args=(db_path, task_id, owner))
    proc.start()
    time.sleep(2.0)                          # let it claim and write its note

    killed_ok = True
    try:
        os.kill(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        killed_ok = False                     # died before we killed it
    proc.join(timeout=10)

    wl = Worklist(db_path)
    t = wl.get(task_id)
    alive = t is not None and t.state == "in_progress"
    note_kept = bool(t) and "did work before dying" in t.notes
    still_held = bool(t) and t.owner == owner

    return FaultOutcome(
        name="kill_during_claim",
        detail=(f"killed={killed_ok} state={t.state if t else None} "
                f"note_kept={note_kept} claim_held={still_held}"),
        recovered=alive and note_kept and still_held,
    )


def expire_lease(db_path: str, task_id: str, owner: str,
                 lease_seconds: float = 1.0) -> FaultOutcome:
    """Let a lease lapse, then confirm the task becomes restartable."""
    wl = Worklist(db_path)
    wl.claim(task_id, owner, lease_seconds=lease_seconds)
    time.sleep(lease_seconds + 0.5)

    detected = task_id in [t.task_id for t in wl.expired()]

    wl2 = Worklist(db_path)
    reclaimed = wl2.claim(task_id, "owner-recovery", lease_seconds=60)

    # A live lease must NOT be reclaimable, or the expiry test is vacuous.
    wl3 = Worklist(db_path)
    blocked = not wl3.claim(task_id, "owner-intruder", lease_seconds=60)

    return FaultOutcome(
        name="expire_lease",
        detail=(f"expired_detected={detected} reclaimed={reclaimed} "
                f"live_lease_blocks_intruder={blocked}"),
        recovered=detected and reclaimed and blocked,
    )


def concurrent_claim(db_path: str, task_id: str, n: int = 5) -> FaultOutcome:
    """N processes race for one task. Exactly one must win.

    A claim two owners can win at once is worse than no claim: a correctness bug
    visible only under concurrency.
    """
    q = mp.Queue()
    procs = [mp.Process(target=_racer, args=(db_path, task_id, i, q))
             for i in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    results = []
    while not q.empty():
        results.append(q.get())

    errors = [r for r in results if isinstance(r[1], str)]
    winners = [i for i, got in results if got is True]

    detail = f"{len(winners)} of {n} won: {winners}"
    if errors:
        detail += f" | errors: {errors}"
    if len(results) != n:
        detail += f" | WARNING: only {len(results)}/{n} reported"

    return FaultOutcome(name="concurrent_claim", detail=detail,
                        recovered=(len(winners) == 1 and not errors))


def mutate_version_midrun(db_path: str, task_id: str,
                          versions: dict) -> FaultOutcome:
    """Change the instruction set while a task is in flight."""
    wl = Worklist(db_path)
    pinned = wl.get(task_id).version
    before = versions.get(pinned)

    versions["v99"] = "REPLACED: totally different instructions."
    after = dict(versions)
    del versions["v99"]

    unchanged = after.get(pinned) == before
    new_latest = max(after)

    return FaultOutcome(
        name="mutate_version_midrun",
        detail=(f"pinned={pinned} pinned_text_unchanged={unchanged} "
                f"new_latest={new_latest}"),
        recovered=unchanged,
    )
