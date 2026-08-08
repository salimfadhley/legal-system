"""Unit tests for periodic self-healing catch-up (``run_periodic_catchup``, task #2).

The running consumer re-runs the bounded catch-up on a timer so the raw-vs-index gap
cannot grow unbounded if the git publish-commit hook silently fails between restarts.
The loop is decoupled from wall-clock time (``wait`` and ``run_pass`` are injected) so
these tests assert the wiring WITHOUT sleeping.
"""

from __future__ import annotations

import asyncio

from goldberg_system.ingest.catchup import run_periodic_catchup


def _fake_report(run_id: str = "catchup-x"):
    from goldberg_system.ingest.catchup import CatchupReport

    return CatchupReport(
        run_id=run_id, scanned=0, new=0, pending=0, indexed=0, skipped=0,
        dead_lettered=0, elapsed=0.0,
    )


def test_runs_a_pass_each_interval_until_stopped() -> None:
    reports = []
    waits: list[float] = []

    def should_continue() -> bool:
        # Called before AND after each wait; allow the loop to proceed until 3 passes
        # have completed, then stop.
        return len(reports) < 3

    async def run_pass():
        return _fake_report(f"pass-{len(reports)}")

    async def fake_wait(interval: float) -> None:
        waits.append(interval)  # no real sleep

    async def go() -> int:
        return await run_periodic_catchup(
            interval=900.0,
            run_pass=run_pass,
            should_continue=should_continue,
            on_report=reports.append,
            wait=fake_wait,
        )

    passes = asyncio.run(go())

    assert passes == 3
    assert len(reports) == 3
    assert all(w == 900.0 for w in waits)  # each pass waited the configured interval


def test_stop_during_interval_runs_no_extra_pass() -> None:
    # should_continue is True before the wait, then flips to False so the post-wait
    # check ends the loop with zero passes (a stop requested mid-interval is prompt).
    state = {"first": True}

    def should_continue() -> bool:
        if state["first"]:
            state["first"] = False
            return True  # enter the loop, do the wait
        return False  # after the wait → stop, no pass

    ran = {"count": 0}

    async def run_pass():
        ran["count"] += 1
        return _fake_report()

    async def fake_wait(interval: float) -> None:
        return None

    passes = asyncio.run(
        run_periodic_catchup(
            interval=1.0,
            run_pass=run_pass,
            should_continue=should_continue,
            wait=fake_wait,
        )
    )

    assert passes == 0
    assert ran["count"] == 0


def test_disabled_when_should_continue_false_immediately() -> None:
    async def run_pass():  # pragma: no cover - must never be called
        raise AssertionError("run_pass should not run when disabled")

    async def fake_wait(interval: float) -> None:  # pragma: no cover
        raise AssertionError("wait should not run when disabled")

    passes = asyncio.run(
        run_periodic_catchup(
            interval=900.0,
            run_pass=run_pass,
            should_continue=lambda: False,
            wait=fake_wait,
        )
    )

    assert passes == 0
