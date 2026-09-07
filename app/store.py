"""Persistence for MANIFEST.

Two interchangeable backends:

* SQLiteStore   – zero-config default. One file in ./data. Good for local
                  runs, previews, and the first few thousand ventures.
* SupabaseStore – used automatically when SUPABASE_URL and
                  SUPABASE_SERVICE_ROLE_KEY are set. Talks to PostgREST and
                  Supabase Storage directly over HTTPS (no extra SDK).

Both expose the same async surface so server.py never cares which is live.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .provenance import now_iso

JSON_FIELDS = ("tiers", "roadmap", "grounding", "image_prompts", "images")

VENTURE_COLUMNS = [
    "id", "slug", "owner_token", "idea_raw", "idea_hash", "provenance_code",
    "name", "tagline", "description", "category", "accent",
    "tiers", "roadmap", "grounding", "image_prompts", "images", "images_status",
    "visibility", "status", "signal_count", "buy_count", "build_count", "fund_count",
    "promoted_at", "released_at", "created_at", "updated_at",
]


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

class SQLiteStore:
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
                """
            )
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

    # -- renders ------------------------------------------------------------
    async def save_render(self, venture_id: str, idx: int, png: bytes) -> str:
        path = self.renders_dir / f"{venture_id}-{idx}.png"
        await asyncio.to_thread(path.write_bytes, png)
        return f"/renders/{path.name}"


# ---------------------------------------------------------------------------
# Supabase (PostgREST + Storage)
# ---------------------------------------------------------------------------

class SupabaseStore:
    kind = "supabase"

    def __init__(self, url: str, service_key: str, bucket: str = "renders"):
        self.url = url.rstrip("/")
        self.key = service_key
        self.bucket = bucket
        self._client = httpx.AsyncClient(
            base_url=self.url,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        self._slug_cache: set[str] = set()

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
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}"},
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

    async def save_render(self, venture_id: str, idx: int, png: bytes) -> str:
        path = f"{venture_id}/{idx}.png"
        r = await self._client.post(
            f"/storage/v1/object/{self.bucket}/{path}",
            content=png,
            headers={"Content-Type": "image/png", "x-upsert": "true"},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Storage upload failed: {r.status_code} {r.text[:200]}")
        return f"{self.url}/storage/v1/object/public/{self.bucket}/{path}"


# ---------------------------------------------------------------------------

def build_store() -> SQLiteStore | SupabaseStore:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if url and key:
        return SupabaseStore(url, key, os.environ.get("SUPABASE_RENDERS_BUCKET", "renders"))
    return SQLiteStore(os.environ.get("MANIFEST_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def day_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def hours_ago_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
