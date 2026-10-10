"""Focused tests for the one-time venture refinement flow."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
from starlette.requests import Request

_IMPORT_DATA = tempfile.TemporaryDirectory(prefix="manifest-refinement-import-")
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

from app.store import SQLiteStore, SupabaseStore  # noqa: E402


OWNER = "refinement-owner-token-123"


def venture(slug: str = "refine-test", **overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "slug": slug,
        "owner_token": OWNER,
        "idea_raw": "A sturdy organizer for a crowded workbench",
        "idea_hash": "original-idea-hash",
        "provenance_code": "ORIG42",
        "name": "Bench Nest",
        "tagline": "Small tools stay where hands need them.",
        "description": "A wall-mounted organizer for a compact workshop.",
        "category": "Workshop tools",
        "accent": "#aabbcc",
        "tiers": [{"label": "The Lean Build", "title": "Plywood rail", "description": "Original tier."}],
        "roadmap": [{"step": 1, "action": "Build a rail.", "where": "A local workshop."}],
        "grounding": {
            "exists": "Pegboards are common.",
            "who_pays": "Small-shop woodworkers.",
            "first_hundred": "Sell a few rails locally.",
        },
        "image_prompts": ["original studio render"],
        "images": [],
        "images_status": "failed",
        "visibility": "private",
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "path": None,
    }
    row.update(overrides)
    return row


def refined_output() -> dict:
    return {
        "name": "Bench Nest Plus",
        "tagline": "A modular rail built for tiny workshops.",
        "description": "A modular wall rail keeps the tools a small-shop woodworker reaches for most close at hand. Short add-on trays fit around benches that have no spare floor space.",
        "category": "Workshop tools",
        "accent": "#bb8844",
        "tiers": [{"title": "Modular rail", "description": "A revised product tier."}],
        "roadmap": [{"action": "Measure a crowded bench.", "where": "Ask local woodworkers."}],
        "grounding": {
            "exists": "Pegboards remain the nearest alternative.",
            "who_pays": "Woodworkers with small shops.",
            "first_hundred": "Sell two starter rails.",
        },
        "image_prompts": ["refined render one", "refined render two"],
    }


class RefinementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="manifest-refinement-")
        self.store = SQLiteStore(self.tempdir.name)
        self.original_store = server.store
        server.store = self.store
        self.env = patch.dict(os.environ, {"LLM_PROVIDER": "mock", "IMAGE_PROVIDER": "none"}, clear=False)
        self.env.start()

    async def asyncTearDown(self) -> None:
        self.env.stop()
        server.store = self.original_store
        self.store._db.close()
        self.tempdir.cleanup()

    def client(self, owner: bool = True) -> httpx.AsyncClient:
        headers = {"Cookie": f"{server.COOKIE}={OWNER}"} if owner else {}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://testserver",
            headers=headers,
        )

    async def test_refines_existing_venture_and_preserves_identity_and_provenance(self) -> None:
        row = await self.store.create_venture(venture())
        original_identity = {
            key: row[key] for key in (
                "id", "slug", "idea_raw", "idea_hash", "provenance_code",
                "created_at", "visibility", "owner_token",
            )
        }
        seen = {}

        async def fake_refinement(existing, context):
            seen["existing"] = existing
            seen["context"] = context
            yield json.dumps(refined_output())

        with patch.object(server.llm, "stream_refinement_text", fake_refinement), patch.object(
            server.llm, "image_provider", return_value="none"
        ):
            async with self.client() as client:
                response = await client.post(
                    f"/api/ventures/{row['slug']}/refine",
                    json={"context": "  It must fit in a narrow apartment workshop.  "},
                )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        saved = await self.store.get_by_slug(row["slug"])
        self.assertEqual(seen["context"], "It must fit in a narrow apartment workshop.")
        self.assertEqual(seen["existing"]["idea_raw"], row["idea_raw"])
        self.assertEqual(seen["existing"]["name"], row["name"])
        prompt = server.llm._refinement_user_message(seen["existing"], seen["context"])
        self.assertIn(row["idea_raw"], prompt)
        self.assertIn(row["name"], prompt)
        self.assertIn("It must fit in a narrow apartment workshop.", prompt)
        self.assertEqual({key: saved[key] for key in original_identity}, original_identity)
        self.assertEqual(saved["name"], "Bench Nest Plus")
        self.assertEqual(saved["image_prompts"], refined_output()["image_prompts"])
        self.assertEqual(saved["images"], [])
        self.assertEqual(saved["images_status"], "failed")
        self.assertEqual(saved["grounding"]["_refinement"]["state"], "completed")
        self.assertEqual(
            saved["grounding"]["_refinement"]["context"],
            "It must fit in a narrow apartment workshop.",
        )
        self.assertTrue(saved["grounding"]["_refinement"]["claim_id"])
        self.assertEqual(body["refinement"], {
            "state": "completed", "context": "It must fit in a narrow apartment workshop."
        })
        self.assertNotIn("_refinement", body["grounding"])

    async def test_owner_privacy_invalid_context_and_nonowner_metadata(self) -> None:
        row = await self.store.create_venture(venture(slug="owner-guard"))
        called = False

        async def unused(*args, **kwargs):
            nonlocal called
            called = True
            yield "{}"

        with patch.object(server.llm, "stream_refinement_text", unused):
            async with self.client(owner=False) as client:
                private = await client.post(f"/api/ventures/{row['slug']}/refine", json={"context": "More space"})
            async with self.client() as client:
                blank = await client.post(f"/api/ventures/{row['slug']}/refine", json={"context": "   "})
                too_long = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "x" * 1001}
                )
        self.assertEqual(private.status_code, 404)
        self.assertEqual(blank.status_code, 400)
        self.assertEqual(too_long.status_code, 400)
        self.assertFalse(called)

        await self.store.update_venture(row["id"], {"visibility": "public"})
        async with self.client(owner=False) as client:
            public = await client.get(f"/api/ventures/{row['slug']}")
        self.assertEqual(public.status_code, 200)
        self.assertIsNone(public.json()["refinement"])
        self.assertNotIn("_refinement", public.json()["grounding"])

    async def test_waiting_then_available_and_skip_persists_during_image_generation(self) -> None:
        row = await self.store.create_venture(
            venture(slug="skip-pending", images_status="pending", image_prompts=["initial render"])
        )
        async with self.client() as client:
            waiting = await client.get(f"/api/ventures/{row['slug']}")
            skipped = await client.post(f"/api/ventures/{row['slug']}/refinement/skip")
            again = await client.get(f"/api/ventures/{row['slug']}")
            repeated = await client.post(f"/api/ventures/{row['slug']}/refinement/skip")
        self.assertEqual(waiting.json()["refinement"], {"state": "waiting"})
        self.assertEqual(skipped.json()["refinement"], {"state": "skipped"})
        self.assertEqual(again.json()["refinement"], {"state": "skipped"})
        self.assertEqual(repeated.json()["refinement"], {"state": "skipped"})
        stored = await self.store.get_by_id(row["id"])
        self.assertEqual(stored["status"], "ready")

        available_row = await self.store.create_venture(venture(slug="available"))
        async with self.client() as client:
            available = await client.get(f"/api/ventures/{available_row['slug']}")
        self.assertEqual(available.json()["refinement"], {"state": "available"})

    async def test_duplicate_concurrent_refinement_is_rejected(self) -> None:
        row = await self.store.create_venture(venture(slug="single-claim"))
        started = asyncio.Event()
        finish = asyncio.Event()

        async def blocked_refinement(*args):
            started.set()
            await finish.wait()
            yield json.dumps(refined_output())

        with (
            patch.object(server.llm, "stream_refinement_text", blocked_refinement),
            patch.object(server.llm, "image_provider", return_value="none"),
            patch.object(server.llm, "stream_path_text") as path_provider,
        ):
            async with self.client() as client:
                first = asyncio.create_task(client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Make it modular"}
                ))
                await started.wait()
                pending = await client.get(f"/api/ventures/{row['slug']}")
                duplicate = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Try another direction"}
                )
                still_pending = await client.get(f"/api/ventures/{row['slug']}")
                path = await client.post(f"/api/ventures/{row['slug']}/path", json={"tier": 0})
                release = await client.post(f"/api/ventures/{row['slug']}/release")
                finish.set()
                response = await first
        self.assertEqual(pending.json()["refinement"]["state"], "pending")
        self.assertEqual(still_pending.json()["refinement"]["state"], "pending")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(path.status_code, 409)
        self.assertEqual(release.status_code, 409)
        path_provider.assert_not_called()
        self.assertEqual(response.status_code, 200)

    async def test_refined_prompts_regenerate_same_ventures_images(self) -> None:
        row = await self.store.create_venture(venture(slug="new-renders"))
        calls = []

        async def fake_refinement(*args):
            yield json.dumps(refined_output())

        async def fake_render(prompt, accent):
            calls.append((prompt, accent))
            return b"render-bytes"

        with patch.object(server.llm, "stream_refinement_text", fake_refinement), patch.object(
            server.llm, "image_provider", return_value="mock"
        ), patch.object(server.llm, "generate_render", fake_render):
            async with self.client() as client:
                response = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Add removable trays"}
                )
            for _ in range(100):
                saved = await self.store.get_by_slug(row["slug"])
                if saved["images_status"] != "pending":
                    break
                await asyncio.sleep(0.01)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call[0] for call in calls], refined_output()["image_prompts"])
        self.assertTrue(all(call[1] == refined_output()["accent"] for call in calls))
        self.assertTrue(all(saved["images"]))

    async def test_provider_failure_restores_original_and_allows_retry(self) -> None:
        row = await self.store.create_venture(venture(slug="retry-refinement"))
        original = await self.store.get_by_id(row["id"])

        async def fail(*args):
            raise RuntimeError("provider offline")
            yield  # pragma: no cover

        async def succeed(*args):
            yield json.dumps(refined_output())

        with patch.object(server.llm, "stream_refinement_text", fail):
            async with self.client() as client:
                failed = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Add removable trays"}
                )
        after_failure = await self.store.get_by_id(row["id"])
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(after_failure["status"], "ready")
        self.assertEqual(after_failure["grounding"], original["grounding"])
        self.assertEqual(after_failure["name"], original["name"])

        async def truncated(*args):
            yield json.dumps(refined_output())[:-12]

        with patch.object(server.llm, "stream_refinement_text", truncated):
            async with self.client() as client:
                partial = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Add removable trays"}
                )
        after_partial = await self.store.get_by_id(row["id"])
        self.assertEqual(partial.status_code, 502)
        self.assertEqual(after_partial["status"], "ready")
        self.assertEqual(after_partial["grounding"], original["grounding"])
        self.assertEqual(after_partial["name"], original["name"])

        with patch.object(server.llm, "stream_refinement_text", succeed), patch.object(
            server.llm, "image_provider", return_value="none"
        ):
            async with self.client() as client:
                retried = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Add removable trays"}
                )
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["refinement"]["state"], "completed")

    async def test_refinement_timeout_restores_original_and_releases_claim(self) -> None:
        row = await self.store.create_venture(venture(slug="timed-out-refinement"))
        async def stalled(*args, **kwargs):
            yield '{"name":"Not committed",'
            await asyncio.Event().wait()
        with patch.object(server, "TEXT_TIMEOUT_SECONDS", .01), patch.object(
            server.llm, "stream_refinement_text", stalled
        ):
            async with self.client() as client:
                response = await client.post(f"/api/ventures/{row['slug']}/refine", json={"context": "Make it smaller"})
        self.assertEqual(response.status_code, 504)
        self.assertIn("two-minute limit", response.text)
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(saved["name"], row["name"])
        self.assertEqual(saved["grounding"], row["grounding"])
        self.assertEqual(saved["status"], "ready")

    async def test_path_timeout_releases_claim_and_leaves_venture_unchanged(self) -> None:
        row = await self.store.create_venture(venture(slug="timed-out-path"))
        async def stalled(*args, **kwargs):
            yield '{"first_move":"Preview",'
            await asyncio.Event().wait()
        with patch.object(server, "TEXT_TIMEOUT_SECONDS", .01), patch.object(
            server.llm, "stream_path_text", stalled
        ):
            async with self.client() as client:
                response = await client.post(f"/api/ventures/{row['slug']}/path", json={"tier": 0})
        self.assertIn("two-minute limit", response.text)
        self.assertNotIn("event: done", response.text)
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertIsNone(saved["path"])
        self.assertNotIn("path_claim_id", saved["grounding"].get("_refinement", {}))

    async def test_cancelled_claim_acquisition_cleans_up_after_delayed_response(self) -> None:
        row = await self.store.create_venture(venture(slug="cancel-during-claim"))
        original_claim = self.store.claim_refinement
        claim_committed = asyncio.Event()
        allow_response = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_finished = asyncio.Event()
        original_clear = self.store.clear_refinement_claim

        async def delayed_claim(*args, **kwargs):
            claimed = await original_claim(*args, **kwargs)
            claim_committed.set()
            await allow_response.wait()
            return claimed

        async def delayed_clear(*args, **kwargs):
            cleanup_started.set()
            await asyncio.sleep(0.01)
            result = await original_clear(*args, **kwargs)
            cleanup_finished.set()
            return result

        with patch.object(self.store, "claim_refinement", delayed_claim), patch.object(
            self.store, "clear_refinement_claim", delayed_clear
        ), patch.object(server.llm, "stream_refinement_text") as provider:
            request = Request({
                "type": "http", "method": "POST", "path": f"/api/ventures/{row['slug']}/refine",
                "headers": [], "query_string": b"", "scheme": "http", "http_version": "1.1",
                "server": ("testserver", 80), "client": ("127.0.0.1", 1234), "state": {},
            })
            request.state.owner = OWNER
            call = asyncio.create_task(server.refine_venture(
                row["slug"], server.RefinementBody(context="Make the rail modular"), request
            ))
            await claim_committed.wait()
            call.cancel()
            allow_response.set()
            try:
                await call
            except asyncio.CancelledError:
                pass

        provider.assert_not_called()
        self.assertTrue(cleanup_started.is_set())
        self.assertTrue(cleanup_finished.is_set())
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["grounding"], row["grounding"])

    async def test_cancellation_during_provider_uses_async_token_scoped_cleanup(self) -> None:
        row = await self.store.create_venture(venture(slug="cancel-during-provider"))
        provider_started = asyncio.Event()
        cleanup_finished = asyncio.Event()
        original_clear = self.store.clear_refinement_claim

        async def blocked_provider(*args):
            provider_started.set()
            await asyncio.Event().wait()
            yield "{}"

        async def delayed_clear(*args, **kwargs):
            await asyncio.sleep(0.01)
            result = await original_clear(*args, **kwargs)
            cleanup_finished.set()
            return result

        request = Request({
            "type": "http", "method": "POST", "path": f"/api/ventures/{row['slug']}/refine",
            "headers": [], "query_string": b"", "scheme": "http", "http_version": "1.1",
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234), "state": {},
        })
        request.state.owner = OWNER
        with patch.object(server.llm, "stream_refinement_text", blocked_provider), patch.object(
            self.store, "clear_refinement_claim", delayed_clear
        ):
            call = asyncio.create_task(server.refine_venture(
                row["slug"], server.RefinementBody(context="Make it modular"), request
            ))
            await provider_started.wait()
            call.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await call
        self.assertTrue(cleanup_finished.is_set())
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["grounding"], row["grounding"])
        self.assertEqual(saved["name"], row["name"])

    async def test_cancel_after_refinement_commit_still_schedules_render_once(self) -> None:
        row = await self.store.create_venture(venture(slug="cancel-after-commit"))
        original_finish = self.store.finish_refinement
        committed = asyncio.Event()
        allow_response = asyncio.Event()
        worker_called = asyncio.Event()
        worker_calls = []

        async def delayed_response(venture_id, fields, claim_id=None):
            saved = await original_finish(venture_id, fields, claim_id)
            committed.set()
            await allow_response.wait()
            return saved

        async def render_worker(saved):
            worker_calls.append(saved["id"])
            worker_called.set()

        request = Request({
            "type": "http", "method": "POST", "path": f"/api/ventures/{row['slug']}/refine",
            "headers": [], "query_string": b"", "scheme": "http", "http_version": "1.1",
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234), "state": {},
        })
        request.state.owner = OWNER
        with patch.object(self.store, "finish_refinement", delayed_response), patch.object(
            server, "_render_images", render_worker
        ), patch.object(server.llm, "image_provider", return_value="mock"):
            call = asyncio.create_task(server.refine_venture(
                row["slug"], server.RefinementBody(context="Add modular trays"), request
            ))
            await committed.wait()
            call.cancel()
            allow_response.set()
            with self.assertRaises(asyncio.CancelledError):
                await call
            await worker_called.wait()
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(worker_calls, [row["id"]])
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["grounding"]["_refinement"]["state"], "completed")
        self.assertEqual(saved["images_status"], "pending")

    async def test_lost_finish_response_reconciles_and_schedules_render_once(self) -> None:
        row = await self.store.create_venture(venture(slug="lost-finish-response"))
        original_finish = self.store.finish_refinement
        worker_called = asyncio.Event()
        worker_calls = []

        async def lost_response(venture_id, fields, claim_id=None):
            await original_finish(venture_id, fields, claim_id)
            raise httpx.ReadError("response disappeared after the database commit")

        async def render_worker(saved):
            worker_calls.append(saved["id"])
            worker_called.set()

        with patch.object(self.store, "finish_refinement", lost_response), patch.object(
            server, "_render_images", render_worker
        ), patch.object(server.llm, "image_provider", return_value="mock"):
            async with self.client() as client:
                response = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Add modular trays"}
                )
            await worker_called.wait()
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(worker_calls, [row["id"]])
        self.assertEqual(saved["grounding"]["_refinement"]["state"], "completed")
        self.assertTrue(saved["grounding"]["_refinement"]["claim_id"])
        self.assertEqual(saved["images_status"], "pending")

    async def test_openai_terminal_incomplete_event_is_a_provider_failure(self) -> None:
        row = venture()
        valid = json.dumps(refined_output())
        for terminal_type in ("response.failed", "response.incomplete"):
            class FakeResponses:
                async def create(self, **kwargs):
                    async def events():
                        yield types.SimpleNamespace(type="response.output_text.delta", delta=valid)
                        yield types.SimpleNamespace(
                            type=terminal_type,
                            response=types.SimpleNamespace(
                                error={"message": "provider failed"},
                                incomplete_details={"reason": "max_output_tokens"},
                            ),
                        )
                    return events()

            fake_client = types.SimpleNamespace(responses=FakeResponses())
            fake_module = types.SimpleNamespace(AsyncOpenAI=lambda: fake_client)
            with patch.dict(
                os.environ, {"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-4.1-mini"}, clear=False
            ), patch.dict(sys.modules, {"openai": fake_module}):
                with self.subTest(terminal_type=terminal_type), self.assertRaisesRegex(
                    RuntimeError, terminal_type
                ):
                    async for _ in server.llm.stream_refinement_text(row, "Use modular trays"):
                        pass

    async def test_complete_but_hollow_json_does_not_replace_existing_content(self) -> None:
        row = await self.store.create_venture(venture(slug="hollow-refinement"))
        original = await self.store.get_by_id(row["id"])
        hollow = {**refined_output(), "roadmap": [], "grounding": {}}

        async def hollow_provider(*args):
            yield json.dumps(hollow)

        with patch.object(server.llm, "stream_refinement_text", hollow_provider):
            async with self.client() as client:
                response = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Add modular trays"}
                )
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(response.status_code, 502)
        self.assertEqual(saved["name"], original["name"])
        self.assertEqual(saved["grounding"], original["grounding"])
        self.assertEqual(saved["status"], "ready")

    async def test_path_and_release_implicitly_skip_refinement(self) -> None:
        path_venture = await self.store.create_venture(venture(slug="path-implies-skip"))
        release_venture = await self.store.create_venture(venture(slug="release-implies-skip"))
        valid_path = {
            "first_move": "Make one prototype.",
            "steps": [{"title": "Build one", "why": "Test it", "how": ["Measure"], "cost": "Free",
                       "time": "One day", "done_when": "It fits"}],
        }

        async def path_stream(*args):
            yield json.dumps(valid_path)

        with patch.object(server.llm, "stream_path_text", path_stream):
            async with self.client() as client:
                path_response = await client.post(
                    f"/api/ventures/{path_venture['slug']}/path", json={"tier": 0}
                )
                released = await client.post(f"/api/ventures/{release_venture['slug']}/release")
        events = [block for block in path_response.text.split("\n\n") if block.startswith("event: done")]
        self.assertTrue(events)
        self.assertEqual(released.status_code, 200)
        self.assertEqual(
            (await self.store.get_by_id(path_venture["id"]))["grounding"]["_refinement"]["state"],
            "skipped",
        )
        self.assertEqual(
            (await self.store.get_by_id(release_venture["id"]))["grounding"]["_refinement"]["state"],
            "skipped",
        )
        reset_path = await self.store.update_venture(path_venture["id"], {"path": None})
        self.assertEqual(reset_path["grounding"]["_refinement"]["state"], "skipped")

    async def test_resetting_a_preexisting_path_consumes_refinement(self) -> None:
        row = await self.store.create_venture(
            venture(
                slug="legacy-path",
                path={"tier": 0, "steps": [{"n": 1, "status": "todo"}]},
            )
        )
        async with self.client() as client:
            response = await client.delete(f"/api/ventures/{row['slug']}/path")
            refreshed = await client.get(f"/api/ventures/{row['slug']}")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone((await self.store.get_by_id(row["id"]))["path"])
        self.assertEqual(refreshed.json()["refinement"], {"state": "skipped"})

    async def test_supabase_refinement_claim_uses_conditional_postgrest_filters(self) -> None:
        row = venture(slug="supabase-claim")
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=[row])
            payload = json.loads(request.content)
            claimed = {**row, **payload}
            return httpx.Response(200, json=[claimed])

        supabase = SupabaseStore("https://example.supabase.co", "test-key")
        await supabase._client.aclose()
        supabase._client = httpx.AsyncClient(
            base_url=supabase.url,
            headers={"apikey": "test-key", "Authorization": "Bearer test-key", "Content-Type": "application/json"},
            transport=httpx.MockTransport(handler),
        )
        try:
            claimed = await supabase.claim_refinement(row["id"], "new context", claim_id="claim-test")
            request = requests[-1]
            params = request.url.params
            self.assertEqual(request.method, "PATCH")
            self.assertEqual(params["status"], "eq.ready")
            self.assertEqual(params["visibility"], "eq.private")
            self.assertEqual(params["path"], "is.null")
            self.assertEqual(params["grounding->_refinement"], "is.null")
            self.assertEqual(params["images_status"], "neq.pending")
            self.assertEqual(json.loads(request.content)["grounding"]["_refinement"]["claim_id"], "claim-test")
            self.assertEqual(claimed["status"], "refining")
            self.assertEqual(claimed["grounding"]["_refinement"]["state"], "pending")
            await supabase.finish_refinement(row["id"], {"status": "ready"}, claim_id="claim-test")
            finish_params = requests[-1].url.params
            self.assertEqual(
                finish_params["grounding->_refinement->>claim_id"], "eq.claim-test"
            )
            skipped = await supabase.claim_refinement(row["id"], skipped=True, require_images_finished=False)
            skip_request = requests[-1]
            self.assertNotIn("images_status", skip_request.url.params)
            self.assertEqual(json.loads(skip_request.content)["status"], "ready")
            self.assertEqual(skipped["grounding"]["_refinement"]["state"], "skipped")
        finally:
            await supabase._client.aclose()

    async def test_path_provider_and_tier_label_use_atomic_claim_snapshot(self) -> None:
        row = await self.store.create_venture(venture(slug="fresh-path-snapshot"))
        original_claim = self.store.claim_path
        seen = {}
        changed_tiers = [
            {"label": "Freshly Updated Tier", "title": "Updated", "description": "Use new tier content."}
        ]

        async def changed_snapshot(venture_id, claim_id=None):
            await self.store.update_venture(venture_id, {"tiers": changed_tiers})
            return await original_claim(venture_id, claim_id)

        async def path_stream(generated_venture, tier):
            seen["venture"] = generated_venture
            seen["tier"] = tier
            yield json.dumps({
                "first_move": "Measure the bench.",
                "steps": [{"title": "Measure bench", "why": "Start with fit.",
                           "how": ["Measure the space"], "cost": "Free",
                           "time": "Ten minutes", "done_when": "The width is recorded"}],
            })

        with patch.object(self.store, "claim_path", changed_snapshot), patch.object(
            server.llm, "stream_path_text", path_stream
        ):
            async with self.client() as client:
                response = await client.post(
                    f"/api/ventures/{row['slug']}/path", json={"tier": 0}
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["venture"]["tiers"], changed_tiers)
        self.assertEqual(seen["tier"], 0)
        done = next(
            json.loads(block.split("data: ", 1)[1])
            for block in response.text.split("\n\n")
            if block.startswith("event: done")
        )
        self.assertEqual(done["path"]["tier_label"], "Freshly Updated Tier")

    async def test_path_cancel_during_provider_waits_for_bounded_cleanup(self) -> None:
        row = await self.store.create_venture(venture(slug="cancel-path-provider"))
        provider_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        allow_cleanup = asyncio.Event()
        original_release = self.store.release_path_claim

        async def blocked_provider(*args):
            provider_started.set()
            await asyncio.Event().wait()
            yield "{}"

        async def delayed_release(venture_id, claim_id=None):
            cleanup_started.set()
            await allow_cleanup.wait()
            return await original_release(venture_id, claim_id)

        request = Request({
            "type": "http", "method": "POST", "path": f"/api/ventures/{row['slug']}/path",
            "headers": [], "query_string": b"", "scheme": "http", "http_version": "1.1",
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234), "state": {},
        })
        request.state.owner = OWNER
        with patch.object(server.llm, "stream_path_text", blocked_provider), patch.object(
            self.store, "release_path_claim", delayed_release
        ):
            response = await server.choose_path(row["slug"], server.PathBody(tier=0), request)

            async def consume():
                async for _ in response.body_iterator:
                    pass

            stream = asyncio.create_task(consume())
            await provider_started.wait()
            stream.cancel()
            await cleanup_started.wait()
            self.assertEqual((await self.store.get_by_id(row["id"]))["status"], "planning")
            allow_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await stream
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertIsNone(saved["path"])
        self.assertNotIn("path_claim_id", saved["grounding"]["_refinement"])

    async def test_path_cancel_during_claim_response_releases_its_claim(self) -> None:
        row = await self.store.create_venture(venture(slug="cancel-path-claim"))
        original_claim = self.store.claim_path
        claim_committed = asyncio.Event()
        allow_response = asyncio.Event()

        async def delayed_claim(venture_id, claim_id=None):
            claimed = await original_claim(venture_id, claim_id)
            claim_committed.set()
            await allow_response.wait()
            return claimed

        request = Request({
            "type": "http", "method": "POST", "path": f"/api/ventures/{row['slug']}/path",
            "headers": [], "query_string": b"", "scheme": "http", "http_version": "1.1",
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234), "state": {},
        })
        request.state.owner = OWNER
        with patch.object(self.store, "claim_path", delayed_claim), patch.object(
            server.llm, "stream_path_text"
        ) as provider:
            call = asyncio.create_task(server.choose_path(
                row["slug"], server.PathBody(tier=0), request
            ))
            await claim_committed.wait()
            call.cancel()
            allow_response.set()
            with self.assertRaises(asyncio.CancelledError):
                await call
        provider.assert_not_called()
        saved = await self.store.get_by_id(row["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertNotIn("path_claim_id", saved["grounding"]["_refinement"])


if __name__ == "__main__":
    unittest.main()