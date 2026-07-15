"""End-to-end phase 2 checks executed by the frozen macro worker."""

import json
import sys
import tempfile
import threading
from pathlib import Path

from engine.action_executor import ActionConfirmationRequired, ActionExecutor
from engine.execution_runtime import ExecutionCancelled, ExecutionController
from engine.managers.dict_manager import DictionaryManager


class RetryProbeExecutor(ActionExecutor):
    def __init__(self, action, failures):
        super().__init__({})
        self.action = action
        self.failures = failures
        self.calls = 0

    def _execute_rendered_step(self, step, index, step_results):
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("temporary probe failure")

    def _verify_step(self, step):
        return {"status": "verified", "reason": "frozen retry probe"}


def verify_file_overwrite(root):
    source = root / "source.txt"
    target = root / "target.txt"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    executor = ActionExecutor({})
    plan = [{
        "action": "copy_file",
        "target": str(source),
        "text": str(target),
        "overwrite": False,
    }]
    confirmation = False
    try:
        executor.execute_plan(plan)
    except ActionConfirmationRequired as error:
        confirmation = error.status == "confirmation_required"
    protected = target.read_text(encoding="utf-8") == "old"
    plan[0]["overwrite"] = True
    executor.execute_plan(plan)
    replaced = target.read_text(encoding="utf-8") == "new"
    return confirmation and protected and replaced


def verify_retry_policy():
    safe = RetryProbeExecutor("clipboard_get", failures=1)
    safe.execute_plan([{"action": "clipboard_get"}])
    unsafe = RetryProbeExecutor("type_text", failures=2)
    unsafe_failed = False
    try:
        unsafe.execute_plan([{"action": "type_text", "text": "probe"}], retry_attempts=2)
    except OSError:
        unsafe_failed = True
    return safe.calls == 2 and unsafe_failed and unsafe.calls == 1


def verify_shared_dictionary_lock(root):
    dictionary_path = root / "dictionary.json"
    manager = DictionaryManager(str(dictionary_path))
    manager.macro_manager.save = lambda: None
    with manager.locked():
        manager.macro_dict["CONCURRENCY_PROBE"] = {
            "name": "probe",
            "synonyms": [],
            "type": "internal",
        }

    def add_values(offset):
        for value in range(offset, offset + 50):
            manager.macro_manager.add_macro_synonym(
                "CONCURRENCY_PROBE", f"value-{value}"
            )

    threads = [threading.Thread(target=add_values, args=(index * 50,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    values = manager.macro_dict["CONCURRENCY_PROBE"]["synonyms"]
    return manager._lock is manager.macro_manager.lock and len(values) == 400


def verify_cancel_recovery(root):
    controller = ExecutionController(str(root / "diagnostics.json"))
    controller.begin("cancel probe")
    cancelled = False
    controller.cancel()
    try:
        controller.check_cancelled()
    except ExecutionCancelled:
        cancelled = True
    controller.finish(False, status="cancelled")
    controller.begin("recovery probe")
    controller.check_cancelled()
    controller.finish(True, status="success")
    statuses = [record["status"] for record in controller.diagnostics()["records"]]
    return cancelled and statuses == ["cancelled", "success"]


def main():
    output_path = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="jarvis-phase2-exe-") as temp_dir:
        root = Path(temp_dir)
        results = {
            "file_overwrite_policy": verify_file_overwrite(root),
            "retry_policy": verify_retry_policy(),
            "shared_dictionary_lock": verify_shared_dictionary_lock(root),
            "cancel_recovery": verify_cancel_recovery(root),
        }
    results["all_passed"] = all(results.values())
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not results["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
