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

JSON_FIELDS = ("tiers", "roadmap", "grounding", "image_prompts", "images", "path")

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
    async def save_render(self, venture_id: str, idx: int, png: bytes) -> str:
        data, ext, _ = await asyncio.to_thread(compress_render, png)
        path = self.renders_dir / f"{venture_id}-{idx}.{ext}"
        await asyncio.to_thread(path.write_bytes, data)
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

    async def save_render(self, venture_id: str, idx: int, png: bytes) -> str:
        data, ext, mime = await asyncio.to_thread(compress_render, png)
        path = f"{venture_id}/{idx}.{ext}"
        r = await self._client.post(
            f"/storage/v1/object/{self.bucket}/{path}",
            content=data,
            headers={"Content-Type": mime, "x-upsert": "true"},
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
