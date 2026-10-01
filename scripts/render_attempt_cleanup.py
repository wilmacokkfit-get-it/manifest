"""Operator-only inventory and approved cleanup of abandoned render attempts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.render_cleanup import (  # noqa: E402
    MIN_RETENTION_SECONDS,
    SQLiteCleanup,
    SupabaseCleanup,
    delete_approved,
    inventory,
)
from app.store import SupabaseStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory or explicitly delete one abandoned render attempt."
    )
    parser.add_argument(
        "--backend", required=True, choices=("sqlite", "supabase"),
        help="storage backend to inspect; there is no implicit default",
    )
    parser.add_argument("--venture-id", required=True, help="exact venture ID")
    parser.add_argument("--attempt", required=True, help="exact render attempt token")
    parser.add_argument(
        "--data-dir",
        help="SQLite data directory (required with --backend sqlite)",
    )
    parser.add_argument(
        "--retention-seconds",
        type=float,
        default=MIN_RETENTION_SECONDS,
        help=f"minimum age for file, venture update, and lease deadline (at least {MIN_RETENTION_SECONDS})",
    )
    parser.add_argument(
        "--output",
        help="also write the dry-run JSON plan to this file",
    )
    parser.add_argument(
        "--delete-plan",
        metavar="FILE",
        help="delete only candidates from a previously reviewed dry-run JSON plan",
    )
    parser.add_argument(
        "--approve",
        metavar="DIGEST",
        help="exact approval digest printed in the reviewed plan",
    )
    parser.add_argument(
        "--workers-stopped",
        action="store_true",
        help="assert that app workers and all other writers are stopped",
    )
    args = parser.parse_args()

    if args.backend == "sqlite" and not args.data_dir:
        parser.error("--data-dir is required with --backend sqlite")
    if args.backend == "supabase" and args.data_dir:
        parser.error("--data-dir applies only to --backend sqlite")

    deleting = args.delete_plan is not None
    if deleting:
        if not args.approve:
            parser.error("--delete-plan requires --approve DIGEST")
        if not args.workers_stopped:
            parser.error("--delete-plan requires --workers-stopped")
        if args.output:
            parser.error("--output is only available for a dry run")
    elif args.approve or args.workers_stopped:
        parser.error("--approve and --workers-stopped require --delete-plan")
    return args


async def open_backend(args: argparse.Namespace):
    if args.backend == "sqlite":
        return SQLiteCleanup(args.data_dir)

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (
        os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if not url or not key:
        raise ValueError(
            "Supabase cleanup requires SUPABASE_URL and a backend key "
            "(SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY)."
        )
    bucket = os.environ.get("SUPABASE_RENDERS_BUCKET", "renders")
    store = SupabaseStore(url, key, bucket)
    try:
        return SupabaseCleanup(store)
    except Exception:
        # SupabaseCleanup validates the exact bucket before any network access.
        await store._client.aclose()
        raise


def load_plan(path: str) -> dict:
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Cannot read a valid JSON cleanup plan.") from None
    if not isinstance(plan, dict):
        raise ValueError("Cleanup plan must be a JSON object.")
    return plan


def validate_plan_scope(plan: dict, args: argparse.Namespace, backend) -> None:
    if plan.get("version") != 1:
        raise ValueError("Unsupported cleanup plan version.")
    if plan.get("venture_id") != args.venture_id or plan.get("attempt") != args.attempt:
        raise ValueError("Cleanup plan venture ID and attempt must match the CLI arguments.")
    if plan.get("scope") != backend.identity:
        raise ValueError("Cleanup plan backend scope does not match the selected backend.")


def write_plan(path: str, plan: dict) -> None:
    try:
        # Do not let an output typo overwrite a database, render, or prior plan.
        with Path(path).open("x", encoding="utf-8") as output:
            output.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        raise ValueError("Cleanup output must be a new file; existing files are never overwritten.") from None
    except OSError:
        raise ValueError("Cannot write the cleanup plan to the requested output file.") from None


async def run(args: argparse.Namespace) -> None:
    backend = await open_backend(args)
    try:
        if args.delete_plan:
            plan = load_plan(args.delete_plan)
            validate_plan_scope(plan, args, backend)
            deleted = await delete_approved(
                backend, plan, args.approve, workers_stopped=args.workers_stopped
            )
            print(json.dumps({"deleted": deleted}, indent=2, sort_keys=True))
            return

        plan = await inventory(
            backend,
            args.venture_id,
            args.attempt,
            retention_seconds=args.retention_seconds,
        )
        serialized = json.dumps(plan, indent=2, sort_keys=True) + "\n"
        if args.output:
            write_plan(args.output, plan)
        sys.stdout.write(serialized)
    finally:
        await backend.close()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, OSError) as exc:
        # These messages are validation/IO text, but redact configured credentials
        # defensively in case a dependency includes one in its exception string.
        message = str(exc)
        for variable in ("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
            secret = os.environ.get(variable)
            if secret:
                message = message.replace(secret, "[redacted]")
                if secret.strip():
                    message = message.replace(secret.strip(), "[redacted]")
        print(f"render-attempt-cleanup: {message}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        # Never dump a traceback or HTTP request details; these can include
        # credential-bearing headers from a failed Supabase request.
        print(
            f"render-attempt-cleanup: operation failed ({type(exc).__name__}).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()