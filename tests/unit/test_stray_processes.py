"""The catalogue tools must not leave applications behind.

One run ended with 23 한글 processes and one Excel still resident because
the cleanup asked each to terminate and moved on when it refused. These
cover the refusal, not the happy path — the happy path was already working
and is not what went wrong.
"""

from __future__ import annotations

import unittest
from unittest import mock

from verification import stray_processes
from verification.stray_processes import (
    StrayProcessesRemain,
    StrayProcessGuard,
)


class FakeProcess:
    """A process that dies after a set number of requests, or never."""

    def __init__(self, pid, dies_after=0):
        self.pid = pid
        self.dies_after = dies_after
        self.requests = []

    @property
    def alive(self):
        return self.dies_after is None or len(self.requests) < self.dies_after

    def terminate(self):
        self.requests.append("terminate")

    def kill(self):
        self.requests.append("kill")

    def wait(self, timeout=None):
        if self.alive:
            raise TimeoutError("still running")
        return 0


class StrayProcessGuardTests(unittest.TestCase):
    def _guard(self, baseline, running, processes):
        """A guard over a fake process table."""
        self.table = dict(processes)
        seen = {"ids": list(running)}

        def ids(_names):
            return {pid for pid in seen["ids"] if self.table[pid].alive}

        patch_ids = mock.patch.object(stray_processes, "process_ids", ids)
        patch_ids.start()
        self.addCleanup(patch_ids.stop)

        fake_psutil = mock.MagicMock()
        fake_psutil.Process.side_effect = lambda pid: self.table[pid]
        fake_psutil.NoSuchProcess = LookupError
        patch_import = mock.patch.dict(
            "sys.modules", {"psutil": fake_psutil}
        )
        patch_import.start()
        self.addCleanup(patch_import.stop)

        guard = StrayProcessGuard({"hwp.exe"})
        guard.baseline = set(baseline)
        return guard

    def test_a_process_the_run_did_not_start_is_left_alone(self):
        # The reader's own 한글, with their unsaved document in it.
        theirs = FakeProcess(100, dies_after=None)
        guard = self._guard({100}, {100}, {100: theirs})

        self.assertEqual((), guard.clear())
        self.assertEqual([], theirs.requests)

    def test_a_process_that_refuses_to_terminate_is_killed(self):
        stubborn = FakeProcess(200, dies_after=2)
        guard = self._guard(set(), {200}, {200: stubborn})

        self.assertEqual((), guard.clear())
        self.assertEqual(["terminate", "kill"], stubborn.requests)

    def test_a_process_that_will_not_die_is_reported_not_swallowed(self):
        immortal = FakeProcess(300, dies_after=None)
        guard = self._guard(set(), {300}, {300: immortal})

        self.assertEqual((300,), guard.clear())

    def test_the_run_stops_rather_than_starting_another_instance(self):
        # This is the whole fix: 23 processes accumulated because each
        # restart carried on after the cleanup silently failed.
        immortal = FakeProcess(400, dies_after=None)
        guard = self._guard(set(), {400}, {400: immortal})

        with self.assertRaises(StrayProcessesRemain) as caught:
            guard.require_clear()
        self.assertEqual((400,), caught.exception.survivors)
        self.assertIn("400", str(caught.exception))

    def test_a_clean_run_that_left_something_behind_still_fails(self):
        immortal = FakeProcess(500, dies_after=None)
        guard = self._guard(set(), {500}, {500: immortal})

        with self.assertRaises(StrayProcessesRemain):
            with guard:
                pass

    def test_an_error_inside_the_run_is_not_masked_by_the_cleanup(self):
        immortal = FakeProcess(600, dies_after=None)
        guard = self._guard(set(), {600}, {600: immortal})

        with self.assertRaises(ZeroDivisionError):
            with guard:
                raise ZeroDivisionError("the real failure")


if __name__ == "__main__":
    unittest.main()
