"""Print read-only operator SQL; never connect to Supabase or read credentials."""

import os
from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "supabase" / "storage-policy-audit.sql"
MARKER = "__RENDERS_BUCKET_LITERAL__"


def audit_sql(bucket: str) -> str:
    # Match the app's configured bucket exactly, without trimming or defaulting
    # an explicitly empty value. Reject control characters rather than emit them.
    if not bucket or any(ord(char) < 32 or ord(char) == 127 for char in bucket):
        raise ValueError("SUPABASE_RENDERS_BUCKET must be nonempty with no control characters.")
    literal = "E'" + bucket.replace("\\", "\\\\").replace("'", "''") + "'"
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        raise ValueError("Storage audit template must contain exactly one bucket marker.")
    return template.replace(MARKER, literal)


def main() -> None:
    try:
        sql = audit_sql(os.environ.get("SUPABASE_RENDERS_BUCKET", "renders"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(sql)


if __name__ == "__main__":
    main()