"""Continuously persist a large JSON document until the parent kills the EXE."""

import sys
from pathlib import Path

from engine.storage.json_store import atomic_write_json


def main():
    target = Path(sys.argv[1]).resolve()
    ready = Path(f"{target}.ready")
    payload = "자비스-강제종료-검증" * 20000
    atomic_write_json(target, {"sequence": 0, "payload": payload})
    ready.write_text("ready", encoding="utf-8")
    sequence = 1
    while True:
        atomic_write_json(target, {"sequence": sequence, "payload": payload})
        sequence += 1


if __name__ == "__main__":
    main()
