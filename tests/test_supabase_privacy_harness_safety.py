"""Offline failure-path checks for the opt-in live privacy harness.

Only HTTP is replaced: the real store, lifespan, and harness cleanup run.
Unexpected requests fail rather than falling through to a network transport.
"""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from test_regressions import server
import test_supabase_image_privacy_live as live


class FixtureHTTP:
    """Small disposable-fixture API with unrelated data that must survive."""

    def __init__(self, case, failure, cleanup_failure=None):
        self.case = case
        self.failure = failure
        self.cleanup_failure = cleanup_failure
        self.requests = []
        self.bucket = None
        self.public = True
        self.row = None
        self.objects = {}
        self.user = None
        self.uploads = 0
        self.original_store = server.store
        self.original_trust = server.TRUST_VISITOR_HEADER
        self.unrelated = {
            "bucket": "renders",
            "objects": {"existing/0.png": b"existing render"},
            "venture": {"id": "existing", "owner_token": "another-owner"},
            "user": "another-user",
        }

    def error(self, request, kind):
        if kind == "timeout":
            raise httpx.ReadTimeout("offline injected failure", request=request)
        if kind == "invalid_json":
            return httpx.Response(201, content=b"not-json")
        if kind == "empty":
            return httpx.Response(201, json=[])
        if kind == "missing_id":
            return httpx.Response(201, json={})
        return httpx.Response(409 if kind == "collision" else 500)

    def __call__(self, request):
        self.requests.append(request)
        method, path = request.method, request.url.path
        if method == "DELETE":
            # Restoration must precede cleanup, even when cleanup itself fails.
            self.case.assertIs(server.store, self.original_store)
            self.case.assertIs(server.TRUST_VISITOR_HEADER, self.original_trust)
            if path == "/rest/v1/ventures":
                target = "venture"
                self.case.assertEqual(dict(request.url.params), {
                    "id": f"eq.{self.row['id']}",
                    "slug": f"eq.{self.row['slug']}",
                    "owner_token": f"eq.{self.row['owner_token']}",
                })
            elif path == "/auth/v1/admin/users/disposable-user":
                target = "auth"
            elif path == f"/storage/v1/object/{self.bucket}":
                target = "objects"
                self.case.assertEqual(json.loads(request.content), {
                    "prefixes": [f"{self.fixture_id}/0.png", f"{self.fixture_id}/1.png"],
                })
            elif path == f"/storage/v1/bucket/{self.bucket}":
                target = "bucket"
            else:
                raise AssertionError(f"Unsafe cleanup: {method} {path}")
            if self.cleanup_failure and target == self.cleanup_failure[0]:
                return self.error(request, self.cleanup_failure[1])
            if target == "venture":
                self.row = None
            elif target == "auth":
                self.user = None
            elif target == "objects":
                for prefix in json.loads(request.content)["prefixes"]:
                    self.objects.pop(prefix, None)
            return httpx.Response(204)

        if method == "POST" and path == "/storage/v1/bucket":
            body = json.loads(request.content)
            self.bucket = body["id"]
            self.case.assertEqual(body, {
                "id": self.bucket, "name": self.bucket, "public": True,
            })
            self.case.assertTrue(self.bucket.startswith("manifest-privacy-test-"))
            if self.failure.startswith("bucket_"):
                # Even if a create may have committed, its ownership is unproven.
                self.objects["existing/0.png"] = b"pre-existing collision object"
                return self.error(request, self.failure.removeprefix("bucket_"))
            return httpx.Response(201, json={"id": self.bucket})

        upload_prefix = f"/storage/v1/object/{self.bucket}/"
        if method == "POST" and path.startswith(upload_prefix):
            self.uploads += 1
            key = path.removeprefix(upload_prefix)
            self.fixture_id = key.split("/")[0]
            self.case.assertNotIn("x-upsert", request.headers)
            # Simulate a partial write, including a timeout after acceptance.
            self.objects[key] = request.content
            # An unrelated object in the same bucket must never be removed.
            self.objects.setdefault("unrelated/keep.png", b"keep")
            if self.failure == f"upload_{self.uploads}":
                return self.error(request, "http")
            if self.failure == f"upload_{self.uploads}_timeout":
                return self.error(request, "timeout")
            return httpx.Response(201, json={})

        if path == "/rest/v1/ventures":
            if method == "GET":
                if self.failure == "preflight":
                    return self.error(request, "http")
                if self.failure == "existing_venture":
                    return httpx.Response(200, json=[self.unrelated["venture"]])
                return httpx.Response(200, json=[dict(self.row)] if self.row else [])
            if method == "POST":
                self.row = json.loads(request.content)
                if self.failure.startswith("venture_"):
                    return self.error(request, self.failure.removeprefix("venture_"))
                return httpx.Response(201, json=[dict(self.row)])

        if method == "POST" and path == f"/storage/v1/object/list/{self.bucket}":
            return httpx.Response(200, json=[{"name": "0.png"}, {"name": "1.png"}])

        if path == f"/storage/v1/bucket/{self.bucket}":
            if method == "PUT":
                self.case.assertIsNot(server.store, self.original_store)
                self.case.assertIs(server.TRUST_VISITOR_HEADER, False)
                self.case.assertEqual(json.loads(request.content), {"public": False})
                self.public = False
                return httpx.Response(200, json={})
            if method == "GET":
                return httpx.Response(200, json={"public": self.public})

        if method == "GET" and "/storage/v1/object/" in path:
            is_backend = request.headers.get("apikey") == "sb_secret_offline-only"
            if self.public or is_backend:
                key = "/".join(path.split("/")[-2:])
                return httpx.Response(200, content=self.objects[key])
            return httpx.Response(404)

        if method == "POST" and path == "/auth/v1/admin/users":
            self.user = "disposable-user"
            if self.failure.startswith("auth_"):
                return self.error(request, self.failure.removeprefix("auth_"))
            return httpx.Response(201, json={"id": self.user})
        if method == "POST" and path == "/auth/v1/token":
            self.case.assertEqual(request.url.params["grant_type"], "password")
            self.case.assertIsNot(server.store, self.original_store)
            return self.error(request, "timeout" if self.failure == "signin_timeout" else "http")
        raise AssertionError(f"Unexpected offline request: {method} {path}")


class PrivacyHarnessSafetyTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the live harness without inheriting its class-level opt-in skip or
    # its two live test methods. No store or harness methods are mocked.
    check_backend = live.SupabaseImagePrivacyLiveTests.check_backend
    require = live.SupabaseImagePrivacyLiveTests.require
    status = live.SupabaseImagePrivacyLiveTests.status
    object_snapshot = live.SupabaseImagePrivacyLiveTests.object_snapshot
    cache_headers = staticmethod(live.SupabaseImagePrivacyLiveTests.cache_headers)
    wait_for_public_denial = live.SupabaseImagePrivacyLiveTests.wait_for_public_denial
    check_direct_denial = live.SupabaseImagePrivacyLiveTests.check_direct_denial
    check_app_delivery = live.SupabaseImagePrivacyLiveTests.check_app_delivery

    def setUp(self):
        original_store, original_trust = server.store, server.TRUST_VISITOR_HEADER

        def restore_globals():
            server.store, server.TRUST_VISITOR_HEADER = original_store, original_trust

        self.addCleanup(restore_globals)
        # Ensure the harness really changes this value before restoring it.
        server.TRUST_VISITOR_HEADER = True

    async def run_failure(self, failure, *, expected, cleanup_failure=None):
        fixture = FixtureHTTP(self, failure, cleanup_failure)
        real_client = httpx.AsyncClient
        clients = []

        def offline_client(*args, **kwargs):
            # Every client, including anonymous and browser clients, is offline.
            self.assertNotIn("transport", kwargs)
            client = real_client(*args, transport=httpx.MockTransport(fixture), **kwargs)
            clients.append(client)
            return client

        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://privacy-fixtures.invalid",
            "SUPABASE_SECRET_KEY": "sb_secret_offline-only",
            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_offline-only",
            "SUPABASE_PRIVACY_REQUIRE_BROWSER": "1",
            "SUPABASE_PRIVACY_CACHE_WAIT_SECONDS": "0",
        }), patch.object(httpx, "AsyncClient", side_effect=offline_client):
            try:
                with self.assertRaises(expected) as caught:
                    await self.check_backend("SUPABASE_SECRET_KEY", modern=True)
            finally:
                # Check before test teardown could hide a broken restoration.
                self.assertIs(server.store, fixture.original_store)
                self.assertIs(server.TRUST_VISITOR_HEADER, fixture.original_trust)
                for client in clients:
                    await client.aclose()

        deletes = [r for r in fixture.requests if r.method == "DELETE"]
        self.assertEqual(fixture.unrelated, {
            "bucket": "renders",
            "objects": {"existing/0.png": b"existing render"},
            "venture": {"id": "existing", "owner_token": "another-owner"},
            "user": "another-user",
        })
        self.assertFalse(any("/empty" in r.url.path for r in fixture.requests))
        if failure.startswith("bucket_"):
            self.assertEqual(deletes, [])
            self.assertEqual(len(fixture.requests), 1)
            self.assertTrue(fixture.public)
            self.assertEqual(fixture.objects, {
                "existing/0.png": b"pre-existing collision object",
            })
        else:
            expected_paths = []
            if failure.startswith("venture_") or failure.startswith(("auth_", "signin")):
                expected_paths.append("/rest/v1/ventures")
            if failure.startswith("signin"):
                expected_paths.append("/auth/v1/admin/users/disposable-user")
            expected_paths.extend([
                f"/storage/v1/object/{fixture.bucket}",
                f"/storage/v1/bucket/{fixture.bucket}",
            ])
            self.assertEqual([r.url.path for r in deletes], expected_paths)
            self.assertEqual(fixture.objects.get("unrelated/keep.png"), b"keep")
            if not cleanup_failure or cleanup_failure[0] != "objects":
                self.assertEqual(fixture.objects, {"unrelated/keep.png": b"keep"})
            if expected_paths[0] == "/rest/v1/ventures" and (
                not cleanup_failure or cleanup_failure[0] != "venture"
            ):
                self.assertIsNone(fixture.row)
        if cleanup_failure:
            self.assertIn("Cleanup incomplete", str(caught.exception))
        return fixture

    async def test_bucket_collision_never_touches_existing_bucket(self):
        await self.run_failure("bucket_collision", expected=AssertionError)

    async def test_failed_or_ambiguous_bucket_create_never_claims_ownership(self):
        for failure, expected in (
            ("bucket_http", AssertionError),
            ("bucket_timeout", httpx.ReadTimeout),
        ):
            with self.subTest(failure=failure):
                await self.run_failure(failure, expected=expected)

    async def test_partial_uploads_delete_only_exact_fixture_paths(self):
        for failure, expected in (
            ("upload_1", AssertionError), ("upload_2", AssertionError),
            ("upload_1_timeout", httpx.ReadTimeout),
            ("upload_2_timeout", httpx.ReadTimeout),
        ):
            with self.subTest(failure=failure):
                await self.run_failure(failure, expected=expected)

    async def test_venture_preflight_failure_or_collision_never_deletes_row(self):
        for failure, expected in (
            ("preflight", RuntimeError), ("existing_venture", AssertionError),
        ):
            with self.subTest(failure=failure):
                await self.run_failure(failure, expected=expected)

    async def test_failed_and_ambiguous_venture_insert_uses_all_ownership_filters(self):
        for failure, expected in (
            ("venture_http", RuntimeError), ("venture_timeout", httpx.ReadTimeout),
            ("venture_empty", IndexError), ("venture_invalid_json", ValueError),
        ):
            with self.subTest(failure=failure):
                await self.run_failure(failure, expected=expected)

    async def test_failed_and_ambiguous_auth_create_never_deletes_unknown_user(self):
        for failure, expected in (
            ("auth_http", AssertionError), ("auth_timeout", httpx.ReadTimeout),
            ("auth_invalid_json", ValueError), ("auth_missing_id", KeyError),
        ):
            with self.subTest(failure=failure):
                fixture = await self.run_failure(failure, expected=expected)
                self.assertEqual(fixture.user, "disposable-user")

    async def test_signin_failure_deletes_only_the_confirmed_auth_user(self):
        for failure, expected in (
            ("signin_http", AssertionError), ("signin_timeout", httpx.ReadTimeout),
        ):
            with self.subTest(failure=failure):
                fixture = await self.run_failure(failure, expected=expected)
                self.assertIsNone(fixture.user)

    async def test_cleanup_continues_after_any_cleanup_http_or_transport_failure(self):
        for target in ("venture", "auth", "objects", "bucket"):
            for kind in ("http", "timeout"):
                with self.subTest(target=target, kind=kind):
                    await self.run_failure(
                        "signin_http", expected=AssertionError,
                        cleanup_failure=(target, kind),
                    )