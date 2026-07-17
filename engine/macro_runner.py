"""Cancellable Python macro runner with unique temporary files."""

import os
import subprocess
import sys
import tempfile
import time

from engine.execution_runtime import ExecutionCancelled
from engine.execution_result import success_result
from engine.macro_worker import MACRO_WORKER_FLAG


class MacroTimeoutError(TimeoutError):
    def __init__(self, message, stdout="", stderr=""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class MacroExecutionError(RuntimeError):
    def __init__(self, message, returncode=None, stdout="", stderr=""):
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class MacroRunner:
    def __init__(self, controller=None, timeout=30):
        self.controller = controller
        self.timeout = timeout

    def run(self, code, argument=""):
        temp_path = ""
        stdout_path = ""
        stderr_path = ""
        stdout_file = None
        stderr_file = None
        process = None
        started = time.monotonic()
        execution_started = None
        try:
            if self.controller:
                self.controller.check_cancelled()
                self.controller.event("python_macro", "running")
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".py",
                prefix="jarvis-macro-", delete=False,
            ) as temp_file:
                temp_file.write(str(code))
                temp_path = temp_file.name
            stdout_file = tempfile.NamedTemporaryFile(
                mode="w+b", suffix=".log", prefix="jarvis-macro-stdout-",
                delete=False,
            )
            stderr_file = tempfile.NamedTemporaryFile(
                mode="w+b", suffix=".log", prefix="jarvis-macro-stderr-",
                delete=False,
            )
            stdout_path = stdout_file.name
            stderr_path = stderr_file.name
            command = build_macro_command(temp_path, argument)
            if self.controller:
                # Cancellation may arrive while Windows is creating the
                # temporary files. Do not launch a new child after that point.
                self.controller.check_cancelled()
            process = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # The user-facing timeout bounds the spawned macro. Temporary-file
            # creation and Windows process setup before Popen must not consume
            # the macro's execution budget on a busy machine.
            execution_started = time.monotonic()
            while process.poll() is None:
                if self.controller:
                    try:
                        self.controller.check_cancelled()
                    except ExecutionCancelled:
                        self._terminate(process)
                        raise
                if time.monotonic() - execution_started >= self.timeout:
                    self._terminate(process)
                    self._close_output_files(stdout_file, stderr_file)
                    stdout_file = stderr_file = None
                    stdout = self._read_tail(stdout_path)
                    stderr = self._read_tail(stderr_path)
                    raise MacroTimeoutError(
                        f"Python 매크로가 {self.timeout}초 안에 끝나지 않았습니다.",
                        stdout=stdout,
                        stderr=stderr,
                    )
                time.sleep(0.03)
            process.wait()
            self._close_output_files(stdout_file, stderr_file)
            stdout_file = stderr_file = None
            stdout = self._read_tail(stdout_path)
            stderr = self._read_tail(stderr_path)
            if process.returncode != 0:
                raise MacroExecutionError(
                    f"Python 매크로 종료 코드: {process.returncode}",
                    returncode=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
            result = success_result(
                "Python 매크로를 실행했습니다.",
                action="python_macro",
                verified=False,
                data={
                    "command_mode": "frozen_worker" if getattr(sys, "frozen", False) else "python",
                },
                verification_status="confirmation_required",
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
            )
            if self.controller:
                self.controller.event(
                    "python_macro", "success", {"duration_ms": result["duration_ms"]}
                )
            return result
        except Exception as error:
            if self.controller:
                self.controller.event(
                    "python_macro",
                    "cancelled" if isinstance(error, ExecutionCancelled) else "failed",
                    {"error": str(error)},
                )
            raise
        finally:
            if process is not None and process.poll() is None:
                self._terminate(process)
            self._close_output_files(stdout_file, stderr_file)
            for path in (temp_path, stdout_path, stderr_path):
                if not path:
                    continue
                try:
                    os.remove(path)
                except OSError:
                    pass

    @staticmethod
    def _terminate(process):
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    @staticmethod
    def _close_output_files(*files):
        for output in files:
            if output is not None and not output.closed:
                output.flush()
                output.close()

    @staticmethod
    def _read_tail(path, limit=4000):
        if not path:
            return ""
        try:
            with open(path, "rb") as output:
                output.seek(0, os.SEEK_END)
                size = output.tell()
                # UTF-8 can use four bytes per character. Reading a bounded byte
                # tail avoids loading arbitrarily large macro output into memory.
                output.seek(max(0, size - (limit * 4)))
                text = output.read().decode("utf-8", errors="replace")
                return text[-limit:]
        except OSError:
            return ""


def build_macro_command(temp_path, argument="", executable=None, frozen=None):
    executable = executable or sys.executable
    frozen = getattr(sys, "frozen", False) if frozen is None else bool(frozen)
    if frozen:
        return [executable, MACRO_WORKER_FLAG, temp_path, str(argument)]
    return [executable, temp_path, str(argument)]
