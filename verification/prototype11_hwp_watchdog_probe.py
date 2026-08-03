"""Verify bounded HWP workflow generation or a safely handled HWP timeout."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import queue
import shutil
import tempfile
import time
from pathlib import Path

import psutil

from engine.workflows import (
    HwpReportWriter,
    HwpSecurityModuleUnavailable,
    HwpWorkflowTimeout,
)
from verification.source_identity import source_identity


REPORT_PATH = Path(__file__).with_name(
    "prototype11_hwp_watchdog_report.json"
)
HWP_PROCESS_NAMES = frozenset({"hwp.exe", "hwp64.exe"})


def _process_ids() -> set[int]:
    result = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if (
                str(process.info.get("name") or "").casefold()
                in HWP_PROCESS_NAMES
            ):
                result.add(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def _stop_created_processes(baseline: set[int]) -> None:
    for process_id in _process_ids() - set(baseline):
        try:
            process = psutil.Process(process_id)
            process.terminate()
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.TimeoutExpired,
        ):
            continue


def _work_product() -> dict:
    return {
        "title": "JARVIS 한글 watchdog 검증",
        "metrics": [],
        "tables": [],
        "charts": [],
        "insights": ["소유 임시 문서만 사용합니다."],
        "source_files": [],
    }


def _owned_probe() -> dict:
    baseline = _process_ids()
    if baseline:
        return {
            "status": "skipped_user_hwp_running",
            "user_process_protected": True,
        }
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-hwp-watchdog-"))
    output_path = temp_dir / "watchdog-report.hwp"
    started = time.monotonic()
    try:
        writer = HwpReportWriter(timeout_seconds=60)
        try:
            artifact = writer.run(
                {
                    "output_path": str(output_path),
                    "preferences": {},
                },
                _work_product(),
            )
            elapsed = time.monotonic() - started
            checks = {
                "bounded_timeout_or_generation": elapsed <= 75,
                "generation_readback_or_typed_block": bool(
                    output_path.is_file()
                    and artifact.get("verification", {}).get(
                        "content_readback"
                    )
                ),
                "partial_output_absent_on_block": True,
            }
            outcome = "generation_verified"
        except (
            HwpWorkflowTimeout,
            HwpSecurityModuleUnavailable,
        ) as error:
            elapsed = time.monotonic() - started
            checks = {
                "bounded_timeout_or_generation": elapsed <= 75,
                "generation_readback_or_typed_block": (
                    error.error_type
                    in {"timeout", "environment_error"}
                    and error.retryable is True
                ),
                "partial_output_absent_on_block": (
                    not output_path.exists()
                ),
            }
            outcome = (
                "environment_timeout_handled"
                if error.error_type == "timeout"
                else "security_module_unavailable_blocked"
            )
        return {
            "status": "passed" if all(checks.values()) else "failed",
            "outcome": outcome,
            "elapsed_seconds": round(elapsed, 3),
            "owned_fixture_only": True,
            "user_process_protected": True,
            "checks": checks,
        }
    except Exception as error:
        return {
            "status": "failed",
            "error_type": str(
                getattr(error, "error_type", type(error).__name__)
            ),
            "exception_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
    finally:
        _stop_created_processes(baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _worker(output) -> None:
    output.put(_owned_probe())


def run_probe(timeout=90) -> dict:
    baseline = _process_ids()
    if baseline:
        result = {
            "status": "skipped_user_hwp_running",
            "user_process_protected": True,
        }
    else:
        context = multiprocessing.get_context("spawn")
        output = context.Queue(maxsize=1)
        process = context.Process(target=_worker, args=(output,))
        process.start()
        process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(5)
            _stop_created_processes(baseline)
            result = {
                "status": "failed",
                "error_type": "timeout",
                "message": (
                    "한글 watchdog 프로브가 자체 제한 시간 안에 "
                    "복귀하지 못했습니다."
                ),
                "user_process_protected": True,
            }
        else:
            try:
                result = output.get(timeout=2)
            except queue.Empty:
                result = {
                    "status": "failed",
                    "error_type": "execution_error",
                    "message": "한글 watchdog 작업자가 결과 없이 종료됐습니다.",
                    "user_process_protected": True,
                }
    zombies = _process_ids() - baseline
    result["owned_process_cleanup_verified"] = not zombies
    if zombies:
        _stop_created_processes(baseline)
        result["status"] = "failed"
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source_identity(),
        "probe": "prototype11_hwp_workflow_watchdog",
        "success": result.get("status") == "passed",
        "user_documents_modified": False,
        "paths_or_contents_reported": False,
        "result": result,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)
    report = run_probe(args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
