"""Collect reproducible stage-12 size and local execution measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from engine.parser import CommandParser

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "stage12_performance_report.json"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def source_metrics():
    extensions = {".py", ".js", ".html", ".css", ".json", ".bat", ".md"}
    excluded = {".venv", "build", "dist", "__pycache__"}
    files = [
        path for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in extensions
        and not any(part in excluded for part in path.parts)
    ]
    return {"file_count": len(files), "total_bytes": sum(path.stat().st_size for path in files)}


def local_command_timings(parser, iterations=50):
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        result = parser.execute_command_result("지금 몇 시야?")
        samples.append((time.perf_counter() - started) * 1000)
        if not result.get("success") or result.get("action") != "time":
            raise RuntimeError("local time command failed during performance measurement")
    return samples


def learned_skill_timings(parser, iterations=20):
    skill = {
        "state": "active",
        "plan": [{"action": "wait", "seconds": 0.001}],
        "code": "",
        "learning": {"intent": "STAGE12_PERFORMANCE", "slots": []},
    }
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        result = parser.skill_executor.execute(
            "시스템",
            "성능반복",
            skill=skill,
            source="stage12_performance",
            record_usage=False,
            record_candidate=False,
        )
        samples.append((time.perf_counter() - started) * 1000)
        if not result.get("success"):
            raise RuntimeError("learned skill failed during performance measurement")
    return samples


def describe(samples):
    ordered = sorted(samples)
    percentile_index = min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))
    return {
        "iterations": len(samples),
        "average_ms": round(statistics.fmean(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[percentile_index], 3),
        "max_ms": round(max(samples), 3),
    }


def main():
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--cold-start", type=float, required=True)
    argument_parser.add_argument("--restart", type=float, required=True)
    argument_parser.add_argument("--working-set", type=int, required=True)
    argument_parser.add_argument("--private-memory", type=int, required=True)
    argument_parser.add_argument("--idle-cpu", type=float, required=True)
    argument_parser.add_argument("--ui-local-response", type=float, required=True)
    args = argument_parser.parse_args()

    parser = CommandParser()
    local_samples = local_command_timings(parser)
    learned_samples = learned_skill_timings(parser)
    exe = ROOT / "dist" / "Jarvis" / "Jarvis.exe"
    archive = ROOT / "dist" / "Jarvis.zip"
    dist_files = [path for path in (ROOT / "dist" / "Jarvis").rglob("*") if path.is_file()]
    baseline = json.loads(
        (ROOT / "verification" / "phase0_baseline_report.json").read_text(encoding="utf-8")
    )
    baseline_exe_size = int(baseline["packaged_executable"]["size"])
    exe_size = exe.stat().st_size
    report = {
        "stage": 12,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "success": True,
        "distribution": {
            "exe_bytes": exe_size,
            "exe_sha256": sha256(exe),
            "archive_bytes": archive.stat().st_size,
            "archive_sha256": sha256(archive),
            "bundle_file_count": len(dist_files),
            "bundle_total_bytes": sum(path.stat().st_size for path in dist_files),
        },
        "source": source_metrics(),
        "runtime": {
            "cold_start_seconds": args.cold_start,
            "restart_seconds": args.restart,
            "idle_working_set_bytes": args.working_set,
            "idle_private_memory_bytes": args.private_memory,
            "idle_cpu_percent": args.idle_cpu,
            "ui_local_command_response_ms": args.ui_local_response,
        },
        "local_command": describe(local_samples),
        "learned_macro": describe(learned_samples),
        "ai_call": {
            "average_response_ms": None,
            "status": "not_measured_external_dependency",
            "reason": "Stage 12 verification does not spend or expose a user's external AI credential.",
        },
        "baseline_comparison": {
            "phase0_exe_bytes": baseline_exe_size,
            "exe_size_change_percent": round((exe_size - baseline_exe_size) * 100 / baseline_exe_size, 3),
            "stage6_performance_baseline_available": False,
            "note": "Stage 6 recorded functional results but no comparable startup, memory, CPU, or latency baseline.",
        },
        "acceptance": {
            "cold_start_under_5_seconds": args.cold_start < 5,
            "restart_under_5_seconds": args.restart < 5,
            "idle_cpu_under_2_percent": args.idle_cpu < 2,
            "ui_local_response_under_1_second": args.ui_local_response < 1000,
            "exe_growth_under_10_percent": (exe_size - baseline_exe_size) / baseline_exe_size < 0.10,
        },
    }
    report["success"] = all(report["acceptance"].values())
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
