"""Refuse to leave 한글 and Excel processes behind.

The catalogue tools drive a real application, and some commands leave it in
a state where it will not close — a split window, full screen, a modal it
will not give up.  The tools restart the session and carry on, which is the
right call for the run but was leaving the old process alive: one run ended
with 23 한글 processes and one Excel still resident, invisible, holding
their documents open.  A person had to clear them by hand.

The old cleanup asked each process to terminate and moved on if it refused,
so nothing noticed the refusal and the next restart added another one.  Two
things fix that: ask harder, and stop rather than pile up.

- ``clear`` escalates — terminate, wait, kill, wait — and reports which
  process ids survived instead of swallowing the failure.
- ``require_clear`` raises when any survive, so a tool stops before starting
  another instance.  A run that ends early is recoverable; twenty-three
  hidden processes are the reader's problem to find.

Only processes this run started are ever touched.  The application the
reader already had open is in the baseline and stays untouched — the tools
run against a real desktop, and killing someone's unsaved document to tidy
up would be a far worse bug than the one this fixes.
"""

from __future__ import annotations

HWP_PROCESS_NAMES = frozenset({"hwp.exe", "hwp64.exe"})
EXCEL_PROCESS_NAMES = frozenset({"excel.exe"})

# Long enough for an application that is closing to finish, short enough
# that a wedged one does not hold the run for a minute.
TERMINATE_TIMEOUT_SECONDS = 5.0
KILL_TIMEOUT_SECONDS = 5.0


class StrayProcessesRemain(RuntimeError):
    """Processes this run started are still alive and would not close."""

    def __init__(self, names, survivors):
        self.survivors = tuple(sorted(survivors))
        super().__init__(
            f"{'/'.join(sorted(names))} 프로세스 {len(self.survivors)}개가 "
            f"종료를 거부했습니다: {', '.join(str(pid) for pid in self.survivors)}. "
            "새 인스턴스를 더 만들지 않고 멈춥니다. 작업 관리자에서 정리한 뒤 "
            "다시 실행해주세요."
        )


def process_ids(names) -> set:
    """Process ids currently running under any of ``names``."""
    import psutil

    wanted = {str(name).casefold() for name in names}
    found = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() in wanted:
                found.add(int(process.info["pid"]))
        except Exception:
            # A process that ended while the list was being read is not a
            # stray; it is exactly what this is trying to achieve.
            continue
    return found


class StrayProcessGuard:
    """Track which processes a run started, and insist they are gone."""

    def __init__(self, names):
        self.names = frozenset(str(name).casefold() for name in names)
        self.baseline = process_ids(self.names)

    def strays(self) -> set:
        return process_ids(self.names) - self.baseline

    def clear(self) -> tuple:
        """Close what this run started. Returns the ids that would not go."""
        import psutil

        survivors = []
        for process_id in sorted(self.strays()):
            try:
                process = psutil.Process(process_id)
            except Exception:
                continue
            for stop, timeout in (
                (process.terminate, TERMINATE_TIMEOUT_SECONDS),
                (process.kill, KILL_TIMEOUT_SECONDS),
            ):
                try:
                    stop()
                    process.wait(timeout=timeout)
                    break
                except psutil.NoSuchProcess:
                    break
                except Exception:
                    # Asked and refused, or not allowed to ask. Escalate to
                    # the next step, and if that was the last one it counts
                    # as a survivor below.
                    continue
            if process_id in self.strays():
                survivors.append(process_id)
        return tuple(survivors)

    def require_clear(self) -> None:
        """Clear, and refuse to go on if anything is still alive."""
        survivors = self.clear()
        if survivors:
            raise StrayProcessesRemain(self.names, survivors)

    def __enter__(self) -> "StrayProcessGuard":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        survivors = self.clear()
        if survivors and exc_type is None:
            # A run that finished cleanly but left processes behind has not
            # finished cleanly, and saying so is the whole point.
            raise StrayProcessesRemain(self.names, survivors)
        return False


__all__ = [
    "EXCEL_PROCESS_NAMES",
    "HWP_PROCESS_NAMES",
    "StrayProcessGuard",
    "StrayProcessesRemain",
    "process_ids",
]
