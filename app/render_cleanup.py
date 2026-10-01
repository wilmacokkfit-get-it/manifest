"""Conservative, operator-only abandoned-attempt inventory. No record writes."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from .store import JSON_FIELDS, RENDER_LEASE_SECONDS, WORK_LEASE_SECONDS, SupabaseStore

MIN_RETENTION_SECONDS = max(86400, 2 * RENDER_LEASE_SECONDS, 2 * WORK_LEASE_SECONDS)
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def strings(value):
    if isinstance(value, str):
        yield unquote(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SQLiteCleanup:
    """Open only an existing database, without migrations or directory creation."""

    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve()
        self.renders = self.root / "renders"
        self.db = sqlite3.connect((self.root / "manifest.db").as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.identity = {"backend": "sqlite", "directory": str(self.root)}

    async def records(self):
        rows = [dict(row) for row in self.db.execute("SELECT * FROM ventures ORDER BY id")]
        for row in rows:
            for key in JSON_FIELDS:
                if isinstance(row.get(key), str):
                    row[key] = json.loads(row[key])
        return rows

    async def objects(self, venture_id, attempt):
        if self.renders.is_symlink():
            raise ValueError("Refusing a symlinked renders directory.")
        result = []
        pattern = re.compile(re.escape(f"{venture_id}-{attempt}-") + r"[0-9]+\.(png|webp)")
        for path in sorted(self.renders.iterdir()):
            if not pattern.fullmatch(path.name):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            # Hardlinks may be shared, even when no database reference exists.
            if stat.st_nlink != 1:
                continue
            result.append({
                "path": path.name,
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "fingerprint": [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
            })
        return result

    async def remove(self, paths):
        for name in paths:
            if Path(name).name != name:
                raise ValueError("Not an exact local filename.")
            (self.renders / name).unlink()

    async def close(self):
        self.db.close()


class SupabaseCleanup:
    def __init__(self, store: SupabaseStore):
        self.store = store
        if not SAFE_COMPONENT.fullmatch(store.bucket):
            raise ValueError("Cleanup requires a simple exact bucket name.")
        self.identity = {"backend": "supabase", "url": store.url, "bucket": store.bucket}

    async def records(self):
        rows = []
        offset = 0
        while True:
            response = await self.store._client.get(
                "/rest/v1/ventures", params={"select": "*", "order": "id.asc", "offset": offset, "limit": 100},
                headers={"Prefer": "count=exact"},
            )
            response.raise_for_status()
            page = response.json()
            # Never treat a capped/partial reference scan as complete.
            content_range = response.headers.get("Content-Range", "")
            match = re.fullmatch(r"(?:[0-9]+-[0-9]+|\*)/([0-9]+)", content_range)
            if not match or not isinstance(page, list):
                raise ValueError("Cannot establish a complete venture reference inventory.")
            total = int(match[1])
            rows.extend(page)
            offset += len(page)
            if offset == total:
                if len({r["id"] for r in rows}) != total:
                    raise ValueError("Reference inventory changed during pagination.")
                return rows
            if not page or offset > total:
                raise ValueError("Incomplete venture reference inventory.")

    async def objects(self, venture_id, attempt):
        prefix = f"{venture_id}/{attempt}"
        result = []
        offset = 0
        while True:
            response = await self.store._client.post(
                f"/storage/v1/object/list/{self.store.bucket}",
                json={"prefix": prefix, "limit": 100, "offset": offset,
                      "sortBy": {"column": "name", "order": "asc"}},
            )
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list):
                raise ValueError("Invalid Storage listing.")
            for obj in page:
                if not re.fullmatch(r"[0-9]+\.(png|webp)", obj.get("name", "")) or not obj.get("id"):
                    continue
                # Require both creation and modification times; unknown age is protected.
                created, updated = timestamp(obj.get("created_at")), timestamp(obj.get("updated_at"))
                if created is None or updated is None:
                    continue
                result.append({"path": f"{prefix}/{obj['name']}",
                               "modified": max(created, updated).isoformat(),
                               "fingerprint": obj})
            offset += len(page)
            if len(page) < 100:
                return sorted(result, key=lambda obj: obj["path"])

    async def remove(self, paths):
        # Exact object keys only, never a bucket clear or folder/prefix deletion.
        response = await self.store._client.request(
            "DELETE", f"/storage/v1/object/{self.store.bucket}", json={"prefixes": paths},
        )
        response.raise_for_status()

    async def close(self):
        await self.store._client.aclose()


async def inventory(backend, venture_id, attempt, retention_seconds=MIN_RETENTION_SECONDS, *, now=None):
    for value in (venture_id, attempt):
        if not isinstance(value, str) or not SAFE_COMPONENT.fullmatch(value):
            raise ValueError("An exact safe venture ID and attempt token are required.")
    if not math.isfinite(retention_seconds) or retention_seconds < MIN_RETENTION_SECONDS:
        raise ValueError(f"Retention must be at least {MIN_RETENTION_SECONDS} seconds.")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=retention_seconds)
    rows = await backend.records()
    row = next((item for item in rows if item["id"] == venture_id), None)
    if row is None:
        raise ValueError("Venture not found; orphan ownership cannot be proved.")
    grounding = row.get("grounding") or {}
    marker = grounding.get("_refinement") or {}
    current = marker.get("render_claim_id")
    reasons = []
    if row.get("status") != "ready" or row.get("images_status") not in ("ready", "complete", "completed", "failed"):
        reasons.append("venture work is pending or its status is unknown")
    if marker.get("state") != "completed" or not isinstance(current, str) or not SAFE_COMPONENT.fullmatch(current):
        reasons.append("no completed replacement attempt establishes abandonment")
    if current == attempt:
        reasons.append("attempt is current")
    updated = timestamp(row.get("updated_at"))
    if updated is None or updated > cutoff:
        reasons.append("venture has not been stable for the retention period")
    for key in ("lease_until", "render_lease_until", "path_lease_until"):
        if marker.get(key):
            deadline = timestamp(marker[key])
            if deadline is None or deadline > cutoff:
                reasons.append("worker lease is unknown or still within retention")
    references = [text for item in rows for text in strings(item)]
    # Any claim anywhere may be shared. Do not rely only on the scoped row.
    if attempt in references:
        reasons.append("attempt token is referenced by a record")
    candidates, protected = [], []
    for obj in await backend.objects(venture_id, attempt):
        blocked = list(reasons)
        modified = timestamp(obj["modified"])
        if modified is None or modified > cutoff:
            blocked.append("object is too recent or its age is unknown")
        if any(obj["path"] in ref for ref in references):
            blocked.append("object is referenced by a record")
        if backend.identity["backend"] == "sqlite":
            for item in rows:
                # Hyphen-separated local names can collide with another venture's
                # legacy name or current attempt. Protect implicit references too.
                other_marker = (item.get("grounding") or {}).get("_refinement") or {}
                prefixes = [f"{item['id']}-"]
                if other_marker.get("render_claim_id"):
                    prefixes.append(f"{item['id']}-{other_marker['render_claim_id']}-")
                if any(re.fullmatch(re.escape(prefix) + r"[0-9]+\.(png|webp)", obj["path"])
                       for prefix in prefixes):
                    blocked.append("object overlaps a legacy or current local filename")
                    break
                if item["id"] != venture_id and re.fullmatch(
                    re.escape(f"{item['id']}-") + r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-[0-9]+\.(png|webp)",
                    obj["path"],
                ):
                    blocked.append("local filename has ambiguous venture ownership")
                    break
        if blocked:
            protected.append({"path": obj["path"], "reasons": blocked})
        else:
            candidates.append(obj)
    plan = {"version": 1, "scope": backend.identity, "venture_id": venture_id,
            "attempt": attempt, "retention_seconds": retention_seconds,
            "records_fingerprint": digest(rows), "candidates": candidates, "protected": protected}
    plan["approval"] = digest(plan)
    return plan


async def delete_approved(backend, plan, approval, *, workers_stopped=False):
    if not workers_stopped:
        raise ValueError("Stop app workers and other writers before approving deletion.")
    unsigned = {key: value for key, value in plan.items() if key != "approval"}
    if not approval or approval != plan.get("approval") or approval != digest(unsigned):
        raise ValueError("Explicit approval must match the exact reviewed inventory.")
    fresh = await inventory(backend, plan["venture_id"], plan["attempt"], plan["retention_seconds"])
    if fresh != plan:
        raise ValueError("Inventory changed; review a new dry run before deleting.")
    paths = [obj["path"] for obj in fresh["candidates"]]
    if paths:
        await backend.remove(paths)
    return paths