"""Secret-safe command interface used by the React credential manager."""

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.storage import SQLiteMigrationRunner
from modules.tools.memory import create_application_store, get_application_database_path

_MANAGEMENT_OPERATION_ID = "CREDENTIAL_MANAGEMENT"


def _store(output_dir: str, target: str):
    database_path = get_application_database_path({"output_dir": output_dir})
    if not Path(database_path).is_file():
        raise ValueError("credential database does not exist for the selected output directory")
    SQLiteMigrationRunner(database_path).migrate()
    return create_application_store(database_path, logical_target=target)


def _inventory(store: Any) -> dict[str, Any]:
    credentials = store.list_credential_inventory()
    return {
        "credentials": [
            {
                **credential,
                "history": store.list_credential_history(str(credential["credential_id"])),
                "rotation_requests": store.list_credential_rotation_requests(str(credential["credential_id"])),
            }
            for credential in credentials
        ]
    }


def execute_credential_management(argv: Sequence[str] | None = None) -> dict[str, Any]:
    """Run one management request and return JSON-safe result data for callers and tests."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--target", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    queue = subparsers.add_parser("queue-rotation")
    queue.add_argument("--credential-id", required=True)
    queue.add_argument("--reason", required=True)
    cancel = subparsers.add_parser("cancel-rotation")
    cancel.add_argument("--request-id", required=True)
    cancel.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    store = _store(args.output_dir, args.target)
    if args.command == "list":
        return _inventory(store)
    if args.command == "queue-rotation":
        maintenance_operation_id = f"OP_CREDENTIAL_ROTATION_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        request = store.create_credential_rotation_request(
            _MANAGEMENT_OPERATION_ID,
            args.credential_id,
            args.reason,
            maintenance_operation_id,
        )
        return {"request": request, "maintenance_objective": "Complete queued credential rotation request " + request["request_id"]}
    if args.command == "cancel-rotation":
        return {"request": store.cancel_credential_rotation_request(args.request_id, args.reason)}
    raise RuntimeError("unsupported credential management command")


def main() -> None:
    try:
        print(json.dumps(execute_credential_management(), sort_keys=True))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
