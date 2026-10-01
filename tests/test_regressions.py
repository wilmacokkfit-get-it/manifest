"""Regression coverage for generation, private venture access, and SQLite."""

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
        "MANIFEST_DATA_DIR": _IMPORT_DATA.name,
        "SUPABASE_URL": "",
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
        server._hits.clear()
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
        server._hits.clear()
        self.store._db.close()
        self._tempdir.cleanup()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://testserver",
        )

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