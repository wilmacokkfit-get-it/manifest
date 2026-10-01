"""Persistence for MANIFEST.

Two interchangeable backends:

* SQLiteStore   – zero-config default. One file in ./data. Good for local
                  runs, previews, and the first few thousand ventures.
* SupabaseStore – used automatically when SUPABASE_URL and
                  SUPABASE_SECRET_KEY (or legacy SUPABASE_SERVICE_ROLE_KEY)
                  are set. Talks to PostgREST and
                  Supabase Storage directly over HTTPS (no extra SDK).

Both expose the same async surface so server.py never cares which is live.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .provenance import now_iso

JSON_FIELDS = ("tiers", "roadmap", "grounding", "image_prompts", "images", "path")
WORK_LEASE_SECONDS = 300
RENDER_LEASE_SECONDS = 600


def lease_until(seconds: int = WORK_LEASE_SECONDS) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def lease_expired(marker: dict, key: str, updated_at: str) -> bool:
    """Legacy claims have no lease; give them the same bounded grace period."""
    try:
        raw = marker.get(key)
        deadline = datetime.fromisoformat((raw or updated_at).replace("Z", "+00:00"))
        if not raw:
            deadline += timedelta(seconds=WORK_LEASE_SECONDS)
        return deadline <= datetime.now(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        # A malformed legacy timestamp cannot establish a live lease.
        return True

VENTURE_COLUMNS = [
    "id", "slug", "owner_token", "idea_raw", "idea_hash", "provenance_code",
    "name", "tagline", "description", "category", "accent",
    "tiers", "roadmap", "grounding", "image_prompts", "images", "images_status",
    "visibility", "status", "signal_count", "buy_count", "build_count", "fund_count",
    "promoted_at", "released_at", "created_at", "updated_at", "path",
]


def compress_render(data: bytes, max_side: int = 1280, quality: int = 82) -> tuple[bytes, str, str]:
    """Shrink a generated PNG to a web-sized WebP. Falls back to the original
    bytes if Pillow is missing or the image can't be decoded."""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(data)).convert("RGB")
        im.thumbnail((max_side, max_side))
        out = BytesIO()
        im.save(out, "WEBP", quality=quality, method=5)
        return out.getvalue(), "webp", "image/webp"
    except Exception:
        return data, "png", "image/png"


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

class RenderAccess:
    """Translate stored references without changing existing records or objects."""

    async def reconcile_venture(self, venture: dict) -> dict:
        """Lazily reclaim abandoned text work, never a successful refinement.

        Compare the entire observed snapshot when writing: a new claim or a
        concurrent path/render update must win over this recovery attempt.
        """
        grounding = dict(venture.get("grounding") or {})
        marker = grounding.get("_refinement")
        marker = dict(marker) if isinstance(marker, dict) else {}
        status = venture.get("status")
        if status == "refining" and marker.get("state") == "pending":
            if not lease_expired(marker, "lease_until", venture.get("updated_at")):
                return venture
            grounding.pop("_refinement", None)
        elif status == "planning":
            if not lease_expired(marker, "path_lease_until", venture.get("updated_at")):
                return venture
            marker.pop("path_claim_id", None)
            marker.pop("path_lease_until", None)
            # Choosing a path still consumes the optional refinement.
            marker.setdefault("state", "skipped")
            grounding["_refinement"] = marker
        else:
            return venture
        saved = await self._compare_venture(venture, {"grounding": grounding, "status": "ready"})
        return saved or await self.get_by_id(venture["id"]) or venture

    async def claim_refinement_render(self, venture_id: str) -> dict | None:
        current = await self.get_by_id(venture_id)
        if not current or current.get("images_status") != "pending":
            return None
        grounding = dict(current.get("grounding") or {})
        marker = dict(grounding.get("_refinement") or {})
        if marker.get("state") != "completed":
            return None
        if marker.get("render_claim_id") and not lease_expired(
            marker, "render_lease_until", current.get("updated_at")
        ):
            return None
        marker.update(render_claim_id=_new_id(), render_lease_until=lease_until(RENDER_LEASE_SECONDS))
        grounding["_refinement"] = marker
        return await self._compare_venture(current, {"grounding": grounding, "images": []})

    @staticmethod
    def _render_filename(venture: dict, idx: int, ext: str) -> str:
        marker = (venture.get("grounding") or {}).get("_refinement") or {}
        token = marker.get("render_claim_id")
        if token and re.fullmatch(r"[a-zA-Z0-9-]+", token):
            return f"{venture['id']}-{token}-{idx}.{ext}"
        return f"{venture['id']}-{idx}.{ext}"

    def _legacy_render_url(self, venture_id: str, idx: int, ext: str) -> str:
        raise NotImplementedError

    def render_url(self, venture: dict, idx: int) -> str | None:
        images = venture.get("images") or []
        if idx < 0 or idx >= len(images) or not images[idx]:
            return None
        venture_id = venture["id"]
        if not re.fullmatch(r"[a-zA-Z0-9-]+", venture_id):
            return None
        for ext in ("png", "webp"):
            guarded = f"/api/venture-images/{venture_id}/{idx}.{ext}"
            if images[idx] in (guarded, self._legacy_render_url(venture_id, idx, ext)):
                return guarded
        # Never fetch an arbitrary URL from a stored record.
        return None


class SQLiteStore(RenderAccess):
    kind = "sqlite"

    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.renders_dir = self.dir / "renders"
        self.renders_dir.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.dir / "manifest.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS ventures (
                  id TEXT PRIMARY KEY,
                  slug TEXT UNIQUE NOT NULL,
                  owner_token TEXT NOT NULL,
                  idea_raw TEXT NOT NULL,
                  idea_hash TEXT NOT NULL,
                  provenance_code TEXT NOT NULL,
                  name TEXT, tagline TEXT, description TEXT, category TEXT, accent TEXT,
                  tiers TEXT, roadmap TEXT, grounding TEXT, image_prompts TEXT,
                  images TEXT DEFAULT '[]',
                  images_status TEXT DEFAULT 'pending',
                  visibility TEXT DEFAULT 'private',
                  status TEXT DEFAULT 'generating',
                  signal_count INTEGER DEFAULT 0,
                  buy_count INTEGER DEFAULT 0,
                  build_count INTEGER DEFAULT 0,
                  fund_count INTEGER DEFAULT 0,
                  promoted_at TEXT, released_at TEXT,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                  id TEXT PRIMARY KEY,
                  venture_id TEXT NOT NULL,
                  type TEXT NOT NULL,
                  email TEXT NOT NULL,
                  price REAL,
                  created_at TEXT NOT NULL,
                  UNIQUE(venture_id, type, email)
                );
                CREATE TABLE IF NOT EXISTS waitlist (
                  id TEXT PRIMARY KEY,
                  name TEXT, email TEXT UNIQUE NOT NULL, role TEXT,
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ventures_public ON ventures(visibility, promoted_at, signal_count);
                CREATE TABLE IF NOT EXISTS needs (
                  id TEXT PRIMARY KEY,
                  venture_id TEXT NOT NULL,
                  step_n INTEGER,
                  title TEXT NOT NULL,
                  detail TEXT,
                  status TEXT DEFAULT 'open',
                  offer_count INTEGER DEFAULT 0,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS offers (
                  id TEXT PRIMARY KEY,
                  need_id TEXT NOT NULL,
                  email TEXT NOT NULL,
                  name TEXT,
                  message TEXT,
                  created_at TEXT NOT NULL,
                  UNIQUE(need_id, email)
                );
                """
            )
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(ventures)").fetchall()}
            if "path" not in cols:
                self._db.execute("ALTER TABLE ventures ADD COLUMN path TEXT")
            self._db.commit()

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for f in JSON_FIELDS:
            if d.get(f) is not None and isinstance(d[f], str):
                try:
                    d[f] = json.loads(d[f])
                except Exception:
                    d[f] = None
        return d

    @staticmethod
    def _encode(fields: dict) -> dict:
        out = {}
        for k, v in fields.items():
            out[k] = json.dumps(v) if k in JSON_FIELDS and v is not None else v
        return out

    # -- ventures -----------------------------------------------------------
    async def create_venture(self, row: dict) -> dict:
        row = dict(row)
        row.setdefault("id", _new_id())
        row.setdefault("created_at", now_iso())
        row["updated_at"] = row["created_at"]
        enc = self._encode(row)
        cols = ", ".join(enc.keys())
        qs = ", ".join("?" for _ in enc)
        with self._lock:
            self._db.execute(f"INSERT INTO ventures ({cols}) VALUES ({qs})", list(enc.values()))
            self._db.commit()
        return await self.get_by_id(row["id"])

    async def update_venture(self, venture_id: str, fields: dict) -> dict | None:
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        enc = self._encode(fields)
        sets = ", ".join(f"{k} = ?" for k in enc)
        with self._lock:
            self._db.execute(f"UPDATE ventures SET {sets} WHERE id = ?", [*enc.values(), venture_id])
            self._db.commit()
        return await self.get_by_id(venture_id)

    async def _compare_venture(self, current: dict, fields: dict) -> dict | None:
        fields = {**fields, "updated_at": now_iso()}
        enc = self._encode(fields)
        sets = ", ".join(f"{k} = ?" for k in enc)
        with self._lock:
            cur = self._db.execute(
                f"""UPDATE ventures SET {sets} WHERE id = ? AND status = ?
                    AND images_status = ? AND updated_at = ?
                    AND json(grounding) IS json(?)""",
                [*enc.values(), current["id"], current["status"], current["images_status"],
                 current["updated_at"], json.dumps(current["grounding"]) if current.get("grounding") is not None else None],
            )
            self._db.commit()
        return await self.get_by_id(current["id"]) if cur.rowcount else None

    async def update_refinement_images(self, venture_id: str, claim_id: str, fields: dict) -> dict | None:
        enc = self._encode({**fields, "updated_at": now_iso()})
        sets = ", ".join(f"{k} = ?" for k in enc)
        with self._lock:
            cur = self._db.execute(
                f"""UPDATE ventures SET {sets} WHERE id = ? AND images_status = 'pending'
                    AND json_extract(grounding, '$._refinement.render_claim_id') = ?""",
                [*enc.values(), venture_id, claim_id],
            )
            self._db.commit()
        return await self.get_by_id(venture_id) if cur.rowcount else None

    async def claim_refinement(
        self, venture_id: str, context: str | None = None, *, skipped: bool = False,
        require_images_finished: bool = True, claim_id: str | None = None,
    ) -> dict | None:
        """Atomically consume the one refinement opportunity (or mark it skipped)."""
        marker = {"state": "skipped"} if skipped else {
            "state": "pending", "context": context, "claim_id": claim_id or _new_id(),
            "lease_until": lease_until(),
        }
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT * FROM ventures WHERE id = ?", [venture_id]).fetchone()
            venture = self._row_to_dict(row)
            eligible = bool(
                venture
                and venture.get("status") == "ready"
                and venture.get("visibility") == "private"
                and venture.get("path") is None
                and "_refinement" not in (venture.get("grounding") or {})
                and (not require_images_finished or venture.get("images_status") != "pending")
            )
            if not eligible:
                self._db.rollback()
                return None
            grounding = dict(venture.get("grounding") or {})
            grounding["_refinement"] = marker
            claimed_status = "ready" if skipped else "refining"
            self._db.execute(
                """UPDATE ventures SET grounding = ?, status = ?, updated_at = ?
                   WHERE id = ? AND status = 'ready' AND visibility = 'private' AND path IS NULL""",
                [json.dumps(grounding), claimed_status, now_iso(), venture_id],
            )
            changed = self._db.execute("SELECT changes()").fetchone()[0]
            self._db.commit()
        return await self.get_by_id(venture_id) if changed else None

    async def finish_refinement(
        self, venture_id: str, fields: dict, claim_id: str | None = None,
    ) -> dict | None:
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        enc = self._encode(fields)
        sets = ", ".join(f"{k} = ?" for k in enc)
        claim_condition = " AND json_extract(grounding, '$._refinement.claim_id') = ?" if claim_id else ""
        values = [*enc.values(), venture_id]
        if claim_id:
            values.append(claim_id)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            cur = self._db.execute(
                f"""UPDATE ventures SET {sets}
                    WHERE id = ? AND status = 'refining' AND visibility = 'private' AND path IS NULL
                    {claim_condition}""",
                values,
            )
            if cur.rowcount:
                self._db.commit()
            else:
                self._db.rollback()
        return await self.get_by_id(venture_id) if cur.rowcount else None

    async def clear_refinement_claim(
        self, venture_id: str, original_grounding: dict, claim_id: str | None = None,
    ) -> None:
        """Restore the untouched original grounding after a failed provider call."""
        claim_condition = " AND json_extract(grounding, '$._refinement.claim_id') = ?" if claim_id else ""
        values = [json.dumps(original_grounding or {}), now_iso(), venture_id]
        if claim_id:
            values.append(claim_id)
        with self._lock:
            self._db.execute(
                f"""UPDATE ventures SET grounding = ?, status = 'ready', updated_at = ?
                   WHERE id = ? AND status = 'refining' {claim_condition}""",
                values,
            )
            self._db.commit()

    async def claim_path(self, venture_id: str, claim_id: str | None = None) -> dict | None:
        """Reserve path generation so refinement cannot claim the same ready venture."""
        claim_id = claim_id or _new_id()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT * FROM ventures WHERE id = ?", [venture_id]).fetchone()
            venture = self._row_to_dict(row)
            if not venture or venture.get("status") != "ready":
                self._db.rollback()
                return None
            grounding = dict(venture.get("grounding") or {})
            marker = grounding.get("_refinement")
            marker = dict(marker) if isinstance(marker, dict) else {"state": "skipped"}
            marker["path_claim_id"] = claim_id
            marker["path_lease_until"] = lease_until()
            grounding["_refinement"] = marker
            cur = self._db.execute(
                """UPDATE ventures SET status = 'planning', grounding = ?, updated_at = ?
                   WHERE id = ? AND status = 'ready'""",
                [json.dumps(grounding), now_iso(), venture_id],
            )
            if cur.rowcount:
                self._db.commit()
            else:
                self._db.rollback()
        return await self.get_by_id(venture_id) if cur.rowcount else None

    async def finish_path(self, venture_id: str, path: dict, claim_id: str | None = None) -> dict | None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT * FROM ventures WHERE id = ?", [venture_id]).fetchone()
            venture = self._row_to_dict(row)
            marker = ((venture or {}).get("grounding") or {}).get("_refinement") or {}
            if not venture or not claim_id or marker.get("path_claim_id") != claim_id:
                self._db.rollback()
                return None
            grounding = dict(venture.get("grounding") or {})
            marker = dict(grounding.get("_refinement") or {})
            marker.pop("path_claim_id", None)
            marker.pop("path_lease_until", None)
            grounding["_refinement"] = marker
            cur = self._db.execute(
                """UPDATE ventures SET path = ?, status = 'ready', grounding = ?, updated_at = ?
                   WHERE id = ? AND status = 'planning'
                     AND json_extract(grounding, '$._refinement.path_claim_id') = ?""",
                [json.dumps(path), json.dumps(grounding), now_iso(), venture_id, claim_id],
            )
            if cur.rowcount:
                self._db.commit()
            else:
                self._db.rollback()
        return await self.get_by_id(venture_id) if cur.rowcount else None

    async def release_path_claim(self, venture_id: str, claim_id: str | None = None) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT * FROM ventures WHERE id = ?", [venture_id]).fetchone()
            venture = self._row_to_dict(row)
            marker = ((venture or {}).get("grounding") or {}).get("_refinement") or {}
            if (
                not venture or not claim_id or venture.get("status") != "planning"
                or marker.get("path_claim_id") != claim_id
            ):
                self._db.rollback()
                return
            grounding = dict(venture.get("grounding") or {})
            marker = dict(grounding.get("_refinement") or {})
            marker.pop("path_claim_id", None)
            marker.pop("path_lease_until", None)
            grounding["_refinement"] = marker
            self._db.execute(
                """UPDATE ventures SET status = 'ready', grounding = ?, updated_at = ?
                   WHERE id = ? AND status = 'planning'
                     AND json_extract(grounding, '$._refinement.path_claim_id') = ?""",
                [json.dumps(grounding), now_iso(), venture_id, claim_id],
            )
            self._db.commit()

    async def reset_path(self, venture_id: str) -> dict | None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT * FROM ventures WHERE id = ?", [venture_id]).fetchone()
            venture = self._row_to_dict(row)
            if not venture:
                self._db.rollback()
                return None
            grounding = dict(venture.get("grounding") or {})
            if venture.get("path") is not None and "_refinement" not in grounding:
                grounding["_refinement"] = {"state": "skipped"}
            self._db.execute(
                "UPDATE ventures SET path = NULL, grounding = ?, updated_at = ? WHERE id = ?",
                [json.dumps(grounding), now_iso(), venture_id],
            )
            self._db.commit()
        return await self.get_by_id(venture_id)

    async def release_venture(self, venture_id: str, released_at: str) -> dict | None:
        """Release only if a refinement has not atomically claimed the venture."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT * FROM ventures WHERE id = ?", [venture_id]).fetchone()
            venture = self._row_to_dict(row)
            if not venture or venture.get("status") == "refining":
                self._db.rollback()
                return None
            grounding = dict(venture.get("grounding") or {})
            if "_refinement" not in grounding:
                grounding["_refinement"] = {"state": "skipped"}
            cur = self._db.execute(
                """UPDATE ventures SET visibility = 'public', released_at = ?, grounding = ?, updated_at = ?
                   WHERE id = ? AND visibility = ? AND status != 'refining'""",
                [
                    released_at if venture.get("visibility") != "public" else venture.get("released_at"),
                    json.dumps(grounding), now_iso(), venture_id, venture.get("visibility"),
                ],
            )
            if cur.rowcount:
                self._db.commit()
            else:
                self._db.rollback()
        return await self.get_by_id(venture_id) if cur.rowcount else None

    async def get_by_id(self, venture_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM ventures WHERE id = ?", [venture_id]).fetchone()
        return self._row_to_dict(row)

    async def get_by_slug(self, slug: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM ventures WHERE slug = ?", [slug]).fetchone()
        return self._row_to_dict(row)

    def slug_exists(self, slug: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM ventures WHERE slug = ?", [slug]).fetchone() is not None

    async def list_public(self, limit: int = 60) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT * FROM ventures
                WHERE visibility = 'public' AND status = 'ready'
                ORDER BY (promoted_at IS NULL) ASC, signal_count DESC, released_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def count_generations_since(self, since_iso: str) -> int:
        with self._lock:
            (n,) = self._db.execute("SELECT COUNT(*) FROM ventures WHERE created_at >= ?", [since_iso]).fetchone()
        return int(n)

    async def recent_concepts(self, owner_token: str, limit: int = 12) -> list[dict]:
        """Names and taglines of this creator's recent ventures plus the latest site-wide, so the engine never repeats itself."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT name, tagline, idea_raw FROM ventures
                WHERE name IS NOT NULL AND (owner_token = ? OR created_at >= datetime('now', '-2 days'))
                ORDER BY (owner_token = ?) DESC, created_at DESC LIMIT ?
                """,
                [owner_token, owner_token, limit],
            ).fetchall()
        return [{"name": r["name"], "tagline": r["tagline"], "idea": r["idea_raw"]} for r in rows]

    # -- signals ------------------------------------------------------------
    async def add_signal(self, venture_id: str, type_: str, email: str, price: float | None) -> bool:
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO signals (id, venture_id, type, email, price, created_at) VALUES (?,?,?,?,?,?)",
                    [_new_id(), venture_id, type_, email.lower(), price, now_iso()],
                )
                self._db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    async def signal_counts(self, venture_id: str) -> dict:
        with self._lock:
            rows = self._db.execute(
                "SELECT type, COUNT(*) AS n, AVG(price) AS avg_price FROM signals WHERE venture_id = ? GROUP BY type",
                [venture_id],
            ).fetchall()
        counts = {"buy": 0, "build": 0, "fund": 0, "avg_price": None}
        for r in rows:
            counts[r["type"]] = int(r["n"])
            if r["type"] == "buy" and r["avg_price"] is not None:
                counts["avg_price"] = round(float(r["avg_price"]), 2)
        counts["total"] = counts["buy"] + counts["build"] + counts["fund"]
        return counts

    # -- waitlist -----------------------------------------------------------
    async def add_waitlist(self, name: str, email: str, role: str) -> bool:
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO waitlist (id, name, email, role, created_at) VALUES (?,?,?,?,?)",
                    [_new_id(), name, email.lower(), role, now_iso()],
                )
                self._db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    # -- needs + offers (asks for help, and real people answering) -----------
    async def create_need(self, venture_id: str, step_n: int | None, title: str, detail: str) -> dict:
        nid = _new_id()
        with self._lock:
            self._db.execute(
                "INSERT INTO needs (id, venture_id, step_n, title, detail, status, offer_count, created_at) VALUES (?,?,?,?,?,?,?,?)",
                [nid, venture_id, step_n, title, detail, "open", 0, now_iso()],
            )
            self._db.commit()
        return await self.get_need(nid)

    async def get_need(self, need_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM needs WHERE id = ?", [need_id]).fetchone()
        return dict(row) if row else None

    async def list_needs(self, venture_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM needs WHERE venture_id = ? ORDER BY created_at", [venture_id]).fetchall()
        return [dict(r) for r in rows]

    async def set_need_status(self, need_id: str, status: str) -> None:
        with self._lock:
            self._db.execute("UPDATE needs SET status = ? WHERE id = ?", [status, need_id])
            self._db.commit()

    async def list_open_needs(self, limit: int = 40) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                """SELECT n.*, v.slug AS venture_slug, v.name AS venture_name, v.accent AS venture_accent
                   FROM needs n JOIN ventures v ON v.id = n.venture_id
                   WHERE n.status = 'open' AND v.visibility = 'public'
                   ORDER BY n.created_at DESC LIMIT ?""", [limit]).fetchall()
        return [dict(r) for r in rows]

    async def add_offer(self, need_id: str, email: str, name: str, message: str) -> bool:
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO offers (id, need_id, email, name, message, created_at) VALUES (?,?,?,?,?,?)",
                    [_new_id(), need_id, email.lower(), name, message, now_iso()],
                )
                self._db.execute("UPDATE needs SET offer_count = (SELECT COUNT(*) FROM offers WHERE need_id = ?) WHERE id = ?",
                                 [need_id, need_id])
                self._db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    async def list_offers(self, need_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM offers WHERE need_id = ? ORDER BY created_at", [need_id]).fetchall()
        return [dict(r) for r in rows]

    # -- renders ------------------------------------------------------------
    def _legacy_render_url(self, venture_id: str, idx: int, ext: str) -> str:
        return f"/renders/{venture_id}-{idx}.{ext}"

    async def read_render(self, venture: dict, idx: int) -> tuple[bytes, str] | None:
        url = self.render_url(venture, idx)
        if not url:
            return None
        ext = url.rsplit(".", 1)[1]
        path = self.renders_dir / self._render_filename(venture, idx, ext)
        if path.resolve().parent != self.renders_dir.resolve():
            return None
        try:
            return await asyncio.to_thread(path.read_bytes), f"image/{ext}"
        except FileNotFoundError:
            return None

    async def save_render(self, venture_id: str, idx: int, png: bytes, *, render_claim_id: str | None = None) -> str:
        data, ext, _ = await asyncio.to_thread(compress_render, png)
        filename = self._render_filename(
            {"id": venture_id, "grounding": {"_refinement": {"render_claim_id": render_claim_id}}}, idx, ext,
        )
        path = self.renders_dir / filename
        await asyncio.to_thread(path.write_bytes, data)
        return f"/api/venture-images/{venture_id}/{idx}.{ext}" if render_claim_id else f"/renders/{path.name}"


# ---------------------------------------------------------------------------
# Supabase (PostgREST + Storage)
# ---------------------------------------------------------------------------

class SupabaseStore(RenderAccess):
    kind = "supabase"

    def __init__(self, url: str, service_key: str, bucket: str = "renders"):
        url = url.strip().rstrip("/")
        if re.fullmatch(r"[a-zA-Z0-9-]+\.supabase\.(co|in)", url):
            url = f"https://{url}"
        parsed_url = httpx.URL(url)
        if parsed_url.scheme not in ("https", "http") or not parsed_url.host:
            raise ValueError("SUPABASE_URL must be your project's HTTP(S) API URL.")
        self.url = url
        self.key = service_key
        self.bucket = bucket
        # Modern secret keys are API keys, not JWTs. Only legacy service-role
        # JWTs belong in Authorization; both formats use the apikey header.
        self._auth_headers = {"apikey": self.key}
        if not self.key.startswith("sb_secret_"):
            self._auth_headers["Authorization"] = f"Bearer {self.key}"
        self._client = httpx.AsyncClient(
            base_url=self.url,
            headers={
                **self._auth_headers,
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        self._slug_cache: set[str] = set()

    async def initialize(self) -> None:
        """Close old public URLs in place; never delete objects or records.

        Do not serve the app if storage privacy cannot be established.
        """
        r = await self._client.put(
            f"/storage/v1/bucket/{self.bucket}", json={"public": False},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Could not make renders bucket private: {r.status_code}")
        r = await self._client.get(f"/storage/v1/bucket/{self.bucket}")
        if r.status_code >= 400 or r.json().get("public") is not False:
            raise RuntimeError("Could not verify renders bucket is private.")

    async def _rest(self, method: str, table: str, *, params: dict | None = None, json_body: Any = None,
                    prefer: str | None = None) -> Any:
        headers = {}
        if prefer:
            headers["Prefer"] = prefer
        r = await self._client.request(method, f"/rest/v1/{table}", params=params, json=json_body, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {method} {table} failed: {r.status_code} {r.text[:300]}")
        if r.status_code == 204 or not r.text:
            return None
        return r.json()

    async def create_venture(self, row: dict) -> dict:
        row = dict(row)
        row.setdefault("id", _new_id())
        row.setdefault("created_at", now_iso())
        row["updated_at"] = row["created_at"]
        data = await self._rest("POST", "ventures", json_body=row, prefer="return=representation")
        return data[0]

    async def update_venture(self, venture_id: str, fields: dict) -> dict | None:
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        data = await self._rest("PATCH", "ventures", params={"id": f"eq.{venture_id}"}, json_body=fields,
                                prefer="return=representation")
        return data[0] if data else None

    @staticmethod
    def _snapshot_params(current: dict) -> dict:
        return {
            "id": f"eq.{current['id']}", "status": f"eq.{current['status']}",
            "images_status": f"eq.{current['images_status']}",
            "updated_at": f"eq.{current['updated_at']}",
            "grounding": f"eq.{json.dumps(current['grounding'])}" if current.get("grounding") is not None else "is.null",
        }

    async def _compare_venture(self, current: dict, fields: dict) -> dict | None:
        data = await self._rest(
            "PATCH", "ventures", params=self._snapshot_params(current),
            json_body={**fields, "updated_at": now_iso()}, prefer="return=representation",
        )
        return data[0] if data else None

    async def update_refinement_images(self, venture_id: str, claim_id: str, fields: dict) -> dict | None:
        data = await self._rest(
            "PATCH", "ventures",
            params={"id": f"eq.{venture_id}", "images_status": "eq.pending",
                    "grounding->_refinement->>render_claim_id": f"eq.{claim_id}"},
            json_body={**fields, "updated_at": now_iso()}, prefer="return=representation",
        )
        return data[0] if data else None

    async def claim_refinement(
        self, venture_id: str, context: str | None = None, *, skipped: bool = False,
        require_images_finished: bool = True, claim_id: str | None = None,
    ) -> dict | None:
        current = await self.get_by_id(venture_id)
        if not current:
            return None
        grounding = dict(current.get("grounding") or {})
        grounding["_refinement"] = (
            {"state": "skipped"} if skipped else {
                "state": "pending", "context": context, "claim_id": claim_id or _new_id(),
                "lease_until": lease_until(),
            }
        )
        params = {
            "id": f"eq.{venture_id}",
            "status": "eq.ready",
            "visibility": "eq.private",
            "path": "is.null",
            "grounding->_refinement": "is.null",
        }
        if require_images_finished:
            params["images_status"] = "neq.pending"
        data = await self._rest(
            "PATCH", "ventures", params=params,
            json_body={
                "grounding": grounding, "status": "ready" if skipped else "refining",
                "updated_at": now_iso(),
            },
            prefer="return=representation",
        )
        return data[0] if data else None

    async def finish_refinement(
        self, venture_id: str, fields: dict, claim_id: str | None = None,
    ) -> dict | None:
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        params = {
            "id": f"eq.{venture_id}", "status": "eq.refining", "visibility": "eq.private",
            "path": "is.null",
        }
        if claim_id:
            params["grounding->_refinement->>claim_id"] = f"eq.{claim_id}"
        data = await self._rest(
            "PATCH", "ventures",
            params=params,
            json_body=fields, prefer="return=representation",
        )
        return data[0] if data else None

    async def clear_refinement_claim(
        self, venture_id: str, original_grounding: dict, claim_id: str | None = None,
    ) -> None:
        params = {"id": f"eq.{venture_id}", "status": "eq.refining"}
        if claim_id:
            params["grounding->_refinement->>claim_id"] = f"eq.{claim_id}"
        await self._rest(
            "PATCH", "ventures", params=params,
            json_body={"grounding": original_grounding or {}, "status": "ready", "updated_at": now_iso()},
            prefer="return=minimal",
        )

    async def claim_path(self, venture_id: str, claim_id: str | None = None) -> dict | None:
        claim_id = claim_id or _new_id()
        current = await self.get_by_id(venture_id)
        if not current:
            return None
        grounding = dict(current.get("grounding") or {})
        marker = grounding.get("_refinement")
        marker = dict(marker) if isinstance(marker, dict) else {"state": "skipped"}
        marker["path_claim_id"] = claim_id
        marker["path_lease_until"] = lease_until()
        grounding["_refinement"] = marker
        params = {"id": f"eq.{venture_id}", "status": "eq.ready"}
        params["grounding"] = self._snapshot_params(current)["grounding"]
        fields = {"status": "planning", "grounding": grounding, "updated_at": now_iso()}
        if current.get("grounding") is None or "_refinement" not in current.get("grounding", {}):
            params["grounding->_refinement"] = "is.null"
        data = await self._rest("PATCH", "ventures", params=params, json_body=fields, prefer="return=representation")
        if not data and params.get("grounding->_refinement") == "is.null":
            current = await self.get_by_id(venture_id)
            if current and current.get("status") == "ready":
                grounding = dict(current.get("grounding") or {})
                marker = grounding.get("_refinement")
                marker = dict(marker) if isinstance(marker, dict) else {"state": "skipped"}
                marker["path_claim_id"] = claim_id
                marker["path_lease_until"] = lease_until()
                grounding["_refinement"] = marker
                data = await self._rest(
                    "PATCH", "ventures", params={
                        "id": f"eq.{venture_id}", "status": "eq.ready",
                        "grounding": self._snapshot_params(current)["grounding"],
                    },
                    json_body={"status": "planning", "grounding": grounding, "updated_at": now_iso()},
                    prefer="return=representation",
                )
        return data[0] if data else None

    async def finish_path(
        self, venture_id: str, path: dict, claim_id: str | None = None,
    ) -> dict | None:
        if not claim_id:
            return None
        current = await self.get_by_id(venture_id)
        if not current:
            return None
        grounding = dict(current.get("grounding") or {})
        marker = grounding.get("_refinement")
        if not isinstance(marker, dict) or marker.get("path_claim_id") != claim_id:
            return None
        marker = dict(marker)
        marker.pop("path_claim_id", None)
        marker.pop("path_lease_until", None)
        grounding["_refinement"] = marker
        data = await self._rest(
            "PATCH", "ventures",
            params={
                "id": f"eq.{venture_id}", "status": "eq.planning",
                "grounding->_refinement->>path_claim_id": f"eq.{claim_id}",
                "grounding": self._snapshot_params(current)["grounding"],
            },
            json_body={
                "path": path, "status": "ready", "grounding": grounding, "updated_at": now_iso(),
            },
            prefer="return=representation",
        )
        return data[0] if data else None

    async def release_path_claim(self, venture_id: str, claim_id: str | None = None) -> None:
        if not claim_id:
            return
        current = await self.get_by_id(venture_id)
        if not current:
            return
        grounding = dict(current.get("grounding") or {})
        marker = grounding.get("_refinement")
        if not isinstance(marker, dict) or marker.get("path_claim_id") != claim_id:
            return
        marker = dict(marker)
        marker.pop("path_claim_id", None)
        marker.pop("path_lease_until", None)
        grounding["_refinement"] = marker
        await self._rest(
            "PATCH", "ventures",
            params={
                "id": f"eq.{venture_id}", "status": "eq.planning",
                "grounding->_refinement->>path_claim_id": f"eq.{claim_id}",
                "grounding": self._snapshot_params(current)["grounding"],
            },
            json_body={"status": "ready", "grounding": grounding, "updated_at": now_iso()},
            prefer="return=minimal",
        )

    async def reset_path(self, venture_id: str) -> dict | None:
        current = await self.get_by_id(venture_id)
        if not current:
            return None
        grounding = dict(current.get("grounding") or {})
        params = {"id": f"eq.{venture_id}"}
        fields = {"path": None, "updated_at": now_iso()}
        if current.get("path") is not None and "_refinement" not in grounding:
            grounding["_refinement"] = {"state": "skipped"}
            params["grounding->_refinement"] = "is.null"
            fields["grounding"] = grounding
        data = await self._rest(
            "PATCH", "ventures", params=params, json_body=fields, prefer="return=representation",
        )
        if not data and params.get("grounding->_refinement") == "is.null":
            # A concurrent refinement claim can only win when the path was null;
            # in that case its marker remains untouched by this path reset.
            data = await self._rest(
                "PATCH", "ventures", params={"id": f"eq.{venture_id}"},
                json_body={"path": None, "updated_at": now_iso()}, prefer="return=representation",
            )
        return data[0] if data else None

    async def release_venture(self, venture_id: str, released_at: str) -> dict | None:
        current = await self.get_by_id(venture_id)
        if not current:
            return None
        params = {
            "id": f"eq.{venture_id}", "visibility": f"eq.{current.get('visibility')}",
            "status": "neq.refining",
        }
        fields = {
            "visibility": "public", "released_at": (
                released_at if current.get("visibility") != "public" else current.get("released_at")
            ), "updated_at": now_iso(),
        }
        grounding = dict(current.get("grounding") or {})
        if "_refinement" not in grounding:
            grounding["_refinement"] = {"state": "skipped"}
            params["grounding->_refinement"] = "is.null"
            fields["grounding"] = grounding
        data = await self._rest(
            "PATCH", "ventures", params=params, json_body=fields,
            prefer="return=representation",
        )
        if not data and params.get("grounding->_refinement") == "is.null":
            current = await self.get_by_id(venture_id)
            if current and current.get("visibility") == "private":
                grounding = dict(current.get("grounding") or {})
                params = {
                    "id": f"eq.{venture_id}", "visibility": "eq.private", "status": "neq.refining",
                }
                fields = {"visibility": "public", "released_at": released_at, "updated_at": now_iso()}
                if not grounding.get("_refinement"):
                    grounding["_refinement"] = {"state": "skipped"}
                    params["grounding->_refinement"] = "is.null"
                    fields["grounding"] = grounding
                data = await self._rest("PATCH", "ventures", params=params, json_body=fields,
                                        prefer="return=representation")
        return data[0] if data else None

    async def get_by_id(self, venture_id: str) -> dict | None:
        data = await self._rest("GET", "ventures", params={"id": f"eq.{venture_id}", "limit": 1})
        return data[0] if data else None

    async def get_by_slug(self, slug: str) -> dict | None:
        data = await self._rest("GET", "ventures", params={"slug": f"eq.{slug}", "limit": 1})
        return data[0] if data else None

    def slug_exists(self, slug: str) -> bool:
        # Sync check used during slug minting; the DB has a UNIQUE constraint as the
        # real guard, so a rare race simply retries on insert.
        with httpx.Client(timeout=10) as c:
            r = c.get(
                f"{self.url}/rest/v1/ventures",
                params={"slug": f"eq.{slug}", "select": "id", "limit": 1},
                headers=self._auth_headers,
            )
            return bool(r.status_code == 200 and r.json())

    async def list_public(self, limit: int = 60) -> list[dict]:
        return await self._rest(
            "GET", "ventures",
            params={
                "visibility": "eq.public", "status": "eq.ready",
                "order": "promoted_at.desc.nullslast,signal_count.desc,released_at.desc",
                "limit": limit,
            },
        ) or []

    async def recent_concepts(self, owner_token: str, limit: int = 12) -> list[dict]:
        rows = await self._rest("GET", "ventures", params={
            "owner_token": f"eq.{owner_token}", "select": "name,tagline,idea_raw",
            "order": "created_at.desc", "limit": limit}) or []
        return [{"name": r.get("name"), "tagline": r.get("tagline"), "idea": r.get("idea_raw")} for r in rows if r.get("name")]

    async def count_generations_since(self, since_iso: str) -> int:
        r = await self._client.get(
            "/rest/v1/ventures",
            params={"created_at": f"gte.{since_iso}", "select": "id"},
            headers={"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"},
        )
        cr = r.headers.get("content-range", "*/0")
        try:
            return int(cr.split("/")[-1])
        except ValueError:
            return 0

    async def add_signal(self, venture_id: str, type_: str, email: str, price: float | None) -> bool:
        try:
            await self._rest(
                "POST", "signals",
                json_body={"venture_id": venture_id, "type": type_, "email": email.lower(), "price": price},
                prefer="return=minimal",
            )
            return True
        except RuntimeError as e:
            if "23505" in str(e) or "duplicate" in str(e).lower():
                return False
            raise

    async def signal_counts(self, venture_id: str) -> dict:
        rows = await self._rest("GET", "signals", params={"venture_id": f"eq.{venture_id}", "select": "type,price"}) or []
        counts = {"buy": 0, "build": 0, "fund": 0, "avg_price": None}
        prices = []
        for r in rows:
            counts[r["type"]] = counts.get(r["type"], 0) + 1
            if r["type"] == "buy" and r.get("price") is not None:
                prices.append(float(r["price"]))
        if prices:
            counts["avg_price"] = round(sum(prices) / len(prices), 2)
        counts["total"] = counts["buy"] + counts["build"] + counts["fund"]
        return counts

    async def add_waitlist(self, name: str, email: str, role: str) -> bool:
        try:
            await self._rest("POST", "waitlist", json_body={"name": name, "email": email.lower(), "role": role},
                             prefer="return=minimal")
            return True
        except RuntimeError as e:
            if "23505" in str(e) or "duplicate" in str(e).lower():
                return False
            raise

    # -- needs + offers ------------------------------------------------------
    async def create_need(self, venture_id: str, step_n: int | None, title: str, detail: str) -> dict:
        data = await self._rest("POST", "needs", json_body={"venture_id": venture_id, "step_n": step_n, "title": title,
                                                            "detail": detail}, prefer="return=representation")
        return data[0]

    async def get_need(self, need_id: str) -> dict | None:
        data = await self._rest("GET", "needs", params={"id": f"eq.{need_id}", "limit": 1})
        return data[0] if data else None

    async def list_needs(self, venture_id: str) -> list[dict]:
        return await self._rest("GET", "needs", params={"venture_id": f"eq.{venture_id}", "order": "created_at.asc"}) or []

    async def set_need_status(self, need_id: str, status: str) -> None:
        await self._rest("PATCH", "needs", params={"id": f"eq.{need_id}"}, json_body={"status": status}, prefer="return=minimal")

    async def list_open_needs(self, limit: int = 40) -> list[dict]:
        rows = await self._rest("GET", "needs", params={
            "status": "eq.open", "order": "created_at.desc", "limit": limit,
            "select": "*,ventures!inner(slug,name,accent,visibility)", "ventures.visibility": "eq.public",
        }) or []
        out = []
        for r in rows:
            v = r.pop("ventures", None) or {}
            r.update(venture_slug=v.get("slug"), venture_name=v.get("name"), venture_accent=v.get("accent"))
            out.append(r)
        return out

    async def add_offer(self, need_id: str, email: str, name: str, message: str) -> bool:
        try:
            await self._rest("POST", "offers", json_body={"need_id": need_id, "email": email.lower(), "name": name,
                                                          "message": message}, prefer="return=minimal")
        except RuntimeError as e:
            if "23505" in str(e) or "duplicate" in str(e).lower():
                return False
            raise
        offers = await self.list_offers(need_id)
        await self._rest("PATCH", "needs", params={"id": f"eq.{need_id}"}, json_body={"offer_count": len(offers)},
                         prefer="return=minimal")
        return True

    async def list_offers(self, need_id: str) -> list[dict]:
        return await self._rest("GET", "offers", params={"need_id": f"eq.{need_id}", "order": "created_at.asc"}) or []

    def _legacy_render_url(self, venture_id: str, idx: int, ext: str) -> str:
        return f"{self.url}/storage/v1/object/public/{self.bucket}/{venture_id}/{idx}.{ext}"

    @staticmethod
    def _render_object_path(venture: dict, idx: int, ext: str) -> str:
        marker = (venture.get("grounding") or {}).get("_refinement") or {}
        token = marker.get("render_claim_id")
        if token and re.fullmatch(r"[a-zA-Z0-9-]+", token):
            return f"{venture['id']}/{token}/{idx}.{ext}"
        return f"{venture['id']}/{idx}.{ext}"

    async def read_render(self, venture: dict, idx: int) -> tuple[bytes, str] | None:
        url = self.render_url(venture, idx)
        if not url:
            return None
        ext = url.rsplit(".", 1)[1]
        path = self._render_object_path(venture, idx, ext)
        r = await self._client.get(
            f"/storage/v1/object/authenticated/{self.bucket}/{path}",
        )
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"Storage download failed: {r.status_code}")
        return r.content, f"image/{ext}"

    async def save_render(self, venture_id: str, idx: int, png: bytes, *, render_claim_id: str | None = None) -> str:
        data, ext, mime = await asyncio.to_thread(compress_render, png)
        path = self._render_object_path(
            {"id": venture_id, "grounding": {"_refinement": {"render_claim_id": render_claim_id}}}, idx, ext,
        )
        r = await self._client.post(
            f"/storage/v1/object/{self.bucket}/{path}",
            content=data,
            headers={"Content-Type": mime, "x-upsert": "true", "cache-control": "0"},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Storage upload failed: {r.status_code} {r.text[:200]}")
        return f"/api/venture-images/{venture_id}/{idx}.{ext}"


# ---------------------------------------------------------------------------

def build_store() -> SQLiteStore | SupabaseStore:
    mode = os.environ.get("STORE", "auto").strip().lower()
    if mode not in ("auto", "sqlite", "supabase"):
        raise ValueError("STORE must be auto, sqlite, or supabase.")
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SECRET_KEY", "").strip()
           or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip())
    if mode == "supabase" and not (url and key):
        raise ValueError("Supabase requires SUPABASE_URL and a backend secret key.")
    if mode == "supabase" or (mode == "auto" and url and key):
        return SupabaseStore(url, key, os.environ.get("SUPABASE_RENDERS_BUCKET", "renders"))
    return SQLiteStore(os.environ.get("MANIFEST_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def day_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def hours_ago_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
