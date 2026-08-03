"""Deterministic tests for performance regression calculations."""

import json
import unittest
from pathlib import Path
from unittest import mock

from verification.maintenance_performance import (
    DEFAULT_BASELINE,
    METRIC_POLICY,
    SIZE_POLICY,
    compare_to_baseline,
    describe,
    source_sizes,
)


class MaintenancePerformanceContractTests(unittest.TestCase):
    def _baseline(self, p95=None, size=1_000_000):
        return {
            "metrics": {
                name: {
                    "p95_ms": (
                        p95
                        if p95 is not None
                        else min(10.0, policy["absolute_p95_ms"] / 2)
                    )
                }
                for name, policy in METRIC_POLICY.items()
            },
            "sizes": {name: size for name in SIZE_POLICY},
        }

    def _metrics(self, p95=None):
        return {
            name: {
                "p95_ms": (
                    p95
                    if p95 is not None
                    else min(10.0, policy["absolute_p95_ms"] / 2)
                )
            }
            for name, policy in METRIC_POLICY.items()
        }

    def test_describe_uses_nearest_rank_p95(self):
        result = describe(list(range(1, 21)))
        self.assertEqual(19.0, result["p95_ms"])
        self.assertEqual(10.5, result["median_ms"])

    def test_equal_baseline_passes_every_check(self):
        sizes = {name: 1_000_000 for name in SIZE_POLICY}
        checks = compare_to_baseline(
            self._metrics(), sizes, self._baseline()
        )
        self.assertTrue(all(item["passed"] for item in checks))

    def test_large_latency_regression_fails(self):
        sizes = {name: 1_000_000 for name in SIZE_POLICY}
        checks = compare_to_baseline(
            self._metrics(p95=10_000), sizes, self._baseline()
        )
        latency = [item for item in checks if item["check"].endswith("_p95")]
        self.assertTrue(latency)
        self.assertTrue(all(not item["passed"] for item in latency))

    def test_small_fast_metric_jitter_is_allowed(self):
        baseline = self._baseline(p95=0.01)
        metrics = self._metrics(p95=0.02)
        sizes = {name: 1_000_000 for name in SIZE_POLICY}
        checks = compare_to_baseline(metrics, sizes, baseline)
        normalize = next(
            item for item in checks
            if item["check"] == "execution_result_normalize_p95"
        )
        self.assertTrue(normalize["passed"])

    def test_source_size_growth_has_a_bounded_allowance(self):
        sizes = {name: 10_000_000 for name in SIZE_POLICY}
        checks = compare_to_baseline(
            self._metrics(), sizes, self._baseline(size=1_000_000)
        )
        size_checks = [item for item in checks if not item["check"].endswith("_p95")]
        self.assertTrue(all(not item["passed"] for item in size_checks))

    def test_source_sizes_include_visible_untracked_source(self):
        with mock.patch("verification.maintenance_performance.subprocess.run") as run:
            with mock.patch.object(Path, "is_file", return_value=True), \
                    mock.patch.object(Path, "stat") as stat:
                run.side_effect = (
                    mock.Mock(returncode=0, stdout=b"engine/a.py\0"),
                    mock.Mock(returncode=0, stdout=b"verification/new.py\0"),
                )
                stat.return_value.st_size = 10
                with mock.patch.object(Path, "rglob", return_value=[]):
                    result = source_sizes(Path("project"))

        self.assertEqual(2, result["tracked_source_files"])
        self.assertEqual(20, result["tracked_source_bytes"])

    def test_checked_in_baseline_covers_every_policy(self):
        baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(set(METRIC_POLICY), set(baseline["metrics"]))
        self.assertTrue(set(SIZE_POLICY).issubset(baseline["sizes"]))


if __name__ == "__main__":
    unittest.main()
