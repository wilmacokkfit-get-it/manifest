"""Opt-in live Storage checks. Never use the configured application bucket.

Run with SUPABASE_PRIVACY_INTEGRATION=1; see docs/supabase-image-privacy.md.
All mutations are limited to a newly created bucket, venture, and Auth user.
"""

import asyncio
import base64
import os
import secrets
import time
import unittest
import uuid

import httpx

from app.store import SupabaseStore
from test_regressions import private_venture, server


LIVE = os.environ.get("SUPABASE_PRIVACY_INTEGRATION") == "1"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+a9WQAAAAASUVORK5CYII="
)
DENIED = {400, 401, 403, 404}


@unittest.skipUnless(LIVE, "set SUPABASE_PRIVACY_INTEGRATION=1 for disposable live fixtures")
class SupabaseImagePrivacyLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_modern_secret(self):
        await self.check_backend("SUPABASE_SECRET_KEY", modern=True)

    async def test_legacy_service_role(self):
        await self.check_backend("SUPABASE_SERVICE_ROLE_KEY", modern=False)

    def require(self, name):
        value = os.environ.get(name, "").strip()
        if not value:
            self.fail(f"Live check requires {name}; no fallback to another credential format.")
        return value

    def status(self, response, allowed, operation):
        # Do not include response bodies or request headers: they can contain secrets.
        self.assertIn(response.status_code, allowed, f"{operation}: HTTP {response.status_code}")

    async def check_backend(self, key_name, *, modern):
        key = self.require(key_name)
        self.assertEqual(key.startswith("sb_secret_"), modern, f"{key_name}: wrong key format")
        browser_key = (
            os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
            or os.environ.get("SUPABASE_ANON_KEY", "").strip()
        )
        if not browser_key and os.environ.get("SUPABASE_PRIVACY_REQUIRE_BROWSER", "1") != "0":
            self.fail("Full live check requires a browser API key (see privacy check guide)")
        run = uuid.uuid4().hex
        bucket = f"manifest-privacy-test-{run}"
        store = SupabaseStore(self.require("SUPABASE_URL"), key, bucket)
        self.addAsyncCleanup(store._client.aclose)
        # This ID and owner are generated here, never read from application data.
        row = private_venture(slug=f"privacy-test-{run}", owner=secrets.token_urlsafe(32))
        image = PNG + run.encode()
        objects = {f"{row['id']}/0.png": image, f"{row['id']}/1.png": PNG}
        public_path = f"/storage/v1/object/public/{bucket}/{row['id']}/0.png"
        row["images"] = [f"{store.url}{public_path}"]
        bucket_created = False
        venture_attempted = False
        auth_user_id = None
        original_store, original_trust = server.store, server.TRUST_VISITOR_HEADER
        print(f"\n[live privacy] mode={'modern' if modern else 'legacy'} fixture={bucket}", flush=True)

        async with httpx.AsyncClient(base_url=store.url, timeout=30) as anonymous:
            try:
                # POST only: a collision fails rather than opening an existing bucket.
                response = await store._client.post(
                    "/storage/v1/bucket", json={"id": bucket, "name": bucket, "public": True},
                )
                self.status(response, {200, 201}, "create disposable public bucket")
                bucket_created = True
                for path, data in objects.items():
                    response = await store._client.post(
                        f"/storage/v1/object/{bucket}/{path}", content=data,
                        headers={"Content-Type": "image/png", "cache-control": "60"},
                    )
                    self.status(response, {200, 201}, "upload fixture (no upsert)")
                # Refuse to mutate any row that existed before our insert.
                self.assertIsNone(await store.get_by_id(row["id"]))
                venture_attempted = True
                saved = await store.create_venture(row)
                snapshot = await store.get_by_id(row["id"])
                before = await self.object_snapshot(store, row["id"])

                # Warm the exact old URL; also check a cache-busted URL after closing.
                warm = await anonymous.get(public_path)
                self.status(warm, {200}, "anonymous public URL before privatization")
                self.assertEqual(warm.content, image)
                print(f"[live privacy] warm cache headers={self.cache_headers(warm)}", flush=True)

                server.store, server.TRUST_VISITOR_HEADER = store, False
                # ASGITransport does not start lifespan by itself.
                async with server.app.router.lifespan_context(server.app):
                    response = await store._client.get(f"/storage/v1/bucket/{bucket}")
                    self.status(response, {200}, "read private bucket")
                    self.assertIs(response.json()["public"], False)
                    self.assertEqual(await store.get_by_id(row["id"]), snapshot)
                    self.assertEqual(await self.object_snapshot(store, row["id"]), before)
                    for path, data in objects.items():
                        response = await store._client.get(
                            f"/storage/v1/object/authenticated/{bucket}/{path}",
                        )
                        self.status(response, {200}, "backend download retained object")
                        self.assertEqual(response.content, data)

                    await self.wait_for_public_denial(anonymous, public_path, run)
                    routes = (
                        public_path,
                        public_path.replace("/object/public/", "/object/authenticated/"),
                        public_path.replace("/object/public/", "/object/"),
                    )
                    await self.check_direct_denial(anonymous, routes, "no credentials")

                    # Browser API keys exercise anon RLS rather than just the API
                    # gateway. A disposable signed-in user additionally exercises
                    # policies granted to the authenticated role.
                    if browser_key:
                        headers = {"apikey": browser_key}
                        if not browser_key.startswith("sb_publishable_"):
                            headers["Authorization"] = f"Bearer {browser_key}"
                        async with httpx.AsyncClient(
                            base_url=store.url, headers=headers, timeout=30,
                        ) as browser:
                            await self.check_direct_denial(browser, routes, "browser anon role")
                            email = f"privacy-test-{run}@example.invalid"
                            password = secrets.token_urlsafe(36)
                            response = await store._client.post("/auth/v1/admin/users", json={
                                "email": email, "password": password, "email_confirm": True,
                                "app_metadata": {"manifest_privacy_test": run},
                            })
                            self.status(response, {200, 201}, "create disposable Auth user")
                            auth_user_id = response.json()["id"]
                            response = await browser.post(
                                "/auth/v1/token", params={"grant_type": "password"},
                                json={"email": email, "password": password},
                            )
                            self.status(response, {200}, "sign in disposable Auth user")
                            browser.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
                            await self.check_direct_denial(browser, routes, "browser authenticated role")
                        print("[live privacy] browser anon/authenticated policies: denied", flush=True)
                    else:
                        print(
                            "[live privacy] NOT CHECKED: browser-role policies; set "
                            "SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY for full coverage",
                            flush=True,
                        )
                    await self.check_app_delivery(store, saved, image)
                    # Release is an app permission change, not a Storage permission change.
                    await self.check_direct_denial(anonymous, routes, "after app release")
                    self.assertEqual(await self.object_snapshot(store, row["id"]), before)
                    print("[live privacy] retained objects and owner/released proxy delivery: OK", flush=True)
            finally:
                server.store, server.TRUST_VISITOR_HEADER = original_store, original_trust
                failures = []

                async def clean(method, path, **kwargs):
                    try:
                        response = await store._client.request(method, path, **kwargs)
                        if response.status_code not in {200, 204, 404}:
                            failures.append(f"{method} fixture cleanup: HTTP {response.status_code}")
                    except Exception as exc:
                        failures.append(f"{method} fixture cleanup: {type(exc).__name__}")

                # Exact identity + ownership filters even after an ambiguous insert failure.
                if venture_attempted:
                    await clean("DELETE", "/rest/v1/ventures", params={
                        "id": f"eq.{row['id']}", "slug": f"eq.{row['slug']}",
                        "owner_token": f"eq.{row['owner_token']}",
                    })
                if auth_user_id:
                    await clean("DELETE", f"/auth/v1/admin/users/{auth_user_id}")
                if bucket_created:
                    # Never empty a bucket wholesale or act on the app's renders bucket.
                    await clean("DELETE", f"/storage/v1/object/{bucket}", json={"prefixes": list(objects)})
                    await clean("DELETE", f"/storage/v1/bucket/{bucket}")
                self.assertFalse(failures, f"Cleanup incomplete for {bucket}: {'; '.join(failures)}")
                print(f"[live privacy] cleanup complete: {bucket}", flush=True)

    async def object_snapshot(self, store, venture_id):
        response = await store._client.post(
            f"/storage/v1/object/list/{store.bucket}",
            json={"prefix": venture_id, "limit": 100, "sortBy": {"column": "name", "order": "asc"}},
        )
        self.status(response, {200}, "list fixture objects")
        self.assertEqual({obj["name"] for obj in response.json()}, {"0.png", "1.png"})
        return response.json()

    @staticmethod
    def cache_headers(response):
        return {k: response.headers[k] for k in (
            "age", "cache-control", "cf-cache-status", "x-cache", "sb-cdn-cache-status",
        ) if k in response.headers}

    async def wait_for_public_denial(self, client, path, run):
        budget = float(os.environ.get("SUPABASE_PRIVACY_CACHE_WAIT_SECONDS", "90"))
        self.assertTrue(0 <= budget <= 600, "cache wait must be between 0 and 600 seconds")
        started = time.monotonic()
        stale_seen = False
        while True:
            warm = await client.get(path)
            cold = await client.get(path, params={"privacy_probe": run})
            if warm.status_code in DENIED and cold.status_code in DENIED:
                print(
                    f"[live privacy] public URLs denied after {time.monotonic() - started:.1f}s; "
                    f"post-update exposure observed={stale_seen}; headers={self.cache_headers(warm)}",
                    flush=True,
                )
                return
            # Network/server errors are not evidence of authorization denial.
            for response in (warm, cold):
                self.status(response, DENIED | {200}, "public URL revocation probe")
            if not stale_seen:
                print(
                    f"[live privacy] initial post-update public probe: warm={warm.status_code}, "
                    f"cache-busted={cold.status_code}, headers={self.cache_headers(warm)}",
                    flush=True,
                )
            stale_seen = True
            if time.monotonic() - started >= budget:
                self.fail(
                    f"Old public URL still readable after {budget}s: warm={warm.status_code}, "
                    f"cache-busted={cold.status_code}, headers={self.cache_headers(warm)}. "
                    "Check CDN delay/policies; do not treat this run as a privacy pass."
                )
            await asyncio.sleep(min(2, max(0, budget - (time.monotonic() - started))))

    async def check_direct_denial(self, client, routes, label):
        for route in routes:
            response = await client.get(route)
            self.status(
                response, DENIED,
                f"{label}: {route.split('/object/', 1)[-1].split('/', 1)[0]} direct object access; "
                "a 2xx may indicate a browser-readable Storage policy",
            )
            self.assertNotIn(PNG, response.content)

    async def check_app_delivery(self, store, row, image):
        url = store.render_url(row, 0)
        self.assertEqual(url, f"/api/venture-images/{row['id']}/0.png")
        owner = {"Cookie": f"{server.COOKIE}={row['owner_token']}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="https://testserver",
        ) as client:
            for headers in ({}, {"Cookie": f"{server.COOKIE}={secrets.token_urlsafe(32)}"}):
                client.cookies.clear()
                response = await client.get(url, headers=headers)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                self.assertNotIn(image, response.content)
            response = await client.get(url, headers=owner)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, image)
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            response = await client.post(f"/api/ventures/{row['slug']}/release", headers=owner)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["images"], [url])
            client.cookies.clear()
            response = await client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, image)
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            released = await store.get_by_id(row["id"])
            self.assertEqual(released["visibility"], "public")
            self.assertEqual(released["images"], row["images"])