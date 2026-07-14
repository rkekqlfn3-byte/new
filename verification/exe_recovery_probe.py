"""Recover a deliberately damaged JSON file using packaged storage code."""

import json
import sys
from pathlib import Path

from engine.storage.json_store import get_recovery_events, safe_read_json


def main():
    target = Path(sys.argv[1]).resolve()
    output = Path(f"{target}.recovery.json")
    recovered = safe_read_json(target, {})
    events = get_recovery_events(clear=True)
    report = {
        "recovered_sequence": recovered.get("sequence"),
        "payload_present": bool(recovered.get("payload")),
        "recovery_event_count": len(events),
        "primary_valid_after_recovery": False,
    }
    try:
        with target.open("r", encoding="utf-8") as source:
            json.load(source)
        report["primary_valid_after_recovery"] = True
    except (OSError, ValueError):
        pass
    report["all_passed"] = (
        report["payload_present"]
        and report["recovery_event_count"] == 1
        and report["primary_valid_after_recovery"]
    )
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
