"""Side-effect-free Python performance baseline and regression gate."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from engine.execution_result import normalize_execution_result
from engine.storage.json_store import atomic_write_json
from verification.utterance_acceptance_battery import _make_parser


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = Path(__file__).with_name("maintenance_performance_baseline.json")
DEFAULT_REPORT = Path(__file__).with_name("maintenance_performance_report.json")
METRIC_POLICY = {
    "startup_preflight": {"ratio": 1.50, "jitter_ms": 250.0, "absolute_p95_ms": 3000.0},
    "parser_initialization": {"ratio": 1.50, "jitter_ms": 75.0, "absolute_p95_ms": 1500.0},
    "local_parse": {"ratio": 1.35, "jitter_ms": 0.75, "absolute_p95_ms": 30.0},
    "local_command": {"ratio": 1.35, "jitter_ms": 1.0, "absolute_p95_ms": 75.0},
    "execution_result_normalize": {"ratio": 1.50, "jitter_ms": 0.02, "absolute_p95_ms": 1.0},
    "atomic_json_write": {"ratio": 1.50, "jitter_ms": 8.0, "absolute_p95_ms": 150.0},
}
SIZE_POLICY = {
    "tracked_source_bytes": {"ratio": 1.15, "jitter_bytes": 256 * 1024},
    "engine_python_bytes": {"ratio": 1.15, "jitter_bytes": 128 * 1024},
}


def describe(samples):
    if not samples:
        raise ValueError("performance samples must not be empty")
    ordered = sorted(float(value) for value in samples)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return {
        "iterations": len(ordered),
        "median_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[index], 4),
        "max_ms": round(ordered[-1], 4),
    }


def _timed(callback, iterations):
    samples = []
    for _ in range(max(1, int(iterations))):
        started = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - started) * 1000)
    return describe(samples)


def _startup_preflight(iterations):
    def run_once():
        result = subprocess.run(
            [sys.executable, "-m", "engine.startup_check"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError("startup preflight failed during benchmark")

    return _timed(run_once, iterations)


def _parser_initialization(root: Path, iterations):
    counter = {"value": 0}

    def create_once():
        counter["value"] += 1
        parser = _make_parser(root / f"parser-{counter['value']}")
        if parser.dict_mgr.get_ai_config().get("routing_mode") != "local_only":
            raise RuntimeError("isolated parser did not use local-only routing")

    return _timed(create_once, iterations)


def _local_parse(parser, iterations):
    utterances = (
        "메모장 열어줘",
        "계산기 켜줘",
        "소리를 10 내려줘",
        "오늘 날짜 알려줘",
        "엑셀에서 A1:A10 합계 내줘",
    )
    index = {"value": 0}

    def parse_once():
        text = utterances[index["value"] % len(utterances)]
        index["value"] += 1
        result = parser.analyze_command(text)
        if not isinstance(result, dict) or "kind" not in result:
            raise RuntimeError("local parse returned an invalid result")

    return _timed(parse_once, iterations)


def _local_command(parser, iterations):
    def execute_once():
        result = parser.execute_command_result("지금 몇 시야?")
        if not result.get("success") or result.get("action") != "time":
            raise RuntimeError("local command escaped the deterministic route")

    return _timed(execute_once, iterations)


def _normalize_result(iterations):
    value = {
        "success": True,
        "message": "ok",
        "action": "benchmark",
        "verified": False,
        "data": {"kind": "isolated"},
    }

    def normalize_once():
        result = normalize_execution_result(value)
        if result.get("status") != "success":
            raise RuntimeError("result normalization failed")

    return _timed(normalize_once, iterations)


def _atomic_write(root: Path, iterations):
    counter = {"value": 0}

    def write_once():
        counter["value"] += 1
        atomic_write_json(
            root / "state.json",
            {"schema_version": 1, "value": counter["value"], "items": list(range(20))},
        )

    return _timed(write_once, iterations)


def source_sizes(project_root=ROOT):
    commands = (
        ["git", "ls-files", "-z"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    )
    names = set()
    for command in commands:
        result = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            names.update(
                name
                for name in result.stdout.decode("utf-8", errors="replace").split("\0")
                if name
            )
    visible = [project_root / name for name in sorted(names)]
    source_suffixes = {".py", ".js", ".html", ".css", ".json", ".md", ".bat", ".ps1"}
    visible_source = [
        path for path in visible if path.is_file() and path.suffix.casefold() in source_suffixes
    ]
    engine_python = list((project_root / "engine").rglob("*.py"))
    return {
        "tracked_source_files": len(visible_source),
        "tracked_source_bytes": sum(path.stat().st_size for path in visible_source),
        "engine_python_files": len(engine_python),
        "engine_python_bytes": sum(path.stat().st_size for path in engine_python),
    }


def collect_metrics(*, quick=False):
    counts = {
        "startup": 1 if quick else 3,
        "initialization": 2 if quick else 7,
        "parse": 50 if quick else 500,
        "command": 25 if quick else 200,
        "normalize": 200 if quick else 5000,
        "write": 5 if quick else 30,
    }
    with tempfile.TemporaryDirectory(prefix="jarvis-maintenance-performance-") as temp_dir:
        root = Path(temp_dir)
        parser = _make_parser(root / "shared-parser")
        metrics = {
            "startup_preflight": _startup_preflight(counts["startup"]),
            "parser_initialization": _parser_initialization(root, counts["initialization"]),
            "local_parse": _local_parse(parser, counts["parse"]),
            "local_command": _local_command(parser, counts["command"]),
            "execution_result_normalize": _normalize_result(counts["normalize"]),
            "atomic_json_write": _atomic_write(root, counts["write"]),
        }
    return metrics


def compare_to_baseline(metrics, sizes, baseline):
    checks = []
    baseline_metrics = baseline.get("metrics", {})
    for name, policy in METRIC_POLICY.items():
        current = float(metrics[name]["p95_ms"])
        previous = float(baseline_metrics[name]["p95_ms"])
        regression_limit = max(
            previous * policy["ratio"],
            previous + policy["jitter_ms"],
        )
        allowed = min(regression_limit, policy["absolute_p95_ms"])
        checks.append({
            "check": f"{name}_p95",
            "current": current,
            "baseline": previous,
            "allowed": round(allowed, 4),
            "passed": current <= allowed,
        })

    baseline_sizes = baseline.get("sizes", {})
    for name, policy in SIZE_POLICY.items():
        current = int(sizes[name])
        previous = int(baseline_sizes[name])
        allowed = max(
            int(previous * policy["ratio"]),
            previous + int(policy["jitter_bytes"]),
        )
        checks.append({
            "check": name,
            "current": current,
            "baseline": previous,
            "allowed": allowed,
            "passed": current <= allowed,
        })
    return checks


def environment_identity():
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }


def build_report(*, baseline_path=DEFAULT_BASELINE, quick=False, compare=True):
    metrics = collect_metrics(quick=quick)
    sizes = source_sizes()
    baseline = None
    checks = []
    baseline_error = None
    if compare:
        try:
            baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
            checks = compare_to_baseline(metrics, sizes, baseline)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            baseline_error = type(error).__name__
    success = (not compare or baseline_error is None) and all(
        item["passed"] for item in checks
    )
    return {
        "schema_version": 1,
        "probe": "maintenance_performance",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "environment": environment_identity(),
        "isolated_data_directory": True,
        "external_ai_calls": 0,
        "user_applications_started": False,
        "quick": bool(quick),
        "metrics": metrics,
        "sizes": sizes,
        "baseline_path": str(Path(baseline_path)),
        "baseline_error": baseline_error,
        "checks": checks,
        "success": success,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--report", type=Path, help="선택적 JSON 보고서 경로")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--no-baseline", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(
        baseline_path=args.baseline,
        quick=args.quick,
        compare=not args.no_baseline,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
