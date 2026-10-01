"""Clerk-backed, optional app-owner identity for the vanilla MANIFEST site.

Normal visitors continue using MANIFEST's existing anonymous creator cookie.
This module only recognizes the single, explicitly configured app owner.
"""

from __future__ import annotations

import base64
import hmac
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx
try:
    import jwt
except ImportError:  # fail closed until the declared requirement is installed
    jwt = None
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

router = APIRouter()
PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
CLERK_PROXY_PATH = "/api/__clerk"
CLERK_FAPI = "https://frontend-api.clerk.dev"
CLERK_BACKEND = "https://api.clerk.com/v1"
_jwks_cache: dict[str, tuple[float, dict]] = {}
_JWKS_TTL = 300
_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host",
}


_DEV_FRONTEND_API = re.compile(
    r"^(([a-z]+)-){2}([0-9]{1,2})\.clerk\.accounts([a-z.]*)(dev|com)$",
    re.IGNORECASE,
)
_LEGACY_DEV_SUFFIXES = (".lcl.dev", ".lclstage.dev", ".lclclerk.com")


def _canonical_hostname(request: Request) -> str:
    """Use the public request hostname, matching Clerk's host-based key resolver."""
    forwarded = request.headers.get("x-forwarded-host", "")
    raw_host = forwarded.split(",", 1)[0].strip() or request.headers.get("host", "").strip()
    if raw_host.startswith("["):
        # Preserve IPv6 literal handling while stripping an optional port.
        end = raw_host.find("]")
        hostname = raw_host[1:end] if end > 0 else ""
    else:
        hostname = re.sub(r":\d+$", "", raw_host)
    return hostname.lower()


def _publishable_key(request: Request) -> str | None:
    """Mirror @clerk/shared publishableKeyFromHost(hostname, fallbackKey)."""
    fallback = (
        os.environ.get("VITE_CLERK_PUBLISHABLE_KEY")
        or os.environ.get("CLERK_PUBLISHABLE_KEY")
        or ""
    ).strip()
    if fallback.startswith(("pk_test_", "test_")):
        return fallback
    hostname = _canonical_hostname(request)
    if not hostname or any(char in hostname for char in "/\\\r\n"):
        return None
    frontend_api = f"clerk.{hostname}"
    is_legacy_dev = (
        frontend_api.startswith("clerk.")
        and frontend_api.endswith(_LEGACY_DEV_SUFFIXES)
        and not frontend_api.endswith(".prod.lclclerk.com")
    )
    prefix = "pk_test_" if _DEV_FRONTEND_API.fullmatch(frontend_api) or is_legacy_dev else "pk_live_"
    encoded = base64.urlsafe_b64encode(f"{frontend_api}$".encode("ascii")).decode("ascii").rstrip("=")
    return prefix + encoded


def _issuer_from_publishable_key(key: str) -> str | None:
    """Decode Clerk's public instance hostname from its publishable key."""
    if not isinstance(key, str):
        return None
    match = re.fullmatch(r"(?:pk_(?:test|live)_|(?:test|live)_)([A-Za-z0-9_-]+)", key)
    if not match:
        return None
    encoded = match.group(1)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        host = raw.decode("ascii").rstrip("$").lower()
    except (ValueError, UnicodeDecodeError):
        return None
    if not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host:
        return None
    return f"https://{host}"


def _configured_authorized_parties() -> set[str]:
    return {
        item.strip().rstrip("/")
        for item in os.environ.get("MANIFEST_AUTHORIZED_PARTIES", "").split(",")
        if item.strip()
    }


def _request_origin(request: Request) -> str | None:
    """Return the original app origin (the same forwarded-host rule as Clerk's proxy)."""
    forwarded_host = request.headers.get("x-forwarded-host", "")
    host = forwarded_host.split(",", 1)[0].strip() or request.headers.get("host", "").strip()
    if not host or any(char in host for char in "/\\\r\n"):
        return None
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    scheme = (
        forwarded_proto.split(",", 1)[0].strip().lower()
        or ("https" if os.environ.get("NODE_ENV") == "production" else request.url.scheme)
    )
    if scheme not in ("http", "https"):
        return None
    return f"{scheme}://{host}".rstrip("/")


async def _jwks(issuer: str) -> dict:
    cached = _jwks_cache.get(issuer)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]
    secret = os.environ.get("CLERK_SECRET_KEY", "").strip()
    if not secret:
        raise ValueError("Clerk server key is not configured.")
    # Fetch through Clerk's authenticated backend API, not a client-controlled
    # URL or an issuer endpoint supplied by the token.
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(
            f"{CLERK_BACKEND}/jwks",
            headers={"Authorization": f"Bearer {secret}"},
        )
        response.raise_for_status()
        document = response.json()
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("Clerk returned an invalid JWKS document.")
    _jwks_cache[issuer] = (now + _JWKS_TTL, document)
    return document


async def _decode_session_token(token: str, request: Request) -> dict | None:
    """Verify the Clerk session JWT signature and its required claims."""
    if jwt is None:
        return None
    key = _publishable_key(request)
    issuer = _issuer_from_publishable_key(key)
    origin = _request_origin(request)
    authorized = _configured_authorized_parties()
    # The app's original request origin is the default authorized party. An
    # explicit allowlist supports deployments serving multiple trusted domains.
    if origin:
        authorized.add(origin)
    if not issuer or not authorized:
        return None
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            return None
        keys = (await _jwks(issuer)).get("keys", [])
        jwk = next((item for item in keys if item.get("kid") == header["kid"]), None)
        if not jwk:
            _jwks_cache.pop(issuer, None)
            keys = (await _jwks(issuer)).get("keys", [])
            jwk = next((item for item in keys if item.get("kid") == header["kid"]), None)
        if not jwk:
            return None
        signing_key = jwt.PyJWK.from_dict(jwk, algorithm="RS256").key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp", "iss", "sub", "sid", "azp"], "verify_aud": False},
        )
        azp = claims.get("azp")
        if not isinstance(azp, str) or azp.rstrip("/") not in authorized:
            return None
        if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("sid"), str):
            return None
        return claims
    except (jwt.PyJWTError, KeyError, TypeError, ValueError, httpx.HTTPError):
        return None


async def _clerk_api_get(path: str) -> dict | None:
    secret = os.environ.get("CLERK_SECRET_KEY", "").strip()
    if not secret:
        return None
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(
            f"{CLERK_BACKEND}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {secret}"},
        )
        if response.status_code >= 400:
            return None
        result = response.json()
    return result if isinstance(result, dict) else None


async def _verified_primary_email(session_id: str, user_id: str) -> str | None:
    """Confirm an active session and read its primary address from Clerk itself."""
    session = await _clerk_api_get(f"sessions/{quote(session_id, safe='')}")
    if not session or session.get("status") != "active" or session.get("user_id") != user_id:
        return None
    user = await _clerk_api_get(f"users/{quote(user_id, safe='')}")
    if not user or user.get("id") != user_id:
        return None
    primary_id = user.get("primary_email_address_id")
    if not isinstance(primary_id, str):
        return None
    addresses = user.get("email_addresses")
    if not isinstance(addresses, list):
        return None
    for address in addresses:
        verification = address.get("verification") if isinstance(address, dict) else None
        if (
            isinstance(address, dict)
            and address.get("id") == primary_id
            and isinstance(address.get("email_address"), str)
            and isinstance(verification, dict)
            and verification.get("status") == "verified"
        ):
            return address["email_address"].strip()
    return None


async def is_app_owner(request: Request) -> bool:
    """Fail-closed owner check: signed session, active Clerk account, verified primary email."""
    cached = getattr(request.state, "_manifest_owner_authenticated", None)
    if cached is not None:
        return bool(cached)
    expected_email = os.environ.get("MANIFEST_OWNER_EMAIL", "").strip()
    token = request.cookies.get("__session")
    if not expected_email or not token or not os.environ.get("CLERK_SECRET_KEY"):
        request.state._manifest_owner_authenticated = False
        return False
    try:
        claims = await _decode_session_token(token, request)
        if not claims:
            request.state._manifest_owner_authenticated = False
            return False
        actual_email = await _verified_primary_email(claims["sid"], claims["sub"])
        if not actual_email:
            request.state._manifest_owner_authenticated = False
            return False
        owner = hmac.compare_digest(
            actual_email.casefold().encode("utf-8"),
            expected_email.casefold().encode("utf-8"),
        )
        request.state._manifest_owner_authenticated = owner
        request.state._manifest_owner_user_id = claims["sub"] if owner else None
        return owner
    except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError):
        request.state._manifest_owner_authenticated = False
        return False


async def app_owner_token(request: Request) -> str | None:
    """Return a stable, opaque creator identity for the allowlisted owner only."""
    cached = getattr(request.state, "app_owner_token", None)
    if cached:
        return cached
    if not await is_app_owner(request):
        return None
    secret = os.environ.get("CLERK_SECRET_KEY", "").strip()
    user_id = getattr(request.state, "_manifest_owner_user_id", None)
    if not secret or not user_id:
        return None
    digest = hmac.new(
        secret.encode("utf-8"),
        f"manifest-owner:{user_id}".encode("utf-8"),
        "sha256",
    ).hexdigest()
    request.state.app_owner_token = digest
    return digest


@router.get("/api/owner-auth/config")
async def owner_auth_config(request: Request):
    key = _publishable_key(request)
    issuer = _issuer_from_publishable_key(key)
    if not key or not issuer:
        return JSONResponse({"error": "Clerk publishable key is not configured."}, status_code=503)
    proxy_url = os.environ.get("VITE_CLERK_PROXY_URL", "").strip() or None
    return {
        "publishableKey": key,
        "proxyUrl": proxy_url,
        "frontendApiHost": issuer.removeprefix("https://"),
    }


@router.get("/api/owner-auth/session")
async def owner_auth_session(request: Request):
    return {"is_owner": await is_app_owner(request)}


async def _clerk_proxy(request: Request, path: str = "") -> Response:
    if os.environ.get("NODE_ENV") != "production" or not os.environ.get("CLERK_SECRET_KEY"):
        return Response(status_code=404)
    origin = _request_origin(request)
    if not origin:
        return Response(status_code=400)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    protocol = forwarded_proto.split(",", 1)[0].strip() or "https"
    forwarded_host = request.headers.get("x-forwarded-host", "")
    host = forwarded_host.split(",", 1)[0].strip() or request.headers.get("host", "").strip()
    if not host:
        return Response(status_code=400)
    proxy_url = f"{protocol}://{host}{CLERK_PROXY_PATH}"

    incoming = dict(request.headers)
    connection_tokens = {
        token.strip().lower()
        for token in incoming.get("connection", "").split(",")
        if token.strip()
    }
    headers = {
        name: value for name, value in incoming.items()
        if name.lower() not in _HOP_BY_HOP and name.lower() not in connection_tokens
        and name.lower() not in {"clerk-secret-key", "clerk-proxy-url"}
    }
    headers.update({
        "Clerk-Proxy-Url": proxy_url,
        "Clerk-Secret-Key": os.environ["CLERK_SECRET_KEY"],
    })
    forwarded_for = incoming.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",", 1)[0].strip() or (
        request.client.host if request.client else ""
    )
    if client_ip:
        headers["X-Forwarded-For"] = client_ip

    target = f"{CLERK_FAPI}/{path.lstrip('/')}"
    if request.url.query:
        target += f"?{request.url.query}"
    client = httpx.AsyncClient(timeout=45, follow_redirects=False)
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                target,
                headers=headers,
                content=await request.body(),
            ),
            stream=True,
        )
        status_code = upstream.status_code
        response_headers = [
            (name, value) for name, value in upstream.headers.raw
            if name.decode("latin-1").lower() not in _HOP_BY_HOP
            and name.decode("latin-1").lower() != "content-length"
        ]
        upstream_length = upstream.headers.get("content-length")
        bodyless = (
            request.method == "HEAD"
            or status_code < 200
            or status_code in (204, 304)
        )
        if status_code < 200 or status_code == 204:
            upstream_length = None
        if bodyless:
            await upstream.aclose()
            await client.aclose()
            if upstream_length is not None:
                response_headers.append((b"content-length", upstream_length.encode("ascii")))
            response = Response(status_code=status_code)
            response.raw_headers = response_headers
            return response
        if upstream_length is not None:
            response_headers.append((b"content-length", upstream_length.encode("ascii")))

            async def close_stream():
                await upstream.aclose()
                await client.aclose()

            response = StreamingResponse(
                upstream.aiter_raw(),
                status_code=status_code,
                background=BackgroundTask(close_stream),
            )
            response.raw_headers = response_headers
            return response

        # Dynamic Frontend API documents arrive without a known length.
        # Buffer those so the hosting edge receives Content-Length rather
        # than rejecting a chunked response.
        body = b"".join([chunk async for chunk in upstream.aiter_raw()])
        await upstream.aclose()
        await client.aclose()
        response_headers.append((b"content-length", str(len(body)).encode("ascii")))
        response = Response(content=body, status_code=status_code)
        response.raw_headers = response_headers
        return response
    except httpx.HTTPError:
        await client.aclose()
        return Response(status_code=502)


@router.api_route("/api/__clerk", methods=_PROXY_METHODS, include_in_schema=False)
async def clerk_proxy_root(request: Request):
    return await _clerk_proxy(request)


@router.api_route("/api/__clerk/{path:path}", methods=_PROXY_METHODS, include_in_schema=False)
async def clerk_proxy_path(request: Request, path: str):
    return await _clerk_proxy(request, path)


@router.get("/sign-in", include_in_schema=False)
@router.get("/sign-in/{path:path}", include_in_schema=False)
async def sign_in_page(path: str = ""):
    return FileResponse(PUBLIC_DIR / "sign-in.html")


@router.get("/sign-up", include_in_schema=False)
@router.get("/sign-up/{path:path}", include_in_schema=False)
async def sign_up_page(path: str = ""):
    return FileResponse(PUBLIC_DIR / "sign-up.html")