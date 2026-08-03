"""Record human-only product Goal evidence without storing private notes."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from engine.storage.json_store import atomic_write_json, safe_read_json
from verification.product_goal_acceptance import (
    MANUAL_CHECK_IDS,
    MANUAL_PROTOCOL_IDS,
)

DEFAULT_EVIDENCE_PATH = Path(__file__).with_name(
    "product_goal_manual_acceptance.json"
)
PROTOCOL_IDS = MANUAL_PROTOCOL_IDS
FINAL_STATUSES = frozenset({"passed", "failed"})


def _blank_evidence() -> dict:
    return {
        "schema_version": 2,
        "checks": {
            check_id: {
                "status": "pending",
                "performed_at": "",
                "protocol_id": PROTOCOL_IDS[check_id],
                "attested": False,
            }
            for check_id in MANUAL_CHECK_IDS
        },
    }


def _performed_at(value: str | None) -> str:
    if not value:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("performed_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("performed_at must include a timezone offset")
    return parsed.isoformat(timespec="seconds")


def initialize_evidence(path: Path = DEFAULT_EVIDENCE_PATH) -> dict:
    evidence = _blank_evidence()
    atomic_write_json(path, evidence)
    return evidence


def record_evidence(
    path: Path,
    check_id: str,
    status: str,
    *,
    attested: bool = False,
    performed_at: str | None = None,
) -> dict:
    if check_id not in MANUAL_CHECK_IDS:
        raise ValueError(f"unknown manual check: {check_id}")
    normalized_status = str(status).strip().casefold()
    if normalized_status not in {"pending", *FINAL_STATUSES}:
        raise ValueError(f"unknown manual status: {status}")
    if normalized_status in FINAL_STATUSES and not attested:
        raise ValueError("passed/failed requires --attest after the real test")

    loaded = safe_read_json(path, default={})
    evidence = loaded if isinstance(loaded, dict) else {}
    checks = evidence.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    blank = _blank_evidence()
    for required_id in MANUAL_CHECK_IDS:
        current = checks.get(required_id)
        if not isinstance(current, dict):
            checks[required_id] = blank["checks"][required_id]

    checks[check_id] = {
        "status": normalized_status,
        "performed_at": (
            _performed_at(performed_at)
            if normalized_status in FINAL_STATUSES
            else ""
        ),
        "protocol_id": PROTOCOL_IDS[check_id],
        "attested": bool(attested and normalized_status in FINAL_STATUSES),
    }
    evidence = {"schema_version": 2, "checks": checks}
    atomic_write_json(path, evidence)
    return evidence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=DEFAULT_EVIDENCE_PATH,
        help="Private evidence JSON path; the repository default is gitignored.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("initialize", help="Create three pending checks.")
    record = subparsers.add_parser("record", help="Record one completed check.")
    record.add_argument("check_id", choices=MANUAL_CHECK_IDS)
    record.add_argument("status", choices=("passed", "failed", "pending"))
    record.add_argument("--performed-at")
    record.add_argument(
        "--attest",
        action="store_true",
        help="Confirm that a human actually completed the referenced protocol.",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "initialize":
            initialize_evidence(args.evidence)
        else:
            record_evidence(
                args.evidence,
                args.check_id,
                args.status,
                attested=args.attest,
                performed_at=args.performed_at,
            )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"manual_evidence={args.evidence} command={args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
