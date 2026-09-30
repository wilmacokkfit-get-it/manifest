"""MANIFEST — one sentence in, a venture out.

Run:  uvicorn server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

from app import llm  # noqa: E402
from app.partial_json import parse_partial  # noqa: E402
from app.provenance import mint, new_token, now_iso, unique_slug  # noqa: E402
from app.store import build_store, day_start_iso, hours_ago_iso  # noqa: E402

store = build_store()
app = FastAPI(title="MANIFEST", docs_url=None, redoc_url=None)

PUBLIC = ROOT / "public"
_index_cache: dict = {"mtime": 0.0, "html": ""}


def _index_html() -> str:
    """Read index.html, re-reading only when the file changes on disk."""
    path = PUBLIC / "index.html"
    mtime = path.stat().st_mtime
    if mtime != _index_cache["mtime"]:
        _index_cache.update(mtime=mtime, html=path.read_text())
    return _index_cache["html"]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GEN_PER_HOUR = int(os.environ.get("GEN_PER_HOUR", "3"))
GEN_PER_DAY = int(os.environ.get("GEN_PER_DAY", "8"))
GLOBAL_PER_DAY = int(os.environ.get("GLOBAL_PER_DAY", "150"))
MARK_SIGNALS = int(os.environ.get("MARK_THRESHOLD_SIGNALS", "25"))
MARK_BUILD_FUND = int(os.environ.get("MARK_THRESHOLD_BUILD_FUND", "5"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
IMAGE_CONCURRENCY = asyncio.Semaphore(int(os.environ.get("IMAGE_CONCURRENCY", "3")))

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Rate limiting (per visitor, in memory) + global daily cap (in the store)
# ---------------------------------------------------------------------------

_hits: dict[str, deque] = defaultdict(deque)


def _caller_id(request: Request) -> str:
    vid = request.headers.get("x-visitor-id")
    if vid:
        return f"v:{vid}"
    fwd = request.headers.get("x-forwarded-for")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")
    return f"ip:{ip}"


def _check_rate(caller: str) -> None:
    now = time.time()
    q = _hits[caller]
    while q and now - q[0] > 86400:
        q.popleft()
    last_hour = sum(1 for t in q if now - t < 3600)
    if last_hour >= GEN_PER_HOUR:
        raise HTTPException(429, "You've manifested a lot in the last hour. Give the engine a moment.")
    if len(q) >= GEN_PER_DAY:
        raise HTTPException(429, "That's your ventures for today. Come back tomorrow.")


def _record_hit(caller: str) -> None:
    _hits[caller].append(time.time())


COOKIE = "manifest_owner"


def _owner_token(request: Request) -> str:
    """Who is this? Prefer the hosting proxy's stable visitor id, then our own
    first-party cookie (set by the middleware below), never anything the page
    can forge from JavaScript."""
    return request.headers.get("x-visitor-id") or request.state.owner


@app.middleware("http")
async def _identity_cookie(request: Request, call_next):
    """Give every visitor a private, HttpOnly creator key on first contact."""
    token = request.cookies.get(COOKIE)
    fresh = False
    if not token or len(token) < 16:
        token, fresh = new_token(), True
    request.state.owner = token
    response = await call_next(request)
    if fresh and not request.headers.get("x-visitor-id"):
        response.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 365 * 2, httponly=True,
                            samesite="lax", secure=request.url.scheme == "https")
    return response


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------

def _public_view(v: dict, *, is_owner: bool, counts: dict | None = None, needs: list | None = None) -> dict:
    counts = counts or {
        "buy": v.get("buy_count", 0), "build": v.get("build_count", 0), "fund": v.get("fund_count", 0),
        "total": v.get("signal_count", 0), "avg_price": None,
    }
    return {
        "id": v["id"],
        "slug": v["slug"],
        "name": v.get("name"),
        "tagline": v.get("tagline"),
        "description": v.get("description"),
        "category": v.get("category"),
        "accent": v.get("accent") or "#e8eeff",
        "tiers": v.get("tiers") or [],
        "roadmap": v.get("roadmap") or [],
        "grounding": v.get("grounding") or {},
        "images": v.get("images") or [],
        "images_status": v.get("images_status", "pending"),
        "visibility": v.get("visibility", "private"),
        "status": v.get("status"),
        "provenance": {"code": v["provenance_code"], "hash": v["idea_hash"], "minted_at": v["created_at"]},
        "signals": counts,
        "mark": {
            "marked": bool(v.get("promoted_at")),
            "marked_at": v.get("promoted_at"),
            "threshold_signals": MARK_SIGNALS,
            "threshold_build_fund": MARK_BUILD_FUND,
        },
        "released_at": v.get("released_at"),
        "created_at": v["created_at"],
        "is_owner": is_owner,
        "idea": v.get("idea_raw") if is_owner else None,
        "path": _path_view(v.get("path"), is_owner),
        "needs": needs or [],
    }


def _path_view(path: dict | None, is_owner: bool) -> dict | None:
    if not path:
        return None
    steps = path.get("steps") or []
    done = sum(1 for st in steps if st.get("status") == "done")
    out = {
        "tier": path.get("tier"), "tier_label": path.get("tier_label"), "chosen_at": path.get("chosen_at"),
        "status": path.get("status", "ready"), "first_move": path.get("first_move"), "watch_out": path.get("watch_out"),
        "steps": steps, "done": done, "total": len(steps),
    }
    if not is_owner:
        out["steps"] = [{k: v for k, v in st.items() if k != "note"} for st in steps]
    return out


def _card_view(v: dict) -> dict:
    return {
        "slug": v["slug"], "name": v.get("name"), "tagline": v.get("tagline"), "category": v.get("category"),
        "accent": v.get("accent") or "#e8eeff", "image": (v.get("images") or [None])[0],
        "signals": {"buy": v.get("buy_count", 0), "build": v.get("build_count", 0), "fund": v.get("fund_count", 0),
                    "total": v.get("signal_count", 0)},
        "marked": bool(v.get("promoted_at")), "released_at": v.get("released_at"),
        "provenance_code": v["provenance_code"],
        "path": _path_progress(v.get("path")),
    }


def _path_progress(path: dict | None) -> dict | None:
    if not path or not path.get("steps"):
        return None
    steps = path["steps"]
    return {"tier_label": path.get("tier_label"), "done": sum(1 for st in steps if st.get("status") == "done"), "total": len(steps)}


def _normalize(data: dict) -> dict:
    """Make sure the model output has the shape the UI expects."""
    labels = ["The Lean Build", "The Real Product", "The Empire"]
    tiers = data.get("tiers") or []
    fixed_tiers = []
    for i in range(3):
        t = tiers[i] if i < len(tiers) and isinstance(tiers[i], dict) else {}
        fixed_tiers.append({"label": labels[i], "title": str(t.get("title") or ""), "description": str(t.get("description") or "")})
    roadmap = []
    for i, s in enumerate((data.get("roadmap") or [])[:5]):
        if isinstance(s, dict):
            roadmap.append({"step": i + 1, "action": str(s.get("action") or ""), "where": str(s.get("where") or "")})
    g = data.get("grounding") or {}
    accent = str(data.get("accent") or "#e8eeff")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = "#e8eeff"
    prompts = [str(p) for p in (data.get("image_prompts") or []) if isinstance(p, str)][:3]
    return {
        "name": str(data.get("name") or "Untitled").strip()[:60],
        "tagline": str(data.get("tagline") or "").strip()[:160],
        "description": str(data.get("description") or "").strip()[:900],
        "category": str(data.get("category") or "").strip()[:40],
        "accent": accent,
        "tiers": fixed_tiers,
        "roadmap": roadmap,
        "grounding": {
            "exists": str(g.get("exists") or ""), "who_pays": str(g.get("who_pays") or ""),
            "first_hundred": str(g.get("first_hundred") or ""),
        },
        "image_prompts": prompts,
    }


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class ManifestBody(BaseModel):
    idea: str = Field(min_length=8, max_length=300)


@app.post("/api/manifest")
async def manifest(body: ManifestBody, request: Request):
    idea = " ".join(body.idea.split())
    caller = _caller_id(request)
    _check_rate(caller)
    if await store.count_generations_since(day_start_iso()) >= GLOBAL_PER_DAY:
        raise HTTPException(429, "The engine has reached today's capacity. It resets at midnight UTC.")
    owner = _owner_token(request)
    created_at = now_iso()
    prov = mint(idea, created_at, owner)

    async def gen():
        yield _sse("meta", {"provenance": prov, "owner_token": owner, "created_at": created_at})
        buf = ""
        last_emit = 0.0
        last_key = None
        try:
            async for chunk in llm.stream_venture_text(idea):
                buf += chunk
                now = time.time()
                obj, key = parse_partial(buf)
                if obj and (now - last_emit > 0.07 or key != last_key):
                    last_emit, last_key = now, key
                    yield _sse("partial", {"data": obj, "streaming": key})
            obj, _ = parse_partial(buf)
            if not obj or not obj.get("name"):
                raise RuntimeError("The engine returned nothing usable.")
            data = _normalize(obj)
            slug = unique_slug(data["name"], store.slug_exists)
            row = {
                "slug": slug, "owner_token": owner, "idea_raw": idea, "idea_hash": prov["hash"],
                "provenance_code": prov["code"], "created_at": created_at,
                **{k: data[k] for k in ("name", "tagline", "description", "category", "accent", "tiers", "roadmap", "grounding", "image_prompts")},
                "images": [], "images_status": "pending" if (llm.image_provider() != "none" and data["image_prompts"]) else "failed",
                "visibility": "private", "status": "ready",
            }
            saved = await store.create_venture(row)
            _record_hit(caller)  # only successful births count against the limit
            if saved["images_status"] == "pending":
                asyncio.create_task(_render_images(saved))
            yield _sse("done", _public_view(saved, is_owner=True))
        except HTTPException as e:
            yield _sse("error", {"message": e.detail})
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": f"The engine stalled: {str(e)[:200]}"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _render_images(v: dict) -> None:
    prompts = v.get("image_prompts") or []
    urls: list[str | None] = [None] * len(prompts)

    async def one(i: int, prompt: str):
        async with IMAGE_CONCURRENCY:
            try:
                png = await llm.generate_render(prompt, v.get("accent") or "#e8eeff")
                if png:
                    urls[i] = await store.save_render(v["id"], i, png)
                    # progressive: write each image as it lands
                    await store.update_venture(v["id"], {"images": urls})
            except Exception as e:  # noqa: BLE001
                print(f"[render] {v['slug']} #{i} failed: {e}")

    await asyncio.gather(*(one(i, p) for i, p in enumerate(prompts)))
    status = "ready" if any(urls) else "failed"
    await store.update_venture(v["id"], {"images": urls, "images_status": status})


# ---------------------------------------------------------------------------
# Ventures
# ---------------------------------------------------------------------------

async def _load(slug: str, request: Request) -> tuple[dict, bool]:
    v = await store.get_by_slug(slug)
    if not v:
        raise HTTPException(404, "No venture lives at this address.")
    token = _owner_token(request)
    is_owner = bool(token) and token == v["owner_token"]
    if v.get("visibility") != "public" and not is_owner:
        raise HTTPException(404, "This venture hasn't been released by its creator.")
    return v, is_owner


async def _needs_view(v: dict, is_owner: bool) -> list[dict]:
    needs = await store.list_needs(v["id"])
    out = []
    for n in needs:
        item = {"id": n["id"], "step_n": n.get("step_n"), "title": n["title"], "detail": n.get("detail") or "",
                "status": n.get("status", "open"), "offer_count": int(n.get("offer_count") or 0), "created_at": n["created_at"]}
        if is_owner:
            item["offers"] = [{"email": o["email"], "name": o.get("name") or "", "message": o.get("message") or "",
                               "created_at": o["created_at"]} for o in await store.list_offers(n["id"])]
        out.append(item)
    return out


async def _full_view(v: dict, is_owner: bool) -> dict:
    counts = await store.signal_counts(v["id"])
    return _public_view(v, is_owner=is_owner, counts=counts, needs=await _needs_view(v, is_owner))


@app.get("/api/ventures/{slug}")
async def get_venture(slug: str, request: Request):
    v, is_owner = await _load(slug, request)
    return await _full_view(v, is_owner)


# ---------------------------------------------------------------------------
# The Path: pick a tier, get walked through it, ask the Floor for help
# ---------------------------------------------------------------------------

class PathBody(BaseModel):
    tier: int


def _normalize_path(data: dict, tier: int, tier_label: str) -> dict:
    steps = []
    for i, st in enumerate((data.get("steps") or [])[:8]):
        if not isinstance(st, dict):
            continue
        sups = []
        for sp in (st.get("suppliers") or [])[:3]:
            if isinstance(sp, dict):
                sups.append({"kind": str(sp.get("kind") or "")[:80],
                             "examples": [str(x)[:60] for x in (sp.get("examples") or []) if isinstance(x, str)][:3],
                             "search": str(sp.get("search") or "")[:100]})
        steps.append({
            "n": i + 1, "title": str(st.get("title") or f"Step {i + 1}")[:80], "why": str(st.get("why") or "")[:300],
            "how": [str(x)[:320] for x in (st.get("how") or []) if isinstance(x, str)][:5],
            "suppliers": sups, "cost": str(st.get("cost") or "")[:70], "time": str(st.get("time") or "")[:70],
            "done_when": str(st.get("done_when") or "")[:320], "status": "todo", "note": "", "done_at": None,
        })
    return {"tier": tier, "tier_label": tier_label, "chosen_at": now_iso(), "status": "ready",
            "first_move": str(data.get("first_move") or "")[:700], "watch_out": str(data.get("watch_out") or "")[:700],
            "steps": steps}


@app.post("/api/ventures/{slug}/path")
async def choose_path(slug: str, body: PathBody, request: Request):
    """Owner picks one of the three tiers. The engine streams a step-by-step guide for it."""
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can choose the path.")
    tier = int(body.tier)
    if tier not in (0, 1, 2):
        raise HTTPException(400, "Pick path 0, 1, or 2.")
    existing = v.get("path") or {}
    if existing.get("steps") and any(st.get("status") != "todo" for st in existing["steps"]):
        raise HTTPException(409, "You've already started walking a path. Finish it or reset it first.")
    tier_label = (v.get("tiers") or [{}] * 3)[tier].get("label") or f"Path {tier + 1}"

    async def gen():
        buf = ""
        last_emit, last_key = 0.0, None
        try:
            async for chunk in llm.stream_path_text(v, tier):
                buf += chunk
                now = time.time()
                obj, key = parse_partial(buf)
                if obj and (now - last_emit > 0.08 or key != last_key):
                    last_emit, last_key = now, key
                    yield _sse("partial", {"data": obj, "streaming": key})
            obj, _ = parse_partial(buf)
            if not obj or not obj.get("steps"):
                raise RuntimeError("The engine returned nothing usable.")
            path = _normalize_path(obj, tier, tier_label)
            saved = await store.update_venture(v["id"], {"path": path})
            yield _sse("done", await _full_view(saved, True))
        except Exception as e:  # noqa: BLE001
            print(f"[path] {slug} failed: {e}")
            yield _sse("error", {"message": str(e)[:300] or "The engine stalled."})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class StepBody(BaseModel):
    status: str | None = None
    note: str | None = None


@app.patch("/api/ventures/{slug}/path/steps/{n}")
async def update_step(slug: str, n: int, body: StepBody, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can update the path.")
    path = v.get("path") or {}
    steps = path.get("steps") or []
    step = next((st for st in steps if st.get("n") == n), None)
    if not step:
        raise HTTPException(404, "No such step.")
    if body.status is not None:
        if body.status not in ("todo", "doing", "done"):
            raise HTTPException(400, "Status must be todo, doing, or done.")
        step["status"] = body.status
        step["done_at"] = now_iso() if body.status == "done" else None
    if body.note is not None:
        step["note"] = body.note.strip()[:1500]
    saved = await store.update_venture(v["id"], {"path": path})
    return await _full_view(saved, True)


@app.delete("/api/ventures/{slug}/path")
async def reset_path(slug: str, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can reset the path.")
    saved = await store.update_venture(v["id"], {"path": None})
    return await _full_view(saved, True)


class NeedBody(BaseModel):
    step_n: int | None = None
    title: str
    detail: str = ""


@app.post("/api/ventures/{slug}/needs")
async def create_need(slug: str, body: NeedBody, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can ask the Floor for help.")
    title = body.title.strip()[:120]
    if len(title) < 4:
        raise HTTPException(400, "Say what you need in a few words.")
    if len(await store.list_needs(v["id"])) >= 12:
        raise HTTPException(429, "Twelve open asks is plenty. Close some first.")
    await store.create_need(v["id"], body.step_n, title, body.detail.strip()[:600])
    return await _full_view(v, True)


class NeedStatusBody(BaseModel):
    status: str


@app.patch("/api/ventures/{slug}/needs/{need_id}")
async def update_need(slug: str, need_id: str, body: NeedStatusBody, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can change an ask.")
    if body.status not in ("open", "filled", "closed"):
        raise HTTPException(400, "Status must be open, filled, or closed.")
    n = await store.get_need(need_id)
    if not n or n["venture_id"] != v["id"]:
        raise HTTPException(404, "No such ask.")
    await store.set_need_status(need_id, body.status)
    return await _full_view(v, True)


class OfferBody(BaseModel):
    email: str
    name: str = ""
    message: str = ""


@app.post("/api/ventures/{slug}/needs/{need_id}/offers")
async def add_offer(slug: str, need_id: str, body: OfferBody, request: Request):
    """Anyone can raise a hand on a public venture's ask. Real email, one per person per ask."""
    v, is_owner = await _load(slug, request)
    if v.get("visibility") != "public":
        raise HTTPException(403, "This venture isn't public yet.")
    n = await store.get_need(need_id)
    if not n or n["venture_id"] != v["id"] or n.get("status") != "open":
        raise HTTPException(404, "That ask isn't open.")
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "That email doesn't look right.")
    added = await store.add_offer(need_id, email, body.name.strip()[:80], body.message.strip()[:600])
    out = await _full_view(v, is_owner)
    out["added"] = added
    return out


@app.post("/api/ventures/{slug}/release")
async def release(slug: str, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can release a venture.")
    if v.get("visibility") != "public":
        v = await store.update_venture(v["id"], {"visibility": "public", "released_at": now_iso()})
    return await _full_view(v, True)


class SignalBody(BaseModel):
    type: str
    email: str
    price: float | None = None


@app.post("/api/ventures/{slug}/signals")
async def signal(slug: str, body: SignalBody, request: Request):
    v, is_owner = await _load(slug, request)
    if v.get("visibility") != "public":
        raise HTTPException(400, "Release the venture before collecting signals.")
    kind = body.type.strip().lower()
    if kind not in ("buy", "build", "fund"):
        raise HTTPException(400, "Signal must be buy, build, or fund.")
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "That email doesn't look right.")
    price = None
    if kind == "buy" and body.price is not None:
        price = max(0.0, min(float(body.price), 1_000_000.0))
    added = await store.add_signal(v["id"], kind, email, price)
    counts = await store.signal_counts(v["id"])
    fields = {
        "signal_count": counts["total"], "buy_count": counts["buy"],
        "build_count": counts["build"], "fund_count": counts["fund"],
    }
    if not v.get("promoted_at") and (counts["total"] >= MARK_SIGNALS or counts["build"] + counts["fund"] >= MARK_BUILD_FUND):
        fields["promoted_at"] = now_iso()
    v = await store.update_venture(v["id"], fields)
    out = _public_view(v, is_owner=is_owner, counts=counts, needs=await _needs_view(v, is_owner))
    out["added"] = added
    return out


@app.get("/api/marketplace")
async def marketplace():
    rows = await store.list_public(80)
    cards = [_card_view(v) for v in rows]
    needs = await store.list_open_needs(40)
    requests_ = [{"id": n["id"], "title": n["title"], "detail": n.get("detail") or "", "step_n": n.get("step_n"),
                  "offer_count": int(n.get("offer_count") or 0), "created_at": n["created_at"],
                  "venture": {"slug": n["venture_slug"], "name": n["venture_name"], "accent": n.get("venture_accent") or "#e8eeff"}}
                 for n in needs]
    return {"marked": [c for c in cards if c["marked"]], "rising": [c for c in cards if not c["marked"]], "requests": requests_}


class WaitlistBody(BaseModel):
    name: str = ""
    email: str
    role: str = "Curious"


@app.post("/api/waitlist")
async def waitlist(body: WaitlistBody):
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "That email doesn't look right.")
    role = body.role if body.role in ("Builder", "Investor", "Curious") else "Curious"
    await store.add_waitlist(body.name.strip()[:80], email, role)
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {"ok": True, "store": store.kind, "llm": llm.text_provider(), "images": llm.image_provider()}


# ---------------------------------------------------------------------------
# HTML + static
# ---------------------------------------------------------------------------

def _shell(og: dict | None = None) -> str:
    og = og or {}
    title = og.get("title") or "MANIFEST — one sentence in, a venture out"
    desc = og.get("description") or "Type one sentence. Watch it become a named, rendered, provenance-sealed venture."
    image = og.get("image") or f"{PUBLIC_URL}/og-default.png"
    url = og.get("url") or PUBLIC_URL
    tags = "\n".join([
        f'<title>{html.escape(title)}</title>',
        f'<meta name="description" content="{html.escape(desc)}">',
        f'<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{html.escape(title)}">',
        f'<meta property="og:description" content="{html.escape(desc)}">',
        f'<meta property="og:image" content="{html.escape(image)}">',
        f'<meta property="og:url" content="{html.escape(url)}">',
        f'<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{html.escape(title)}">',
        f'<meta name="twitter:description" content="{html.escape(desc)}">',
        f'<meta name="twitter:image" content="{html.escape(image)}">',
    ])
    page = _index_html().replace("<!--OG-->", tags).replace("<!--BASE-->", '<base href="/">')
    return page.replace("<!--SERVED-->", '<script>window.__MANIFEST_SERVED__=true;</script>')


@app.get("/", response_class=HTMLResponse)
@app.get("/marketplace", response_class=HTMLResponse)
@app.get("/waitlist", response_class=HTMLResponse)
@app.get("/about", response_class=HTMLResponse)
async def index():
    return _shell()


@app.get("/v/{slug}", response_class=HTMLResponse)
async def venture_page(slug: str, request: Request):
    v = await store.get_by_slug(slug)
    og = None
    if v and v.get("visibility") == "public":
        first = next((u for u in (v.get("images") or []) if u), None)
        if first and first.startswith("/"):
            first = f"{PUBLIC_URL}{first}"
        og = {
            "title": f"{v['name']} — {v.get('tagline', '')}".strip(" —"),
            "description": v.get("description") or "",
            "image": first,
            "url": f"{PUBLIC_URL}/v/{slug}",
        }
    return _shell(og)


if (ROOT / "data" / "renders").exists() or store.kind == "sqlite":
    (ROOT / "data" / "renders").mkdir(parents=True, exist_ok=True)
    app.mount("/renders", StaticFiles(directory=ROOT / "data" / "renders"), name="renders")
app.mount("/", StaticFiles(directory=PUBLIC, html=False), name="public")


@app.exception_handler(HTTPException)
async def http_exc(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
