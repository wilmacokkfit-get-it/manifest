"""Regression coverage for generation, venture permissions, and SQLite."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import httpx

# Importing server builds its default store. Point that one-time initialization
# at disposable data so a test run never reads or migrates workspace data.
_IMPORT_DATA = tempfile.TemporaryDirectory(prefix="manifest-test-import-")
_IMPORT_ENV = patch.dict(
    os.environ,
    {
        "STORE": "sqlite",
        "MANIFEST_DATA_DIR": _IMPORT_DATA.name,
        "SUPABASE_URL": "",
        "SUPABASE_SECRET_KEY": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
        "TRUST_VISITOR_HEADER": "0",
    },
    clear=False,
)
_IMPORT_ENV.start()
try:
    import server
finally:
    _IMPORT_ENV.stop()

from app.store import SQLiteStore  # noqa: E402


def private_venture(slug: str = "private-test-venture", owner: str = "owner-token-for-test") -> dict:
    """A private, test-only venture with no marketplace signals or offers."""
    return {
        "id": str(uuid.uuid4()),
        "slug": slug,
        "owner_token": owner,
        "idea_raw": "A compact tool for organizing a workbench",
        "idea_hash": "test-hash",
        "provenance_code": "TEST",
        "name": "Workbench Organizer",
        "tagline": "Keep small tools in reach.",
        "description": "A test-only private venture.",
        "category": "Workshop",
        "accent": "#aabbcc",
        "tiers": [{"label": "The Lean Build", "title": "Prototype", "description": "A small prototype."}],
        "roadmap": [{"step": 1, "action": "Make a prototype.", "where": "A workshop."}],
        "grounding": {"exists": "", "who_pays": "", "first_hundred": ""},
        "image_prompts": [],
        "images": [],
        "images_status": "failed",
        "visibility": "private",
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "path": {"tier_label": "The Lean Build", "steps": [{"status": "todo"}]},
    }


class ServerRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="manifest-test-")
        self.store = SQLiteStore(self._tempdir.name)
        self._original_store = server.store
        self._original_trust_visitor_header = server.TRUST_VISITOR_HEADER
        server.store = self.store
        server.TRUST_VISITOR_HEADER = False
        self._env_patch = patch.dict(
            os.environ,
            {"LLM_PROVIDER": "mock", "IMAGE_PROVIDER": "none"},
            clear=False,
        )
        self._env_patch.start()

    async def asyncTearDown(self) -> None:
        self._env_patch.stop()
        server.store = self._original_store
        server.TRUST_VISITOR_HEADER = self._original_trust_visitor_header
        self.store._db.close()
        self._tempdir.cleanup()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://testserver",
        )

    def database_snapshot(self) -> list[str]:
        """Capture every row in this test's disposable SQLite database."""
        return list(self.store._db.iterdump())

    async def test_private_venture_denies_anonymous_and_allows_owner(self) -> None:
        row = await self.store.create_venture(private_venture())

        async with self.client() as client:
            anonymous = await client.get(f"/api/ventures/{row['slug']}")
            owner = await client.get(
                f"/api/ventures/{row['slug']}",
                headers={"Cookie": f"{server.COOKIE}=owner-token-for-test"},
            )

        self.assertEqual(anonymous.status_code, 404)
        self.assertEqual(owner.status_code, 200)
        self.assertTrue(owner.json()["is_owner"])
        self.assertEqual(owner.json()["idea"], row["idea_raw"])

    async def test_private_venture_rejects_unauthorized_plan_and_release_changes(self) -> None:
        row = await self.store.create_venture(
            {
                **private_venture(slug="private-plan-guard"),
                "path": {
                    "tier": 0,
                    "tier_label": "The Lean Build",
                    "status": "ready",
                    "steps": [{"n": 1, "title": "Make a prototype.", "status": "todo", "note": "", "done_at": None}],
                },
            }
        )
        before = await self.store.get_by_slug(row["slug"])
        self.assertEqual(before["visibility"], "private")

        attempts = [
            ("POST", f"/api/ventures/{row['slug']}/path", {"json": {"tier": 1}}),
            ("PATCH", f"/api/ventures/{row['slug']}/path/steps/1", {"json": {"status": "done", "note": "hacked"}}),
            ("DELETE", f"/api/ventures/{row['slug']}/path", {}),
            ("POST", f"/api/ventures/{row['slug']}/release", {}),
        ]
        callers = {
            "anonymous": {},
            "non-owner": {"Cookie": f"{server.COOKIE}=someone-elses-token"},
        }

        with patch.object(server.llm, "stream_path_text") as stream_path:
            async with self.client() as client:
                for caller, headers in callers.items():
                    for method, url, kwargs in attempts:
                        response = await client.request(method, url, headers=headers, **kwargs)
                        with self.subTest(caller=caller, method=method, url=url):
                            # Private ventures must not leak their existence to outsiders.
                            self.assertEqual(response.status_code, 404)
                            self.assertNotIn("event:", response.text)
                            after = await self.store.get_by_slug(row["slug"])
                            self.assertEqual(after, before)

        stream_path.assert_not_called()
        self.assertEqual(await self.store.list_public(), [])

        # The owner still has full control over their own plan.
        async with self.client() as client:
            owner = await client.patch(
                f"/api/ventures/{row['slug']}/path/steps/1",
                json={"status": "doing"},
                headers={"Cookie": f"{server.COOKIE}=owner-token-for-test"},
            )
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.json()["path"]["steps"][0]["status"], "doing")

    async def test_private_venture_rejects_unauthorized_needs_and_offers(self) -> None:
        row = await self.store.create_venture(private_venture(slug="private-needs-guard"))
        before = await self.store.get_by_slug(row["slug"])
        need_id = str(uuid.uuid4())
        base_url = f"/api/ventures/{row['slug']}/needs"
        attempts = [
            ("POST", base_url, {"step_n": 1, "title": "Help build a prototype", "detail": "Test only."}),
            ("PATCH", f"{base_url}/{need_id}", {"status": "filled"}),
            ("POST", f"{base_url}/{need_id}/offers",
             {"email": "helper@example.test", "name": "Test helper", "message": "I can help."}),
        ]
        callers = {
            "anonymous": {},
            "non-owner": {"Cookie": f"{server.COOKIE}=someone-elses-token"},
        }

        # A missing ask must not mask a broken privacy guard: reject callers
        # before even looking up the ask, and never attempt any writes.
        with (
            patch.object(self.store, "get_need", wraps=self.store.get_need) as get_need,
            patch.object(self.store, "create_need", wraps=self.store.create_need) as create_need,
            patch.object(self.store, "set_need_status", wraps=self.store.set_need_status) as set_status,
            patch.object(self.store, "add_offer", wraps=self.store.add_offer) as add_offer,
        ):
            for caller, headers in callers.items():
                async with self.client() as client:
                    for method, url, body in attempts:
                        # Don't let the anonymous client's newly issued cookie
                        # turn subsequent requests into authenticated requests.
                        client.cookies.clear()
                        response = await client.request(method, url, headers=headers, json=body)
                        with self.subTest(caller=caller, method=method, url=url):
                            self.assertEqual(response.status_code, 404)
                            self.assertNotIn(row["slug"], response.text)
                            self.assertNotIn(row["name"], response.text)
                            self.assertEqual(await self.store.get_by_slug(row["slug"]), before)
                            self.assertEqual(await self.store.list_needs(row["id"]), [])
                            self.assertEqual(await self.store.list_offers(need_id), [])
                            self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM needs").fetchone()[0], 0)
                            self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM offers").fetchone()[0], 0)

            get_need.assert_not_awaited()
            create_need.assert_not_awaited()
            set_status.assert_not_awaited()
            add_offer.assert_not_awaited()

        self.assertEqual(await self.store.list_public(), [])

    async def test_public_venture_rejects_outsider_need_changes_without_writes(self) -> None:
        row = await self.store.create_venture(
            {**private_venture(slug="public-needs-guard"), "visibility": "public"}
        )
        need = await self.store.create_need(row["id"], 1, "Help build a prototype", "Test only.")
        await self.store.add_offer(need["id"], "existing@example.test", "Existing helper", "Keep this.")
        before = self.database_snapshot()
        base_url = f"/api/ventures/{row['slug']}/needs"
        attempts = [
            ("POST", base_url, {"step_n": 1, "title": "Unauthorized ask", "detail": "Do not save."}),
            *[("PATCH", f"{base_url}/{need['id']}", {"status": status})
              for status in ("open", "filled", "closed")],
        ]
        callers = {
            "anonymous": {},
            "non-creator": {"Cookie": f"{server.COOKIE}=someone-elses-token"},
        }

        with (
            patch.object(self.store, "create_need", wraps=self.store.create_need) as create_need,
            patch.object(self.store, "set_need_status", wraps=self.store.set_need_status) as set_status,
        ):
            async with self.client() as client:
                for caller, headers in callers.items():
                    for method, url, body in attempts:
                        client.cookies.clear()
                        with self.subTest(caller=caller, method=method, body=body):
                            response = await client.request(method, url, headers=headers, json=body)
                            self.assertEqual(response.status_code, 403)
                            self.assertEqual(self.database_snapshot(), before)
            create_need.assert_not_awaited()
            set_status.assert_not_awaited()

    async def test_public_venture_creator_can_create_and_update_needs(self) -> None:
        row = await self.store.create_venture(
            {**private_venture(slug="public-creator-needs"), "visibility": "public"}
        )
        base_url = f"/api/ventures/{row['slug']}/needs"
        headers = {"Cookie": f"{server.COOKIE}={row['owner_token']}"}
        async with self.client() as client:
            response = await client.post(
                base_url,
                headers=headers,
                json={"step_n": 1, "title": "Help build a prototype", "detail": "Test only."},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["is_owner"])
            needs = await self.store.list_needs(row["id"])
            self.assertEqual(len(needs), 1)
            need = needs[0]
            self.assertEqual(need["venture_id"], row["id"])
            self.assertEqual(need["step_n"], 1)
            self.assertEqual(need["title"], "Help build a prototype")
            self.assertEqual(need["detail"], "Test only.")
            self.assertEqual(need["status"], "open")
            self.assertEqual(response.json()["needs"][0]["id"], need["id"])
            await self.store.add_offer(need["id"], "helper@example.test", "Helper", "Keep this offer.")
            offers_before = await self.store.list_offers(need["id"])

            for status in ("filled", "closed", "open"):
                with self.subTest(status=status):
                    updated = await client.patch(
                        f"{base_url}/{need['id']}", headers=headers, json={"status": status},
                    )
                    self.assertEqual(updated.status_code, 200)
                    self.assertTrue(updated.json()["is_owner"])
                    self.assertEqual(updated.json()["needs"][0]["status"], status)
                    self.assertEqual(
                        await self.store.get_need(need["id"]),
                        {**need, "status": status, "offer_count": 1},
                    )
                    self.assertEqual(await self.store.list_offers(need["id"]), offers_before)

        self.assertEqual(await self.store.get_by_slug(row["slug"]), row)

    async def test_public_creator_cannot_update_another_ventures_need(self) -> None:
        row = await self.store.create_venture(
            {**private_venture(slug="public-local-ask"), "visibility": "public"}
        )
        other = await self.store.create_venture(
            {**private_venture(slug="public-other-ask", owner="different-creator-token"), "visibility": "public"}
        )
        local_need = await self.store.create_need(row["id"], 1, "Local ask", "Keep this.")
        foreign_need = await self.store.create_need(other["id"], 1, "Other ask", "Keep this too.")
        await self.store.add_offer(foreign_need["id"], "helper@example.test", "Helper", "Keep this offer.")
        before = self.database_snapshot()
        headers = {"Cookie": f"{server.COOKIE}={row['owner_token']}"}
        attempts = [
            (foreign_need["id"], "closed", 404),
            (str(uuid.uuid4()), "filled", 404),
            (local_need["id"], "invalid", 400),
        ]
        with patch.object(self.store, "set_need_status", wraps=self.store.set_need_status) as set_status:
            async with self.client() as client:
                for need_id, status, expected in attempts:
                    with self.subTest(need_id=need_id, status=status):
                        response = await client.patch(
                            f"/api/ventures/{row['slug']}/needs/{need_id}",
                            headers=headers, json={"status": status},
                        )
                        self.assertEqual(response.status_code, expected)
                        self.assertEqual(self.database_snapshot(), before)
            set_status.assert_not_awaited()

    async def test_public_visitors_offer_only_on_open_asks_in_requested_venture(self) -> None:
        row = await self.store.create_venture(
            {**private_venture(slug="public-offer-guard"), "visibility": "public"}
        )
        other = await self.store.create_venture(
            {**private_venture(slug="public-foreign-offer", owner="different-creator-token"), "visibility": "public"}
        )
        open_need = await self.store.create_need(row["id"], 1, "Open ask", "Accept help.")
        closed_need = await self.store.create_need(row["id"], 1, "Closed ask", "No more help.")
        filled_need = await self.store.create_need(row["id"], 1, "Filled ask", "Already helped.")
        foreign_need = await self.store.create_need(other["id"], 1, "Foreign open ask", "Wrong venture.")
        await self.store.set_need_status(closed_need["id"], "closed")
        await self.store.set_need_status(filled_need["id"], "filled")
        callers = {
            "anonymous": {},
            "non-creator": {"Cookie": f"{server.COOKIE}=someone-elses-token"},
        }
        base_url = f"/api/ventures/{row['slug']}/needs"
        async with self.client() as client:
            for offer_count, (caller, headers) in enumerate(callers.items(), start=1):
                body = {"email": f"{caller}@example.test", "name": "Test helper", "message": "I can help."}
                client.cookies.clear()
                response = await client.post(
                    f"{base_url}/{open_need['id']}/offers", headers=headers, json=body,
                )
                with self.subTest(caller=caller, ask="open"):
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json()["added"])
                    self.assertFalse(response.json()["is_owner"])
                    visible_need = next(n for n in response.json()["needs"] if n["id"] == open_need["id"])
                    self.assertNotIn("offers", visible_need)
                    offers = await self.store.list_offers(open_need["id"])
                    self.assertEqual(len(offers), offer_count)
                    self.assertTrue(all(o["need_id"] == open_need["id"] for o in offers))
                    self.assertEqual(
                        [{key: o[key] for key in body} for o in offers if o["email"] == body["email"]],
                        [body],
                    )
                    self.assertEqual((await self.store.get_need(open_need["id"]))["offer_count"], offer_count)
                    self.assertEqual(visible_need["offer_count"], offer_count)

                before = self.database_snapshot()
                with patch.object(self.store, "add_offer", wraps=self.store.add_offer) as add_offer:
                    for need_id in (closed_need["id"], filled_need["id"], foreign_need["id"], str(uuid.uuid4())):
                        client.cookies.clear()
                        with self.subTest(caller=caller, rejected_need=need_id):
                            rejected = await client.post(
                                f"{base_url}/{need_id}/offers", headers=headers, json=body,
                            )
                            self.assertEqual(rejected.status_code, 404)
                            self.assertEqual(self.database_snapshot(), before)
                    add_offer.assert_not_awaited()

        self.assertEqual(len(await self.store.list_offers(open_need["id"])), 2)
        self.assertEqual(await self.store.get_by_slug(row["slug"]), row)
        self.assertEqual(await self.store.get_by_slug(other["slug"]), other)

    async def test_explicit_openai_selection_fails_cleanly_without_credentials(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("OPENAI_BASE_URL", None)
            self.assertEqual(server.llm.text_provider(), "openai")

            async with self.client() as client:
                response = await client.post(
                    "/api/manifest",
                    json={"idea": "A compact organizer for small workshop tools"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: meta", response.text)
        self.assertIn("event: error", response.text)
        self.assertNotIn("event: done", response.text)
        self.assertEqual(await self.store.count_generations_since("1970-01-01T00:00:00Z"), 0)

    async def test_streaming_generation_completes_and_persists_private_venture(self) -> None:
        generated = {
            "name": "Quiet Clamp",
            "tagline": "A steady grip for small projects.",
            "description": "A deterministic test fixture, not a marketplace listing.",
            "category": "Workshop",
            "accent": "#334455",
            "tiers": [{"title": "Bent by hand", "description": "A simple prototype."}],
            "roadmap": [{"action": "Build one.", "where": "A test workshop."}],
            "grounding": {"exists": "Existing clamps vary.", "who_pays": "Test only.", "first_hundred": "Not applicable."},
            "image_prompts": [],
        }
        encoded = json.dumps(generated)
        chunks = [encoded[:16], encoded[16:55], encoded[55:]]

        async def stub_stream(*args, **kwargs):
            for chunk in chunks:
                yield chunk
                await asyncio.sleep(0)

        with patch.object(server.llm, "stream_venture_text", stub_stream), patch.object(
            server.llm, "image_provider", return_value="none"
        ):
            async with self.client() as client:
                response = await client.post(
                    "/api/manifest",
                    json={"idea": "A quiet clamp for small workshop projects"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: meta", response.text)
        self.assertIn("event: partial", response.text)
        self.assertIn("event: done", response.text)
        self.assertNotIn("event: error", response.text)

        events = {}
        for block in response.text.strip().split("\n\n"):
            lines = block.splitlines()
            if len(lines) >= 2 and lines[0].startswith("event: ") and lines[1].startswith("data: "):
                events[lines[0][7:]] = json.loads(lines[1][6:])
        saved = await self.store.get_by_slug(events["done"]["slug"])
        self.assertIsNotNone(saved)
        self.assertEqual(saved["owner_token"], events["meta"]["owner_token"])
        self.assertEqual(saved["name"], generated["name"])
        self.assertEqual(saved["visibility"], "private")
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["images_status"], "failed")

    async def test_image_generation_exception_sets_failed_status(self) -> None:
        row = await self.store.create_venture(
            {**private_venture(slug="image-failure"), "image_prompts": ["test render"]}
        )

        async def fail_render(*args, **kwargs):
            raise RuntimeError("provider unavailable")

        with patch.object(server.llm, "generate_render", fail_render):
            await server._render_images(row)

        updated = await self.store.get_by_id(row["id"])
        self.assertEqual(updated["images_status"], "failed")
        self.assertEqual(updated["images"], [None])

    async def test_image_generation_success_saves_render_and_marks_ready(self) -> None:
        row = await self.store.create_venture(
            {**private_venture(slug="image-success"), "image_prompts": ["test render"]}
        )

        from PIL import Image

        png = BytesIO()
        Image.new("RGB", (4, 4), color=(90, 120, 150)).save(png, format="PNG")
        png_bytes = png.getvalue()
        calls: list[tuple[str, str]] = []

        async def stub_render(prompt: str, accent: str) -> bytes:
            calls.append((prompt, accent))
            return png_bytes

        with patch.object(server.llm, "generate_render", stub_render):
            await server._render_images(row)

        self.assertEqual(calls, [("test render", "#aabbcc")])

        updated = await self.store.get_by_id(row["id"])
        self.assertEqual(updated["images_status"], "ready")
        self.assertEqual(len(updated["images"]), 1)
        render_url = updated["images"][0]
        self.assertTrue(render_url.startswith("/renders/"))
        self.assertTrue(Path(render_url).name.startswith(f"{row['id']}-0."))
        render_path = self.store.renders_dir / Path(render_url).name
        self.assertTrue(render_path.is_file())
        self.assertGreater(render_path.stat().st_size, 0)
        self.assertEqual(updated["visibility"], "private")

    async def test_sqlite_round_trip_update_and_render_file(self) -> None:
        row = await self.store.create_venture(private_venture(slug="sqlite-round-trip"))

        from PIL import Image

        png = BytesIO()
        Image.new("RGB", (3, 3), color=(20, 40, 60)).save(png, format="PNG")
        render_url = await self.store.save_render(row["id"], 0, png.getvalue())
        render_path = self.store.renders_dir / Path(render_url).name
        self.assertTrue(render_path.is_file())
        self.assertGreater(render_path.stat().st_size, 0)
        changed = await self.store.update_venture(
            row["id"], {"images": [render_url], "images_status": "ready"}
        )
        self.assertEqual(changed["images_status"], "ready")

        reopened = SQLiteStore(self._tempdir.name)
        try:
            persisted = await reopened.get_by_slug("sqlite-round-trip")
            self.assertEqual(persisted["tiers"], row["tiers"])
            self.assertEqual(persisted["roadmap"], row["roadmap"])
            self.assertEqual(persisted["grounding"], row["grounding"])
            self.assertEqual(persisted["path"], row["path"])
            self.assertEqual(persisted["images"], [render_url])
            self.assertEqual(persisted["images_status"], "ready")
            self.assertEqual(await reopened.list_public(), [])
        finally:
            reopened._db.close()


if __name__ == "__main__":
    unittest.main()
