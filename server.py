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
import random
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
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
        v = re.split(r"\s+#", v, maxsplit=1)[0]          # allow trailing comments like KEY=value   # note
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _wire_credentials() -> None:
    """Published on pplx.app, API keys never touch the box. The platform injects a proxy URL and a
    proxy token per saved credential; map them onto the names the SDKs expect and pick real model ids
    (the sandbox-only aliases like claude_sonnet_4_6 / nano_banana_2 do not exist outside the sandbox)."""
    a_url, a_key = os.environ.get(ANTHROPIC_URL_VAR, ""), os.environ.get(ANTHROPIC_KEY_VAR, "")
    o_url, o_key = os.environ.get(OPENAI_URL_VAR, ""), os.environ.get(OPENAI_KEY_VAR, "")
    if not (a_key or o_key):
        return                                    # dev sandbox or Replit: .env rules
    if o_key:
        os.environ["OPENAI_API_KEY"] = o_key
        if o_url:
            os.environ["OPENAI_BASE_URL"] = o_url.rstrip("/") + ("" if o_url.rstrip("/").endswith("/v1") else "/v1")
    if a_key:
        os.environ["ANTHROPIC_API_KEY"] = a_key
        if a_url:
            os.environ["ANTHROPIC_BASE_URL"] = a_url.rstrip("/")
        os.environ["LLM_PROVIDER"] = "anthropic"
        if os.environ.get("LLM_MODEL", "").startswith("claude_") or not os.environ.get("LLM_MODEL"):
            os.environ["LLM_MODEL"] = os.environ.get("PROD_LLM_MODEL", "claude-sonnet-4-5")
    else:                                         # OpenAI only: it writes the text too
        os.environ["LLM_PROVIDER"] = "openai"
        if "gpt" not in os.environ.get("LLM_MODEL", "") or "_" in os.environ.get("LLM_MODEL", ""):
            os.environ["LLM_MODEL"] = os.environ.get("PROD_OPENAI_TEXT_MODEL", "gpt-5-mini")
    if o_key:
        os.environ["IMAGE_PROVIDER"] = "openai"
        if os.environ.get("IMAGE_MODEL", "") in ("", "nano_banana_2", "gpt_image_2", "gpt_image_1_5"):
            os.environ["IMAGE_MODEL"] = os.environ.get("PROD_IMAGE_MODEL", "gpt-image-1")
    elif os.environ.get("IMAGE_PROVIDER", "") == "pplx":
        os.environ["IMAGE_PROVIDER"] = "none"
    print(f"[boot] credentials wired: text={os.environ['LLM_PROVIDER']}/{os.environ['LLM_MODEL']} images={os.environ.get('IMAGE_PROVIDER')}/{os.environ.get('IMAGE_MODEL')}")


# Env var names the hosting platform injects for saved vault credentials.
ANTHROPIC_URL_VAR = "CUSTOM_CRED_API_ANTHROPIC_COM_URL"
ANTHROPIC_KEY_VAR = "CUSTOM_CRED_API_ANTHROPIC_COM_PROXY_AUTH_KEY"
OPENAI_URL_VAR = "CUSTOM_CRED_API_OPENAI_COM_URL"
OPENAI_KEY_VAR = "CUSTOM_CRED_API_OPENAI_COM_PROXY_AUTH_KEY"
_wire_credentials()
# Legacy compatibility flag for local fixtures; the environment opt-in is no longer honored.
# Replit preview passes client-supplied X-Visitor-Id through, so it is not an identity credential.
TRUST_VISITOR_HEADER = False

from app import llm  # noqa: E402
from app import architect as arch  # noqa: E402
from app import mixer  # noqa: E402
from app.partial_json import parse_partial  # noqa: E402
from app.provenance import mint, new_token, now_iso, unique_slug  # noqa: E402
from app.store import RENDER_LEASE_SECONDS, build_store, day_start_iso, hours_ago_iso, lease_expired  # noqa: E402
from app.owner_auth import app_owner_token, is_app_owner, router as owner_auth_router  # noqa: E402

store = build_store()


@asynccontextmanager
async def lifespan(app):
    if store.kind == "supabase":
        await store.initialize()
    yield


app = FastAPI(title="MANIFEST", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(owner_auth_router)

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

RENDER_COUNT = max(0, min(3, int(os.environ.get("RENDER_COUNT", "2"))))   # product renders per venture
GEN_PER_HOUR = int(os.environ.get("GEN_PER_HOUR", "3"))
GEN_PER_DAY = int(os.environ.get("GEN_PER_DAY", "8"))
GLOBAL_PER_DAY = int(os.environ.get("GLOBAL_PER_DAY", "150"))
MARK_SIGNALS = int(os.environ.get("MARK_THRESHOLD_SIGNALS", "25"))
MARK_BUILD_FUND = int(os.environ.get("MARK_THRESHOLD_BUILD_FUND", "5"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
IMAGE_CONCURRENCY = asyncio.Semaphore(int(os.environ.get("IMAGE_CONCURRENCY", "3")))

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Generation admission (durable visitor quotas and global in-flight capacity)
# ---------------------------------------------------------------------------

def _caller_id(request: Request) -> str:
    # Uvicorn resolves forwarded addresses only for configured trusted peers,
    # walking the chain from the right. Never re-read a client-controlled prefix.
    ip = request.client.host if request.client else "unknown"
    return f"ip:{ip}"


async def _release_admission(token: str | None) -> None:
    if token:
        try:
            await _shielded_bounded_call(store.release_generation(token))
        except Exception as exc:
            # A lost cleanup response is safe: the pending lease expires.
            print(f"[admission] cleanup failed: {exc}")


async def _reserve_admission(caller: str, *, global_slot: bool) -> str:
    token = new_token()
    try:
        reason = await _shielded_bounded_call(store.reserve_generation(
            token, caller, GEN_PER_HOUR, GEN_PER_DAY, GLOBAL_PER_DAY if global_slot else None))
        messages = {
            "hour": "You've manifested a lot in the last hour. Give the engine a moment.",
            "day": "That's your ventures for today. Come back tomorrow.",
            "global": "The engine has reached today's capacity. It resets at midnight UTC.",
        }
        if reason is not None:
            if reason not in messages:
                raise RuntimeError("Unknown generation admission response.")
            raise HTTPException(429, messages[reason])
        return token
    except BaseException:
        # Token known before acquisition, including an ambiguous network commit.
        await _release_admission(token)
        raise


async def _shielded_bounded_call(awaitable, timeout: float = 35):
    """Finish short store operations despite request cancellation, with a hard bound."""
    task = asyncio.create_task(awaitable)
    try:
        with anyio.move_on_after(timeout, shield=True) as timeout_scope:
            result = await asyncio.shield(task)
        if timeout_scope.cancel_called:
            task.cancel()
            with anyio.move_on_after(timeout, shield=True):
                try:
                    await asyncio.shield(task)
                except BaseException:
                    pass
            raise TimeoutError("Shielded store operation timed out.")
        return result
    except asyncio.CancelledError:
        with anyio.move_on_after(timeout, shield=True):
            try:
                await asyncio.shield(task)
            except BaseException:
                pass
        raise


# __Host- prefix: required by the pplx.app proxy (it drops other cookie names) and a good idea anywhere.
COOKIE = "__Host-manifest_owner"


def _owner_token(request: Request) -> str:
    """Use the verified app-owner account, otherwise the first-party creator cookie."""
    authenticated_token = getattr(request.state, "app_owner_token", None)
    if authenticated_token:
        return authenticated_token
    return request.state.owner


def _is_creator(request: Request, venture: dict) -> bool:
    """An owner account also keeps access to its pre-login browser's ventures."""
    tokens = {_owner_token(request), getattr(request.state, "owner", None)}
    return bool(venture.get("owner_token")) and venture["owner_token"] in tokens


@app.middleware("http")
async def _identity_cookie(request: Request, call_next):
    """Give every visitor a private, HttpOnly creator key on first contact."""
    token = request.cookies.get(COOKIE)
    fresh = False
    if not token or len(token) < 16:
        token, fresh = new_token(), True
    request.state.owner = token
    if request.url.path.startswith(("/api/manifest", "/api/mix/", "/api/ventures/", "/api/venture-images/", "/renders/")):
        request.state.app_owner_token = await app_owner_token(request)
    response = await call_next(request)
    if fresh:
        response.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 365 * 2, httponly=True,
                            samesite="lax", secure=True, path="/")
    return response


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------

def _image_urls(v: dict) -> list:
    return [store.render_url(v, i) for i in range(len(v.get("images") or []))]


def _public_view(v: dict, *, is_owner: bool, counts: dict | None = None, needs: list | None = None,
                 unlimited: bool = False) -> dict:
    counts = counts or {
        "buy": v.get("buy_count", 0), "build": v.get("build_count", 0), "fund": v.get("fund_count", 0),
        "total": v.get("signal_count", 0), "avg_price": None,
    }
    grounding = dict(v.get("grounding") or {})
    has_refinement_marker = "_refinement" in grounding
    marker = grounding.pop("_refinement", None)
    refinement = None
    if is_owner:
        marker = marker if isinstance(marker, dict) else {}
        state = marker.get("state")
        if state in ("completed", "skipped"):
            refinement = {"state": state}
            if marker.get("context") is not None:
                refinement["context"] = marker["context"]
        elif state == "pending" or v.get("status") == "refining":
            refinement = {"state": "pending"}
            if marker.get("context") is not None:
                refinement["context"] = marker["context"]
        elif (
            v.get("status") == "ready"
            and v.get("visibility") == "private"
            and v.get("path") is None
            and not has_refinement_marker
        ):
            refinement = {
                "state": "waiting" if v.get("images_status") == "pending" else "available"
            }
        else:
            refinement = {"state": "unavailable"}
        if (
            unlimited and v.get("visibility") == "private" and v.get("path") is None
            and v.get("status") in ("ready", "refining")
        ):
            refinement["unlimited"] = True
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
        "grounding": grounding,
        "images": _image_urls(v),
        "render_count": len(v.get("image_prompts") or []),
        "splices": (v.get("splices") or None) if is_owner else None,
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
        "refinement": refinement,
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
        "accent": v.get("accent") or "#e8eeff", "image": (_image_urls(v) or [None])[0],
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
    prompts = [str(p) for p in (data.get("image_prompts") or []) if isinstance(p, str)][:RENDER_COUNT]
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


def _complete_refinement(buf: str) -> dict:
    """Reject incomplete or structurally hollow provider replies before saving."""
    try:
        data = json.loads(buf)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("The engine returned incomplete JSON.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"].strip():
        raise RuntimeError("The engine returned no usable venture name.")
    if not isinstance(data.get("description"), str) or len(data["description"].strip()) < 20:
        raise RuntimeError("The engine returned no usable venture description.")
    tiers = data.get("tiers")
    if not isinstance(tiers, list) or not any(
        isinstance(tier, dict) and str(tier.get("title") or "").strip()
        and str(tier.get("description") or "").strip()
        for tier in tiers
    ):
        raise RuntimeError("The engine returned no usable venture tiers.")
    roadmap = data.get("roadmap")
    if not isinstance(roadmap, list) or not any(
        isinstance(step, dict) and str(step.get("action") or "").strip()
        and str(step.get("where") or "").strip()
        for step in roadmap
    ):
        raise RuntimeError("The engine returned no usable venture roadmap.")
    grounding = data.get("grounding")
    grounding_fields = ("exists", "who_pays", "first_hundred")
    if not isinstance(grounding, dict) or any(
        not isinstance(grounding.get(key), str) or not grounding[key].strip()
        for key in grounding_fields
    ):
        raise RuntimeError("The engine returned no usable venture grounding.")
    prompts = data.get("image_prompts")
    if not isinstance(prompts, list) or not any(
        isinstance(prompt, str) and prompt.strip() for prompt in prompts
    ):
        raise RuntimeError("The engine returned no usable image prompts.")
    return data


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


TEXT_TIMEOUT_SECONDS = 120


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class ManifestBody(BaseModel):
    idea: str = Field(min_length=8, max_length=300)
    mix_id: str | None = Field(default=None, max_length=100)


@app.post("/api/manifest")
async def manifest(body: ManifestBody, request: Request):
    idea = " ".join(body.idea.split())
    chosen = None
    if body.mix_id:
        saved_mix = _mix_snapshot(body.mix_id, "mix", _owner_token(request))
        chosen = next((c for c in saved_mix["concepts"] if " ".join(c["sentence"].split()) == idea), None)
        if not chosen:
            raise HTTPException(422, "Choose an unchanged concept from this mix, or submit your own sentence without a mix.")
    caller = _caller_id(request)
    unlimited = await is_app_owner(request)
    owner = _owner_token(request)
    created_at = now_iso()
    prov = mint(idea, created_at, owner)
    angle = random.choice(llm.ANGLES)
    if chosen:
        angle = ("Preserve this selected hybrid's exact product, buyer and borrowed mechanisms. "
                 "Do not reinterpret it as an unrelated venture. Concept: " + json.dumps(chosen, ensure_ascii=False))
    admission = None if unlimited else await _reserve_admission(caller, global_slot=True)

    async def gen():
        buf = ""
        text_completed = False
        last_emit = 0.0
        last_key = None
        try:
            yield _sse("meta", {"provenance": prov, "owner_token": owner, "created_at": created_at})
            # A bounded worker cannot outlive its five-minute database lease.
            with anyio.fail_after(TEXT_TIMEOUT_SECONDS):
                avoid = [] if chosen else await store.recent_concepts(owner)
                stream_options = {"angle": angle, "avoid": avoid}
                if chosen:
                    stream_options["preserve"] = True
                async for chunk in llm.stream_venture_text(idea, **stream_options):
                    buf += chunk
                    now = time.time()
                    obj, key = parse_partial(buf)
                    if obj and (now - last_emit > 0.07 or key != last_key):
                        last_emit, last_key = now, key
                        yield _sse("partial", {"data": obj, "streaming": key})
            text_completed = True
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
            saved = await _shielded_bounded_call(
                store.create_reserved_venture(admission, row) if admission else store.create_venture(row))
            if saved["images_status"] == "pending":
                asyncio.create_task(_render_images(saved))
            yield _sse("done", _public_view(saved, is_owner=True, unlimited=unlimited))
        except HTTPException as e:
            yield _sse("error", {"message": e.detail})
        except TimeoutError:
            message = ("Generation reached its two-minute limit. No venture was saved. Please try again."
                       if not text_completed else "Saving took too long. Check your ventures before trying again.")
            yield _sse("error", {"message": message})
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": f"The engine stalled: {str(e)[:200]}"})
        finally:
            # Only pending entries are released; a successful atomic commit is
            # never refunded even if the client disconnects before seeing done.
            await _release_admission(admission)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _render_images(v: dict) -> None:
    marker = (v.get("grounding") or {}).get("_refinement") or {}
    render_claim_id = None
    if marker.get("state") == "completed":
        # Acquisition belongs to the worker, not the request that queues it.
        # A crash at either side of task creation leaves recoverable pending work.
        v = await store.claim_refinement_render(v["id"])
        if not v:
            return
        render_claim_id = v["grounding"]["_refinement"]["render_claim_id"]
    worker_store = store
    prompts = v.get("image_prompts") or []
    urls: list[str | None] = [None] * len(prompts)

    async def persist(fields: dict):
        if render_claim_id:
            return await worker_store.update_refinement_images(v["id"], render_claim_id, fields)
        return await worker_store.update_venture(v["id"], fields)

    async def one(i: int, prompt: str):
        async with IMAGE_CONCURRENCY:
            try:
                png = await llm.generate_render(prompt, v.get("accent") or "#e8eeff")
                if png:
                    if render_claim_id:
                        urls[i] = await worker_store.save_render(
                            v["id"], i, png, render_claim_id=render_claim_id,
                        )
                    else:
                        urls[i] = await worker_store.save_render(v["id"], i, png)
                    # progressive: write each image as it lands
                    await persist({"images": urls})
            except Exception as e:  # noqa: BLE001
                print(f"[render] {v['slug']} #{i} failed: {e}")

    try:
        # Includes concurrency queueing and storage writes; finish before lease expiry.
        async with asyncio.timeout(RENDER_LEASE_SECONDS - 30):
            await asyncio.gather(*(one(i, p) for i, p in enumerate(prompts)))
    except TimeoutError:
        print(f"[render] {v['slug']} timed out")
    status = "ready" if any(urls) else "failed"
    await persist({"images": urls, "images_status": status})


def _owns_completed_refinement(v: dict | None, claim_id: str) -> bool:
    marker = ((v or {}).get("grounding") or {}).get("_refinement") or {}
    return marker.get("state") == "completed" and marker.get("claim_id") == claim_id


async def _commit_refinement_and_schedule(v: dict, fields: dict, claim_id: str) -> dict:
    """Commit refinement and schedule renders even if the commit response is lost."""
    saved = None
    finish_error: BaseException | None = None
    try:
        saved = await store.finish_refinement(v["id"], fields, claim_id=claim_id)
    except asyncio.CancelledError as exc:
        finish_error = exc
    except Exception as exc:  # noqa: BLE001
        finish_error = exc

    if not _owns_completed_refinement(saved, claim_id):
        try:
            persisted = await store.get_by_id(v["id"])
        except Exception:
            if finish_error:
                raise finish_error
            raise
        if _owns_completed_refinement(persisted, claim_id):
            saved = persisted
        elif finish_error:
            raise finish_error
        else:
            raise RuntimeError("The refinement claim could not be completed.")

    # No await between the durable completion check and task creation. This runs
    # inside a shielded bounded task so a disconnect cannot strand pending renders.
    if saved.get("images_status") == "pending":
        asyncio.create_task(_render_images(saved))
    return saved


# ---------------------------------------------------------------------------
# Ventures
# ---------------------------------------------------------------------------

async def _deliver_render(venture_id: str, idx: int, ext: str, request: Request):
    headers = {"Cache-Control": "private, no-store", "Vary": "Cookie, X-Visitor-Id",
               "X-Content-Type-Options": "nosniff"}
    v = await store.get_by_id(venture_id)
    expected = f"/api/venture-images/{venture_id}/{idx}.{ext}"
    if (not v or (v.get("visibility") != "public" and not _is_creator(request, v))
            or store.render_url(v, idx) != expected):
        return JSONResponse({"error": "Image not found."}, status_code=404, headers=headers)
    image = await store.read_render(v, idx)
    if image is None:
        return JSONResponse({"error": "Image not found."}, status_code=404, headers=headers)
    data, mime = image
    return Response(data, media_type=mime, headers=headers)


@app.get("/api/venture-images/{venture_id}/{idx}.{ext}")
async def venture_image(venture_id: str, idx: int, ext: str, request: Request):
    return await _deliver_render(venture_id, idx, ext, request)


@app.get("/renders/{filename:path}")
async def legacy_render(filename: str, request: Request):
    # Protect already-issued SQLite URLs too, including on Supabase deployments.
    match = re.fullmatch(r"([a-zA-Z0-9-]+)-(\d+)\.(png|webp)", filename)
    if store.kind != "sqlite" or not match:
        return JSONResponse({"error": "Image not found."}, status_code=404,
                            headers={"Cache-Control": "private, no-store"})
    return await _deliver_render(match[1], int(match[2]), match[3], request)


async def _load(slug: str, request: Request) -> tuple[dict, bool]:
    v = await store.get_by_slug(slug)
    if not v:
        raise HTTPException(404, "No venture lives at this address.")
    is_owner = _is_creator(request, v)
    if v.get("visibility") != "public" and not is_owner:
        raise HTTPException(404, "This venture hasn't been released by its creator.")
    v = await store.reconcile_venture(v)
    marker = (v.get("grounding") or {}).get("_refinement") or {}
    if (
        marker.get("state") == "completed" and v.get("images_status") == "pending"
        and (not marker.get("render_claim_id") or lease_expired(
            marker, "render_lease_until", v.get("updated_at")
        ))
    ):
        asyncio.create_task(_render_images(v))
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


async def _full_view(v: dict, is_owner: bool, request: Request | None = None) -> dict:
    counts = await store.signal_counts(v["id"])
    unlimited = bool(is_owner and request is not None and await is_app_owner(request))
    return _public_view(v, is_owner=is_owner, counts=counts, needs=await _needs_view(v, is_owner),
                        unlimited=unlimited)


@app.get("/api/ventures/{slug}")
async def get_venture(slug: str, request: Request):
    v, is_owner = await _load(slug, request)
    return await _full_view(v, is_owner, request)


class RefinementBody(BaseModel):
    context: str


@app.post("/api/ventures/{slug}/refine")
async def refine_venture(slug: str, body: RefinementBody, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can refine this venture.")
    context = body.context.strip()
    if not context or len(context) > 1000:
        raise HTTPException(400, "Context must be between 1 and 1000 characters.")
    caller = _caller_id(request)
    unlimited = await is_app_owner(request)
    admission = None if unlimited else await _reserve_admission(caller, global_slot=False)
    original_grounding = dict(v.get("grounding") or {})
    claim_id = new_token()
    completed = False
    text_completed = False
    try:
        # The store may commit its conditional PATCH just before a request is
        # cancelled or the network drops its response. Keep acquisition shielded,
        # then always attempt a token-scoped cleanup in the finally block.
        claimed = await _shielded_bounded_call(
            store.claim_refinement(v["id"], context, require_images_finished=True,
                                   claim_id=claim_id, allow_repeat=unlimited)
        )
        if not claimed:
            raise HTTPException(409, "This venture is not available for refinement.")
        v = claimed
        if admission:
            # Preserve the existing charge-on-start refinement policy.
            await _shielded_bounded_call(store.consume_refinement_admission(admission))

        buf = ""
        with anyio.fail_after(TEXT_TIMEOUT_SECONDS):
            async for chunk in llm.stream_refinement_text(v, context):
                buf += chunk
        text_completed = True
        obj = _complete_refinement(buf)
        data = _normalize(obj)
        marker = {"state": "completed", "context": context, "claim_id": claim_id}
        grounding = dict(data["grounding"])
        grounding["_refinement"] = marker
        images_status = (
            "pending"
            if llm.image_provider() != "none" and data["image_prompts"]
            else "failed"
        )
        saved = await _shielded_bounded_call(
            _commit_refinement_and_schedule(
                v,
                {
                    **{k: data[k] for k in (
                        "name", "tagline", "description", "category", "accent", "tiers",
                        "roadmap", "image_prompts",
                    )},
                    "grounding": grounding,
                    "images": [],
                    "images_status": images_status,
                    "status": "ready",
                },
                claim_id,
            ),
            timeout=65,
        )
        completed = True
        return await _full_view(saved, True, request)
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except TimeoutError as exc:
        message = ("Refinement reached its two-minute limit. Your original result is unchanged. Please try again."
                   if not text_completed else "Saving the refinement took too long. Refresh to check whether it finished.")
        raise HTTPException(504, message) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Refinement failed: {str(exc)[:200]}") from exc
    finally:
        await _release_admission(admission)
        if not completed:
            try:
                await _shielded_bounded_call(
                    store.clear_refinement_claim(v["id"], original_grounding, claim_id)
                )
            except Exception as cleanup_error:  # noqa: BLE001
                print(f"[refine] {slug} claim cleanup failed: {cleanup_error}")


@app.post("/api/ventures/{slug}/refinement/skip")
async def skip_refinement(slug: str, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can skip refinement.")
    saved = await store.claim_refinement(
        v["id"], skipped=True, require_images_finished=False, allow_repeat=await is_app_owner(request)
    )
    if not saved:
        fresh = await store.get_by_id(v["id"])
        marker = ((fresh or {}).get("grounding") or {}).get("_refinement", {})
        if marker.get("state") == "skipped":
            return await _full_view(fresh, True, request)
        raise HTTPException(409, "This venture is not available to skip refinement.")
    return await _full_view(saved, True, request)


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


async def _bounded_path_text(v: dict, tier: int):
    # Keep provider work shorter than the durable claim's five-minute lease.
    async with asyncio.timeout(TEXT_TIMEOUT_SECONDS):
        async for chunk in llm.stream_path_text(v, tier):
            yield chunk


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
    claim_id = new_token()
    try:
        claimed = await _shielded_bounded_call(store.claim_path(v["id"], claim_id))
    except BaseException:
        try:
            await _shielded_bounded_call(store.release_path_claim(v["id"], claim_id))
        except Exception as cleanup_error:  # noqa: BLE001
            print(f"[path] {slug} claim cleanup failed: {cleanup_error}")
        raise
    if not claimed:
        raise HTTPException(409, "This venture is already being refined or planned.")
    v = claimed
    tier_label = (v.get("tiers") or [{}] * 3)[tier].get("label") or f"Path {tier + 1}"

    async def gen():
        buf = ""
        last_emit, last_key = 0.0, None
        finished = False
        text_completed = False
        try:
            async for chunk in _bounded_path_text(v, tier):
                buf += chunk
                now = time.time()
                obj, key = parse_partial(buf)
                if obj and (now - last_emit > 0.08 or key != last_key):
                    last_emit, last_key = now, key
                    yield _sse("partial", {"data": obj, "streaming": key})
            text_completed = True
            obj, _ = parse_partial(buf)
            if not obj or not obj.get("steps"):
                raise RuntimeError("The engine returned nothing usable.")
            path = _normalize_path(obj, tier, tier_label)
            saved = await store.finish_path(v["id"], path, claim_id)
            if not saved:
                raise RuntimeError("The path claim could not be completed.")
            finished = True
            yield _sse("done", await _full_view(saved, True, request))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            message = ("The path reached its two-minute limit. Your venture is unchanged. Please try again."
                       if not text_completed else "Saving the path took too long. Refresh to check whether it finished.")
            yield _sse("error", {"message": message})
        except Exception as e:  # noqa: BLE001
            print(f"[path] {slug} failed: {e}")
            yield _sse("error", {"message": str(e)[:300] or "The engine stalled."})
        finally:
            if not finished:
                try:
                    await _shielded_bounded_call(store.release_path_claim(v["id"], claim_id))
                except Exception as cleanup_error:  # noqa: BLE001
                    print(f"[path] {slug} claim cleanup failed: {cleanup_error}")

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
    return await _full_view(saved, True, request)


@app.delete("/api/ventures/{slug}/path")
async def reset_path(slug: str, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can reset the path.")
    saved = await store.reset_path(v["id"])
    return await _full_view(saved, True, request)


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
    return await _full_view(v, True, request)


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
    return await _full_view(v, True, request)


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
    out = await _full_view(v, is_owner, request)
    out["added"] = added
    return out


@app.post("/api/ventures/{slug}/release")
async def release(slug: str, request: Request):
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can release a venture.")
    released = await store.release_venture(v["id"], now_iso())
    if not released:
        fresh = await store.get_by_id(v["id"])
        if fresh and fresh.get("visibility") == "public":
            v = fresh
        else:
            raise HTTPException(409, "A venture cannot be released while it is being refined.")
    else:
        v = released
    return await _full_view(v, True, request)


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
    out = _public_view(v, is_owner=is_owner, counts=counts, needs=await _needs_view(v, is_owner),
                       unlimited=bool(is_owner and await is_app_owner(request)))
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


# ---------------------------------------------------------------------------
# The Architect: cross-Floor readings, cached, regenerated only when the rows change
# ---------------------------------------------------------------------------
# Ephemeral research expires after an hour; never silently substitute a new set.
_MIX_SNAPSHOTS: dict[str, dict] = {}
_MIX_HITS: dict[str, deque] = defaultdict(deque)
_MIX_GLOBAL: deque = deque()
MIX_TTL = 3600


def _mix_save(kind: str, owner: str, payload: dict) -> str:
    now = time.time()
    for key in list(_MIX_SNAPSHOTS):
        if _MIX_SNAPSHOTS[key]["expires"] <= now:
            del _MIX_SNAPSHOTS[key]
    if len(_MIX_SNAPSHOTS) >= 256:
        del _MIX_SNAPSHOTS[next(iter(_MIX_SNAPSHOTS))]
    token = new_token()
    _MIX_SNAPSHOTS[token] = {"kind": kind, "owner": owner, "expires": now + MIX_TTL, **payload}
    return token


def _mix_snapshot(token: str, kind: str, owner: str) -> dict:
    row = _MIX_SNAPSHOTS.get(token)
    if not row or row["kind"] != kind or row["owner"] != owner or row["expires"] <= time.time():
        raise HTTPException(410, "This research or mix has expired. Research again to make a fresh combination.")
    return row


async def _mix_rate(request: Request) -> None:
    if await is_app_owner(request):
        return
    now = time.time()
    for key in list(_MIX_HITS):
        q = _MIX_HITS[key]
        while q and now - q[0] >= 86400:
            q.popleft()
        if not q:
            del _MIX_HITS[key]
    q = _MIX_HITS[_caller_id(request)]
    while _MIX_GLOBAL and now - _MIX_GLOBAL[0] >= 86400:
        _MIX_GLOBAL.popleft()
    if sum(now - t < 3600 for t in q) >= 6 or len(q) >= 12:
        raise HTTPException(429, "Research and mixing are limited to six runs an hour and twelve a day. Try again later.")
    if len(_MIX_GLOBAL) >= 100:
        raise HTTPException(429, "Today's market research capacity has been reached. Try again tomorrow.")
    # Reserve before awaiting the provider so concurrent requests cannot evade limits.
    q.append(now)
    _MIX_GLOBAL.append(now)


class ResearchBody(BaseModel):
    topic: str = Field(default="", max_length=120)


class CombineBody(BaseModel):
    research_id: str = Field(min_length=1, max_length=100)
    trend_ids: list[str] = Field(min_length=1, max_length=3)
    floor_slugs: list[str] = Field(default_factory=list, max_length=3)


@app.get("/api/mix/floor")
async def mix_floor():
    return {"ventures": [mixer.floor_brief(v) for v in await store.list_public(80)
                         if v.get("visibility") == "public" and v.get("status") == "ready"]}


@app.post("/api/mix/research")
async def mix_research(body: ResearchBody, request: Request):
    await _mix_rate(request)
    try:
        trends = await mixer.research(" ".join(body.topic.split()))
    except Exception:
        raise HTTPException(502, "Live research could not return verified source links. Try again shortly.")
    owner = _owner_token(request)
    token = _mix_save("research", owner, {"trends": trends})
    from datetime import datetime, timezone
    return {"research_id": token, "researched_at": now_iso(),
            "expires_at": datetime.fromtimestamp(_MIX_SNAPSHOTS[token]["expires"], timezone.utc).isoformat(),
            "trends": trends}


@app.post("/api/mix/combine")
async def mix_combine(body: CombineBody, request: Request):
    owner = _owner_token(request)
    research = _mix_snapshot(body.research_id, "research", owner)
    trend_ids, slugs = body.trend_ids, body.floor_slugs
    if (not 2 <= len(trend_ids) + len(slugs) <= 3
            or len(set(trend_ids)) != len(trend_ids) or len(set(slugs)) != len(slugs)):
        raise HTTPException(422, "Choose two or three different ingredients, including a market trend.")
    by_id = {t["id"]: t for t in research["trends"]}
    if any(t not in by_id for t in trend_ids):
        raise HTTPException(422, "Choose trends from the current research.")
    floor = []
    for slug in slugs:
        if len(slug) > 80:
            raise HTTPException(422, "Invalid Floor selection.")
        row = await store.get_by_slug(slug)
        if not row or row.get("visibility") != "public" or row.get("status") != "ready":
            raise HTTPException(404, "A selected venture is no longer on the public Floor.")
        floor.append(mixer.floor_brief(row))
    trends = [by_id[t] for t in trend_ids]
    await _mix_rate(request)
    try:
        concepts = await mixer.combine(trends, floor)
    except Exception:
        raise HTTPException(502, "The engine couldn't make three concrete combinations. Try different ingredients.")
    sources = {s["url"]: s for t in trends for s in t["sources"]}
    token = _mix_save("mix", owner, {"concepts": concepts})
    return {"mix_id": token, "concepts": concepts, "sources": list(sources.values()),
            "floor": [{"slug": v["slug"], "name": v["name"]} for v in floor], "generated_at": now_iso()}


_ARCH_TASK: asyncio.Task | None = None
_ARCH_LAST_RUN = 0.0
ARCH_MIN_INTERVAL = int(os.environ.get("ARCHITECT_MIN_INTERVAL", "300"))   # seconds between regenerations


async def _arch_requests():
    needs = await store.list_open_needs(60)
    return [{"id": n["id"], "title": n["title"], "detail": n.get("detail") or "", "step_n": n.get("step_n"),
             "offer_count": int(n.get("offer_count") or 0), "created_at": n["created_at"],
             "venture": {"slug": n["venture_slug"], "name": n["venture_name"], "accent": n.get("venture_accent") or "#e8eeff"}}
            for n in needs]


async def _arch_regenerate(facts: dict, h: str) -> None:
    global _ARCH_LAST_RUN
    _ARCH_LAST_RUN = time.time()
    try:
        readings, crosses = await arch.write_readings(facts)
        await store.set_architect(h, {"readings": readings, "crossbreeds": crosses}, now_iso())
        print(f"[architect] {len(readings)} readings, {len(crosses)} crossbreeds for {facts['counts']}")
    except Exception as e:  # noqa: BLE001
        print(f"[architect] failed: {e}")


def _arch_part(cached: dict | None, key: str) -> list:
    """Cache holds {"readings": [...], "crossbreeds": [...]}; older rows held a bare readings list."""
    data = (cached or {}).get("readings")
    if isinstance(data, dict):
        return data.get(key) or []
    return (data or []) if key == "readings" else []


@app.get("/api/architect")
async def architect_view():
    """What the whole Floor looks like from above. Facts are real rows; prose is the engine's, labeled as such."""
    global _ARCH_TASK
    rows = await store.list_public(80)
    reqs = await _arch_requests()
    facts = arch.compute_facts(rows, reqs)
    h = arch.facts_hash(facts)
    cached = await store.get_architect()
    is_thin = arch.thin(facts)
    fresh = bool(cached and cached.get("input_hash") == h)
    pending = False
    if not is_thin and not fresh and llm.text_provider() != "mock":
        running = _ARCH_TASK is not None and not _ARCH_TASK.done()
        if not running and time.time() - _ARCH_LAST_RUN >= ARCH_MIN_INTERVAL:
            _ARCH_TASK = asyncio.create_task(_arch_regenerate(facts, h))
            pending = True
        elif running:
            pending = True
    ventures = {v["slug"]: {"slug": v["slug"], "name": v.get("name"), "accent": v.get("accent") or "#e8eeff",
                            "image": next((u for u in (v.get("images") or []) if u), None)} for v in rows}
    asks = {a["id"]: a for a in reqs}
    return {
        "counts": facts["counts"], "thin": is_thin, "pending": pending, "fresh": fresh,
        "generated_at": (cached or {}).get("generated_at"),
        "readings": _arch_part(cached, "readings"), "crossbreeds": _arch_part(cached, "crossbreeds"),
        "ventures": ventures, "asks": asks,
        "patterns": {"convergence": facts["convergence"], "adjacency": facts["adjacency"], "ask_pairs": facts["ask_pairs"],
                     "echo": facts["echo"], "stalled_paths": facts["stalled_paths"], "unanswered_asks": facts["unanswered_asks"], "gaps": facts["gaps"]},
    }


class SpliceBody(BaseModel):
    with_slug: str | None = Field(default=None, max_length=80)   # another public venture, or None for a lateral field
    lateral: bool = False


SPLICE_PER_DAY = int(os.environ.get("SPLICE_PER_DAY", "6"))      # crossbreed runs per venture per day


@app.post("/api/ventures/{slug}/crossbreed")
async def crossbreed(slug: str, body: SpliceBody, request: Request):
    """Owner splices this venture with another on the Floor, or with a field from left. Result saved on the venture (owner-only)."""
    v, is_owner = await _load(slug, request)
    if not is_owner:
        raise HTTPException(403, "Only the creator can crossbreed this venture.")
    prev = v.get("splices") or {}
    today = day_start_iso()
    runs_today = [r for r in (prev.get("runs") or []) if r >= today]
    if len(runs_today) >= SPLICE_PER_DAY:
        raise HTTPException(429, f"{SPLICE_PER_DAY} crossbreeds a day per venture. Come back tomorrow.")
    donor = None
    field = None
    if body.with_slug and not body.lateral:
        donor = await store.get_by_slug(body.with_slug)
        if not donor or donor.get("visibility") != "public" or donor["slug"] == v["slug"]:
            raise HTTPException(404, "That venture isn't on the Floor.")
    elif not body.lateral:
        # Architect picks: the strongest adjacency on the Floor, else a random public venture, else go lateral
        rows = [r for r in await store.list_public(80) if r["slug"] != v["slug"]]
        if rows:
            facts = arch.compute_facts(rows + [v], await _arch_requests())
            adj = next((a for a in facts["adjacency"] if v["slug"] in a["slugs"]), None)
            pick = next((s for s in adj["slugs"] if s != v["slug"]), None) if adj else random.choice(rows)["slug"]
            donor = next((r for r in rows if r["slug"] == pick), None)
    if donor is None:
        field = random.choice(arch.LATERAL_FIELDS)
    try:
        splices = await arch.write_crossbreed(v, donor, field)
    except Exception as e:  # noqa: BLE001
        print(f"[crossbreed] {slug} failed: {e}")
        raise HTTPException(502, "The Architect stalled. Try again in a minute.")
    if not splices:
        raise HTTPException(502, "The Architect couldn't find a splice that holds. Try another donor.")
    result = {
        "donor": {"kind": "venture", "slug": donor["slug"], "name": donor["name"], "accent": donor.get("accent") or "#e8eeff"} if donor else {"kind": "field", "name": field},
        "splices": splices, "generated_at": now_iso(),
        "runs": (runs_today + [now_iso()])[-SPLICE_PER_DAY:],
        "history": ([{"donor": prev.get("donor"), "splices": prev.get("splices"), "generated_at": prev.get("generated_at")}] if prev.get("splices") else []) + (prev.get("history") or [])[:4],
    }
    saved = await store.update_venture(v["id"], {"splices": result})
    return await _full_view(saved, True)


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
@app.get("/architect", response_class=HTMLResponse)
@app.get("/mix", response_class=HTMLResponse)
async def index():
    return _shell()


@app.get("/v/{slug}", response_class=HTMLResponse)
async def venture_page(slug: str, request: Request):
    v = await store.get_by_slug(slug)
    og = None
    if v and v.get("visibility") == "public":
        first = next((u for u in _image_urls(v) if u), None)
        if first and first.startswith("/"):
            first = f"{PUBLIC_URL}{first}"
        og = {
            "title": f"{v['name']} — {v.get('tagline', '')}".strip(" —"),
            "description": v.get("description") or "",
            "image": first,
            "url": f"{PUBLIC_URL}/v/{slug}",
        }
    return _shell(og)


app.mount("/", StaticFiles(directory=PUBLIC, html=False), name="public")


@app.exception_handler(HTTPException)
async def http_exc(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
