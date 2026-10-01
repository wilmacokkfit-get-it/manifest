"""HTTP regressions for privileged limits without widening creator permissions."""

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from test_refinement import OWNER, SQLiteStore, refined_output, server, venture
from app import owner_auth


class OwnerLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(self.tempdir.name)
        self.store_patch = patch.object(server, "store", self.store)
        self.store_patch.start()
        self.env = patch.dict(os.environ, {
            "IMAGE_PROVIDER": "none", "LLM_PROVIDER": "mock",
            "MANIFEST_OWNER_EMAIL": "", "TRUST_VISITOR_HEADER": "0",
        })
        self.env.start()
        server._hits.clear()

    async def asyncTearDown(self):
        self.env.stop()
        self.store_patch.stop()
        server._hits.clear()
        self.store._db.close()
        self.tempdir.cleanup()

    def client(self, creator=True, cookie=None, extra_headers=None):
        headers = {"Cookie": cookie or f"{server.COOKIE}={OWNER}"} if creator or cookie else {}
        headers.update(extra_headers or {})
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="https://testserver", headers=headers,
        )

    def privilege(self, enabled):
        return patch.object(server, "is_app_owner", AsyncMock(return_value=enabled))

    async def test_owner_generation_bypasses_hour_day_and_global_caps_without_hits(self):
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())

        with (
            self.privilege(True),
            patch.object(server, "GEN_PER_HOUR", 0),
            patch.object(server, "GEN_PER_DAY", 0),
            patch.object(server, "GLOBAL_PER_DAY", 0),
            patch.object(server.llm, "stream_venture_text", output),
            patch.object(self.store, "count_generations_since", AsyncMock()) as count,
        ):
            async with self.client() as client:
                response = await client.post("/api/manifest", json={"idea": "A compact wall rail for tiny workshops"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("event: done", response.text)
        count.assert_not_awaited()
        self.assertFalse(any(server._hits.values()))

    async def test_visitor_generation_keeps_hour_day_and_global_caps(self):
        for hour, day, global_cap in ((0, 8, 150), (3, 0, 150), (3, 8, 0)):
            with (
                self.privilege(False),
                patch.object(server, "GEN_PER_HOUR", hour),
                patch.object(server, "GEN_PER_DAY", day),
                patch.object(server, "GLOBAL_PER_DAY", global_cap),
            ):
                async with self.client() as client:
                    response = await client.post("/api/manifest", json={"idea": "A compact wall rail for tiny workshops"})
                self.assertEqual(response.status_code, 429, response.text)

    async def test_owner_can_repeat_completed_and_skipped_refinements_without_hits(self):
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())

        for prior in ("completed", "skipped"):
            row = await self.store.create_venture(venture(
                slug=f"owner-{prior}", grounding={"_refinement": {"state": prior}},
            ))
            with (
                self.privilege(True),
                patch.object(server, "GEN_PER_HOUR", 0),
                patch.object(server, "GEN_PER_DAY", 0),
                patch.object(server.llm, "stream_refinement_text", output),
            ):
                async with self.client() as client:
                    for _ in range(2):
                        response = await client.post(
                            f"/api/ventures/{row['slug']}/refine",
                            json={"context": "Keep it small enough for renters"},
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertTrue(response.json()["refinement"]["unlimited"])
                        self.assertEqual(response.json()["refinement"]["state"], "completed")
            self.assertFalse(any(server._hits.values()))

    async def test_normal_creator_still_gets_one_refinement(self):
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())

        row = await self.store.create_venture(venture(slug="visitor-one"))
        with self.privilege(False), patch.object(server.llm, "stream_refinement_text", output):
            async with self.client() as client:
                first = await client.post(f"/api/ventures/{row['slug']}/refine", json={"context": "For renters"})
                second = await client.post(f"/api/ventures/{row['slug']}/refine", json={"context": "Another round"})
                fresh = await client.get(f"/api/ventures/{row['slug']}")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertNotIn("unlimited", fresh.json()["refinement"])

    async def test_owner_still_cannot_refine_others_public_or_in_progress_ventures(self):
        rows = [
            venture(slug="someone-else", owner_token="another-creator-123456789"),
            venture(slug="public-result", visibility="public"),
            venture(slug="path-result", path={"steps": []}),
            venture(slug="rendering-result", images_status="pending"),
        ]
        with self.privilege(True):
            async with self.client() as client:
                for index, values in enumerate(rows):
                    row = await self.store.create_venture(values)
                    response = await client.post(f"/api/ventures/{row['slug']}/refine", json={"context": "For renters"})
                    self.assertEqual(response.status_code, 404 if index == 0 else 409, response.text)

    async def test_client_flags_and_creator_cookie_never_grant_owner_exemption(self):
        row = await self.store.create_venture(venture(
            slug="no-spoof", grounding={"_refinement": {"state": "completed"}},
        ))
        async with self.client(extra_headers={"X-App-Owner": "true", "X-Owner-Email": "owner@example.com"}) as client:
            response = await client.post(
                f"/api/ventures/{row['slug']}/refine?is_owner=true",
                json={"context": "Another round", "unlimited": True},
            )
        self.assertEqual(response.status_code, 409)

    async def test_owner_account_creator_identity_survives_browser_change_and_keeps_legacy_access(self):
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())

        claims = {"sid": "session-fixture", "sub": "owner-fixture"}
        legacy = await self.store.create_venture(venture(slug="pre-login-result"))
        with (
            patch.dict(os.environ, {"MANIFEST_OWNER_EMAIL": "owner@example.com", "CLERK_SECRET_KEY": "fixture-only-key"}),
            patch.object(owner_auth, "_decode_session_token", AsyncMock(return_value=claims)),
            patch.object(owner_auth, "_verified_primary_email", AsyncMock(return_value="owner@example.com")),
            patch.object(server.llm, "stream_venture_text", output),
        ):
            async with self.client(cookie=f"{server.COOKIE}={OWNER}; __session=fixture-token") as client:
                response = await client.post("/api/manifest", json={"idea": "A compact wall rail for tiny workshops"})
                old = await client.get(f"/api/ventures/{legacy['slug']}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(old.status_code, 200)
            row = (await self.store.list_public())  # no public records were created
            self.assertEqual(row, [])
            with self.store._lock:
                slug = self.store._db.execute("SELECT slug FROM ventures WHERE slug != ?", [legacy["slug"]]).fetchone()[0]
            async with self.client(cookie=f"{server.COOKIE}=different-browser-123456; __session=fixture-token") as client:
                fresh = await client.get(f"/api/ventures/{slug}")
            self.assertEqual(fresh.status_code, 200, fresh.text)
            self.assertTrue(fresh.json()["is_owner"])
            self.assertTrue(fresh.json()["refinement"]["unlimited"])
        async with self.client() as client:
            signed_out = await client.get(f"/api/ventures/{slug}")
        self.assertEqual(signed_out.status_code, 404)