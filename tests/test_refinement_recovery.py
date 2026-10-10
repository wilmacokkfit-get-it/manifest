"""Restart-style regression coverage for interrupted refinement recovery."""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

from test_refinement import OWNER, refined_output, server, venture
from app import store as store_module
from app.store import SQLiteStore, SupabaseStore


def iso_at(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def marker(row: dict) -> dict:
    return row["grounding"]["_refinement"]


class SQLiteRestartRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="manifest-refinement-recovery-")
        self.store = SQLiteStore(self.tempdir.name)
        self.original_store = server.store
        server.store = self.store

    async def asyncTearDown(self) -> None:
        server.store = self.original_store
        self.store._db.close()
        self.tempdir.cleanup()

    def restart_store(self) -> SQLiteStore:
        """Close and reopen SQLite as if the process died between requests."""
        self.store._db.close()
        self.store = SQLiteStore(self.tempdir.name)
        server.store = self.store
        return self.store

    def seed_raw_state(
        self,
        row_id: str,
        *,
        grounding: dict,
        status: str,
        updated_at: str,
        path: dict | None = None,
    ) -> None:
        with self.store._lock:
            self.store._db.execute(
                """UPDATE ventures
                   SET grounding = ?, status = ?, updated_at = ?, path = ?
                   WHERE id = ?""",
                (json.dumps(grounding), status, updated_at,
                 json.dumps(path) if path is not None else None, row_id),
            )
            self.store._db.commit()

    async def test_restart_keeps_live_claim_and_recovers_expired_and_legacy_claims(self) -> None:
        original = {
            "exists": "Original alternatives.",
            "who_pays": "Original buyers.",
            "first_hundred": "Original outreach.",
        }
        active = await self.store.create_venture(venture(slug="recovery-live"))
        live_grounding = {
            **original,
            "_refinement": {
                "state": "pending", "context": "Add a removable tray",
                "claim_id": "live-refinement", "lease_until": iso_at(timedelta(seconds=180)),
            },
        }
        self.seed_raw_state(
            active["id"], grounding=live_grounding, status="refining", updated_at=iso_at()
        )

        expired = await self.store.create_venture(venture(slug="recovery-expired"))
        expired_grounding = {
            **original,
            "_refinement": {
                "state": "pending", "context": "Add a wider rail",
                "claim_id": "expired-refinement", "lease_until": iso_at(timedelta(seconds=-1)),
            },
        }
        self.seed_raw_state(
            expired["id"], grounding=expired_grounding, status="refining", updated_at=iso_at()
        )

        legacy = await self.store.create_venture(venture(slug="recovery-legacy"))
        legacy_grounding = {
            **original,
            "_refinement": {"state": "pending", "context": "Legacy request", "claim_id": "legacy-refinement"},
        }
        self.seed_raw_state(
            legacy["id"], grounding=legacy_grounding, status="refining",
            updated_at=iso_at(timedelta(seconds=-store_module.WORK_LEASE_SECONDS - 2)),
        )

        reopened = self.restart_store()
        retained = await reopened.reconcile_venture(await reopened.get_by_id(active["id"]))
        recovered = await reopened.reconcile_venture(await reopened.get_by_id(expired["id"]))
        recovered_legacy = await reopened.reconcile_venture(await reopened.get_by_id(legacy["id"]))

        self.assertEqual(store_module.WORK_LEASE_SECONDS, 300)
        self.assertEqual(retained["status"], "refining")
        self.assertEqual(marker(retained)["claim_id"], "live-refinement")
        self.assertGreater(parse_iso(marker(retained)["lease_until"]), datetime.now(timezone.utc))
        for saved in (recovered, recovered_legacy):
            self.assertEqual(saved["status"], "ready")
            self.assertEqual(saved["grounding"], original)
            self.assertEqual(saved["name"], "Bench Nest")
            self.assertEqual(saved["idea_raw"], active["idea_raw"])

    async def test_expired_path_claim_is_cleared_without_changing_prior_path_or_state(self) -> None:
        previous_path = {
            "tier_label": "The Lean Build",
            "steps": [{"title": "Already completed", "status": "done"}],
        }
        row = await self.store.create_venture(venture(slug="path-recovery"))
        grounding = {
            **row["grounding"],
            "_refinement": {
                "state": "skipped", "path_claim_id": "abandoned-path",
                "path_lease_until": iso_at(timedelta(seconds=-5)),
            },
        }
        self.seed_raw_state(
            row["id"], grounding=grounding, status="planning", updated_at=iso_at(), path=previous_path
        )
        reopened = self.restart_store()

        saved = await reopened.reconcile_venture(await reopened.get_by_id(row["id"]))

        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["path"], previous_path)
        self.assertEqual(marker(saved)["state"], "skipped")
        self.assertNotIn("path_claim_id", marker(saved))
        self.assertNotIn("path_lease_until", marker(saved))

    async def test_stale_path_finish_and_release_cannot_replace_recovered_sqlite_path_claim(self) -> None:
        previous_path = {
            "tier_label": "The Lean Build",
            "steps": [{"title": "Keep this path", "status": "done"}],
        }
        row = await self.store.create_venture(venture(slug="stale-sqlite-path-worker"))
        grounding = {
            **row["grounding"],
            "_refinement": {
                "state": "completed", "claim_id": "completed-refinement",
                "path_claim_id": "old-path-worker",
                "path_lease_until": iso_at(timedelta(seconds=-1)),
            },
        }
        self.seed_raw_state(
            row["id"], grounding=grounding, status="planning",
            updated_at=iso_at(), path=previous_path,
        )
        self.restart_store()
        recovered = await self.store.reconcile_venture(await self.store.get_by_id(row["id"]))
        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(recovered["path"], previous_path)
        self.assertEqual(marker(recovered)["state"], "completed")

        fresh = await self.store.claim_path(row["id"], claim_id="new-path-worker")
        self.assertIsNotNone(fresh)
        old_finish = await self.store.finish_path(
            row["id"], {"tier_label": "Stale replacement", "steps": []},
            claim_id="old-path-worker",
        )
        await self.store.release_path_claim(row["id"], claim_id="old-path-worker")
        latest = await self.store.get_by_id(row["id"])

        self.assertIsNone(old_finish)
        self.assertEqual(latest["status"], "planning")
        self.assertEqual(latest["path"], previous_path)
        self.assertEqual(marker(latest)["state"], "completed")
        self.assertEqual(marker(latest)["path_claim_id"], "new-path-worker")

    async def test_obsolete_refinement_finish_and_cleanup_cannot_replace_a_fresh_claim(self) -> None:
        row = await self.store.create_venture(venture(slug="stale-finisher"))
        original = copy.deepcopy(row["grounding"])
        expired_marker = {
            "state": "pending", "context": "Old attempt", "claim_id": "old-worker",
            "lease_until": iso_at(timedelta(seconds=-1)),
        }
        self.seed_raw_state(
            row["id"], grounding={**original, "_refinement": expired_marker},
            status="refining", updated_at=iso_at(),
        )
        self.restart_store()
        recovered = await self.store.reconcile_venture(await self.store.get_by_id(row["id"]))
        self.assertEqual(recovered["grounding"], original)

        fresh = await self.store.claim_refinement(
            row["id"], "Fresh context", claim_id="new-worker"
        )
        self.assertIsNotNone(fresh)
        fresh_grounding = copy.deepcopy(fresh["grounding"])
        old_finish = await self.store.finish_refinement(
            row["id"],
            {
                "name": "Stale worker must not win",
                "grounding": {**original, "_refinement": {"state": "completed", "claim_id": "old-worker"}},
                "status": "ready",
            },
            claim_id="old-worker",
        )
        await self.store.clear_refinement_claim(row["id"], original, claim_id="old-worker")

        latest = await self.store.get_by_id(row["id"])
        self.assertIsNone(old_finish)
        self.assertEqual(latest["status"], "refining")
        self.assertEqual(latest["name"], row["name"])
        self.assertEqual(latest["grounding"], fresh_grounding)
        self.assertEqual(marker(latest)["claim_id"], "new-worker")

    async def test_completed_refinement_remains_one_time_after_restart(self) -> None:
        row = await self.store.create_venture(venture(slug="completed-once"))
        completed_grounding = {
            **refined_output()["grounding"],
            "_refinement": {
                "state": "completed", "context": "Keep it modular", "claim_id": "finished-worker",
            },
        }
        self.seed_raw_state(
            row["id"], grounding=completed_grounding, status="ready",
            updated_at=iso_at(timedelta(seconds=-store_module.WORK_LEASE_SECONDS * 4)),
        )
        reopened = self.restart_store()

        saved = await reopened.reconcile_venture(await reopened.get_by_id(row["id"]))
        second_claim = await reopened.claim_refinement(
            row["id"], "Try another refinement", claim_id="second-worker"
        )

        self.assertEqual(saved["grounding"], completed_grounding)
        self.assertEqual(saved["status"], "ready")
        self.assertIsNone(second_claim)

    async def test_completed_pending_render_resumes_once_on_owner_get_after_restart(self) -> None:
        row = await self.store.create_venture(
            venture(
                slug="resume-render",
                grounding={
                    **refined_output()["grounding"],
                    "_refinement": {
                        "state": "completed", "context": "Add removable trays",
                        "claim_id": "finished-rendering",
                    },
                },
                image_prompts=["resumed render prompt"],
                images_status="pending",
            )
        )
        self.restart_store()
        calls: list[str] = []
        render_started = asyncio.Event()

        async def fake_render(prompt: str, accent: str) -> bytes:
            calls.append(prompt)
            render_started.set()
            await asyncio.sleep(0.03)
            return b"restart-render-bytes"

        with patch.object(server.llm, "generate_render", fake_render):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url="http://testserver",
                headers={"Cookie": f"{server.COOKIE}={OWNER}"},
            ) as client:
                first, second = await asyncio.gather(
                    client.get(f"/api/ventures/{row['slug']}"),
                    client.get(f"/api/ventures/{row['slug']}"),
                )
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(second.status_code, 200, second.text)
                await asyncio.wait_for(render_started.wait(), timeout=1)
                for _ in range(100):
                    saved = await self.store.get_by_id(row["id"])
                    if saved["images_status"] != "pending":
                        break
                    await asyncio.sleep(0.01)

        self.assertEqual(len(calls), 1)
        self.assertIn("resumed render prompt", calls[0])
        self.assertIn("distinctive working mechanism", calls[0])
        self.assertEqual(saved["images_status"], "ready")
        self.assertTrue(saved["images"][0])

    async def test_owner_get_does_not_queue_a_render_with_a_live_lease(self) -> None:
        row = await self.store.create_venture(
            venture(
                slug="live-render-lease",
                grounding={
                    **refined_output()["grounding"],
                    "_refinement": {
                        "state": "completed",
                        "claim_id": "completed-refinement",
                        "render_claim_id": "active-render-worker",
                        "render_lease_until": iso_at(timedelta(seconds=300)),
                    },
                },
                images_status="pending",
                image_prompts=["render prompt"],
            )
        )
        self.restart_store()
        queued: list[str] = []
        render_queued = asyncio.Event()

        async def fake_worker(saved: dict) -> None:
            queued.append(saved["id"])
            render_queued.set()

        with patch.object(server, "_render_images", fake_worker):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url="http://testserver",
                headers={"Cookie": f"{server.COOKIE}={OWNER}"},
            ) as client:
                live = await client.get(f"/api/ventures/{row['slug']}")
                self.assertEqual(live.status_code, 200, live.text)
                await asyncio.sleep(0.03)
                self.assertEqual(queued, [])

                expired_grounding = copy.deepcopy(row["grounding"])
                expired_grounding["_refinement"]["render_lease_until"] = iso_at(
                    timedelta(seconds=-1)
                )
                self.seed_raw_state(
                    row["id"], grounding=expired_grounding, status="ready", updated_at=iso_at()
                )
                recovered = await client.get(f"/api/ventures/{row['slug']}")
                self.assertEqual(recovered.status_code, 200, recovered.text)
                await asyncio.wait_for(render_queued.wait(), timeout=1)

        self.assertEqual(queued, [row["id"]])

    async def test_expired_render_lease_is_replaced_and_old_image_update_is_rejected(self) -> None:
        row = await self.store.create_venture(
            venture(
                slug="render-lease-replacement",
                grounding={
                    **refined_output()["grounding"],
                    "_refinement": {
                        "state": "completed", "claim_id": "completed-refinement",
                    },
                },
                images_status="pending",
                image_prompts=["render prompt"],
                images=["obsolete image reference"],
            )
        )
        before_claim = datetime.now(timezone.utc)
        old_claim = await self.store.claim_refinement_render(row["id"])
        self.assertIsNotNone(old_claim)
        self.assertEqual(store_module.RENDER_LEASE_SECONDS, 600)
        self.assertEqual(old_claim["images"], [])
        old_token = marker(old_claim)["render_claim_id"]
        lease_deadline = parse_iso(marker(old_claim)["render_lease_until"])
        self.assertGreaterEqual(
            lease_deadline,
            before_claim + timedelta(seconds=store_module.RENDER_LEASE_SECONDS - 1),
        )
        self.assertLessEqual(
            lease_deadline,
            datetime.now(timezone.utc) + timedelta(seconds=store_module.RENDER_LEASE_SECONDS + 1),
        )
        expired_grounding = copy.deepcopy(old_claim["grounding"])
        expired_grounding["_refinement"]["render_lease_until"] = iso_at(timedelta(seconds=-1))
        self.seed_raw_state(
            row["id"], grounding=expired_grounding, status="ready", updated_at=iso_at()
        )
        self.restart_store()

        new_claim = await self.store.claim_refinement_render(row["id"])
        self.assertIsNotNone(new_claim)
        new_token = marker(new_claim)["render_claim_id"]
        old_url = await self.store.save_render(
            row["id"], 0, b"old-render-bytes", render_claim_id=old_token
        )
        fresh_url = await self.store.save_render(
            row["id"], 0, b"fresh-render-bytes", render_claim_id=new_token
        )
        self.assertNotEqual(new_token, old_token)
        stale = await self.store.update_refinement_images(
            row["id"], old_token, {"images": [old_url], "images_status": "ready"}
        )
        fresh = await self.store.update_refinement_images(
            row["id"], new_token, {"images": [fresh_url], "images_status": "ready"}
        )

        saved = await self.store.get_by_id(row["id"])
        self.assertFalse(stale)
        self.assertTrue(fresh)
        self.assertEqual(saved["images"], [fresh_url])
        self.assertEqual(saved["images_status"], "ready")
        self.assertEqual(old_url, fresh_url)
        self.assertNotEqual(
            self.store._render_filename(
                {"id": row["id"], "grounding": {"_refinement": {"render_claim_id": old_token}}},
                0,
                "png",
            ),
            self.store._render_filename(
                {"id": row["id"], "grounding": {"_refinement": {"render_claim_id": new_token}}},
                0,
                "png",
            ),
        )
        self.assertEqual((await self.store.read_render(saved, 0))[0], b"fresh-render-bytes")


class StatefulPostgrest:
    """Small in-memory PostgREST fixture that honors the filters sent by the store."""

    def __init__(self, row: dict):
        self.row = copy.deepcopy(row)
        self.requests: list[httpx.Request] = []
        self.race_after_next_get: dict | None = None
        self.storage_objects: dict[str, bytes] = {}
        self.storage_requests: list[httpx.Request] = []

    @staticmethod
    def _value(row: dict, key: str):
        value = row
        for part in key.replace("->>", "->").split("->"):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    @classmethod
    def _matches(cls, row: dict, params: httpx.QueryParams) -> bool:
        for key, expression in params.items():
            if key in {"select", "limit", "order"}:
                continue
            if "." not in expression:
                continue
            operator, expected = expression.split(".", 1)
            actual = cls._value(row, key)
            if operator == "eq":
                if isinstance(actual, (dict, list)):
                    try:
                        if actual != json.loads(expected):
                            return False
                    except json.JSONDecodeError:
                        return False
                elif str(actual) != expected:
                    return False
            if operator == "neq" and str(actual) == expected:
                return False
            if operator == "is" and expected == "null" and actual is not None:
                return False
            if operator in {"lt", "lte"}:
                if actual is None or (actual >= expected if operator == "lt" else actual > expected):
                    return False
        return True

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.startswith("/storage/v1/object/"):
            self.storage_requests.append(request)
            segments = request.url.path.split("/")
            if request.method == "POST" and segments[3] == "object":
                object_key = "/".join(segments[4:])
                self.storage_objects[object_key] = request.content
                return httpx.Response(200, json={"Key": object_key})
            if request.method == "GET" and segments[4] == "authenticated":
                object_key = "/".join(segments[5:])
                body = self.storage_objects.get(object_key)
                return (
                    httpx.Response(200, content=body, headers={"Content-Type": "image/png"})
                    if body is not None else httpx.Response(404)
                )
            return httpx.Response(405)
        if not request.url.path.startswith("/rest/v1/"):
            return httpx.Response(404)
        if request.method == "GET":
            result = copy.deepcopy(self.row)
            if self.race_after_next_get is not None:
                self.row = copy.deepcopy(self.race_after_next_get)
                self.race_after_next_get = None
            return httpx.Response(200, json=[result] if result else [])
        if request.method == "PATCH":
            if self.row and self._matches(self.row, request.url.params):
                fields = json.loads(request.content)
                self.row.update(copy.deepcopy(fields))
                if request.headers.get("Prefer", "").startswith("return=minimal"):
                    return httpx.Response(204)
                return httpx.Response(200, json=[copy.deepcopy(self.row)])
            if request.headers.get("Prefer", "").startswith("return=minimal"):
                return httpx.Response(204)
            return httpx.Response(200, json=[])
        return httpx.Response(405)


class SupabaseRestartRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store: SupabaseStore | None = None

    async def asyncTearDown(self) -> None:
        if self.store is not None:
            await self.store._client.aclose()

    def make_store(self, row: dict, fixture: StatefulPostgrest) -> SupabaseStore:
        self.store = SupabaseStore("https://example.supabase.co", "test-only-key")
        self.store._client._transport = httpx.MockTransport(fixture)
        return self.store

    async def test_supabase_expired_refinement_recovers_and_old_finish_cleanup_cannot_clobber_new_claim(self) -> None:
        row = venture(slug="supabase-expired-refinement")
        original_grounding = copy.deepcopy(row["grounding"])
        row["grounding"] = {
            **original_grounding,
            "_refinement": {
                "state": "pending", "context": "Interrupted context",
                "claim_id": "old-supabase-refiner",
                "lease_until": iso_at(timedelta(seconds=-1)),
            },
        }
        row["status"] = "refining"
        row["updated_at"] = iso_at()
        fixture = StatefulPostgrest(row)
        store = self.make_store(row, fixture)

        recovered = await store.reconcile_venture(row)
        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(recovered["grounding"], original_grounding)
        self.assertEqual(recovered["provenance_code"], row["provenance_code"])
        fresh = await store.claim_refinement(
            row["id"], "New worker context", claim_id="new-supabase-refiner"
        )
        self.assertIsNotNone(fresh)

        stale_finish = await store.finish_refinement(
            row["id"],
            {
                "name": "Stale Supabase result",
                "grounding": {
                    **original_grounding,
                    "_refinement": {"state": "completed", "claim_id": "old-supabase-refiner"},
                },
                "status": "ready",
            },
            claim_id="old-supabase-refiner",
        )
        await store.clear_refinement_claim(
            row["id"], original_grounding, claim_id="old-supabase-refiner"
        )
        latest = fixture.row

        self.assertIsNone(stale_finish)
        self.assertEqual(latest["status"], "refining")
        self.assertEqual(latest["name"], row["name"])
        self.assertEqual(marker(latest)["claim_id"], "new-supabase-refiner")
        self.assertEqual(marker(latest)["context"], "New worker context")

    async def test_supabase_expired_path_recovery_preserves_completed_state_and_protects_new_token(self) -> None:
        previous_path = {
            "tier_label": "Original path",
            "steps": [{"title": "Preserve completed work", "status": "done"}],
        }
        row = venture(slug="supabase-expired-path")
        original_grounding = copy.deepcopy(row["grounding"])
        row["grounding"] = {
            **original_grounding,
            "_refinement": {
                "state": "completed", "claim_id": "completed-refinement",
                "context": "Completed refinement stays completed",
                "path_claim_id": "old-supabase-path",
                "path_lease_until": iso_at(timedelta(seconds=-1)),
            },
        }
        row["path"] = previous_path
        row["status"] = "planning"
        row["updated_at"] = iso_at()
        fixture = StatefulPostgrest(row)
        store = self.make_store(row, fixture)

        recovered = await store.reconcile_venture(row)

        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(recovered["path"], previous_path)
        self.assertEqual(marker(recovered)["state"], "completed")
        self.assertEqual(marker(recovered)["claim_id"], "completed-refinement")
        self.assertNotIn("path_claim_id", marker(recovered))
        self.assertNotIn("path_lease_until", marker(recovered))
        self.assertEqual(recovered["grounding"]["exists"], original_grounding["exists"])
        self.assertEqual(recovered["provenance_code"], row["provenance_code"])

        fresh = await store.claim_path(row["id"], claim_id="new-supabase-path")
        self.assertIsNotNone(fresh)
        stale_finish = await store.finish_path(
            row["id"], {"tier_label": "Stale path", "steps": []},
            claim_id="old-supabase-path",
        )
        await store.release_path_claim(row["id"], claim_id="old-supabase-path")
        latest = fixture.row

        self.assertIsNone(stale_finish)
        self.assertEqual(latest["status"], "planning")
        self.assertEqual(latest["path"], previous_path)
        self.assertEqual(marker(latest)["state"], "completed")
        self.assertEqual(marker(latest)["path_claim_id"], "new-supabase-path")

    async def test_supabase_reconciliation_retains_live_refinement_and_path_claims(self) -> None:
        live_refinement = venture(slug="supabase-live-refinement")
        live_refinement["grounding"] = {
            **live_refinement["grounding"],
            "_refinement": {
                "state": "pending", "claim_id": "live-supabase-refinement",
                "lease_until": iso_at(timedelta(seconds=120)),
            },
        }
        live_refinement["status"] = "refining"
        live_refinement["updated_at"] = iso_at()
        fixture = StatefulPostgrest(live_refinement)
        store = self.make_store(live_refinement, fixture)

        retained = await store.reconcile_venture(live_refinement)

        self.assertEqual(retained["status"], "refining")
        self.assertEqual(marker(retained)["claim_id"], "live-supabase-refinement")
        self.assertFalse(any(request.method == "PATCH" for request in fixture.requests))

        await store._client.aclose()
        live_path = venture(slug="supabase-live-path")
        live_path["status"] = "planning"
        live_path["path"] = {
            "tier_label": "Keep live path",
            "steps": [{"title": "In progress", "status": "todo"}],
        }
        live_path["grounding"] = {
            **live_path["grounding"],
            "_refinement": {
                "state": "skipped", "path_claim_id": "live-supabase-path",
                "path_lease_until": iso_at(timedelta(seconds=120)),
            },
        }
        live_path["updated_at"] = iso_at()
        path_fixture = StatefulPostgrest(live_path)
        path_store = self.make_store(live_path, path_fixture)

        retained_path = await path_store.reconcile_venture(live_path)

        self.assertEqual(retained_path["status"], "planning")
        self.assertEqual(retained_path["path"], live_path["path"])
        self.assertEqual(marker(retained_path)["path_claim_id"], "live-supabase-path")
        self.assertFalse(any(request.method == "PATCH" for request in path_fixture.requests))

    async def test_reconciliation_patch_is_snapshot_conditional_under_worker_race(self) -> None:
        original = venture(slug="supabase-recovery-race")
        original["grounding"] = {
            **original["grounding"],
            "_refinement": {
                "state": "pending", "context": "Old worker", "claim_id": "old-supabase-worker",
                "lease_until": iso_at(timedelta(seconds=-1)),
            },
        }
        original["status"] = "refining"
        original["updated_at"] = iso_at()
        fresh = copy.deepcopy(original)
        fresh["updated_at"] = iso_at(timedelta(milliseconds=1))
        fresh["grounding"]["_refinement"] = {
            "state": "pending", "context": "New worker", "claim_id": "new-supabase-worker",
            "lease_until": iso_at(timedelta(seconds=200)),
        }
        fixture = StatefulPostgrest(original)
        # Reconciliation receives the original request snapshot, then this
        # newer worker wins the simulated write race before the CAS is applied.
        fixture.row = copy.deepcopy(fresh)
        store = self.make_store(original, fixture)

        reconciled = await store.reconcile_venture(original)
        patches = [request for request in fixture.requests if request.method == "PATCH"]

        self.assertEqual(
            reconciled["status"], "refining",
            msg=f"reconciled={reconciled!r}; persisted={fixture.row!r}; "
                f"patches={[dict(request.url.params) for request in patches]!r}",
        )
        self.assertEqual(marker(reconciled)["claim_id"], "new-supabase-worker")
        self.assertEqual(len(patches), 1)
        filters = patches[0].url.params
        self.assertEqual(filters["id"], f"eq.{original['id']}")
        self.assertEqual(filters["status"], "eq.refining")
        self.assertEqual(filters["updated_at"], f"eq.{original['updated_at']}")
        self.assertEqual(
            json.loads(filters["grounding"].removeprefix("eq."))["_refinement"]["claim_id"],
            "old-supabase-worker",
        )

    async def test_supabase_render_claim_competes_by_lease_and_rejects_old_updates(self) -> None:
        row = venture(slug="supabase-render-lease")
        row["images_status"] = "pending"
        row["grounding"] = {
            **row["grounding"],
            "_refinement": {"state": "completed", "claim_id": "completed-refinement"},
        }
        row["updated_at"] = iso_at()
        fixture = StatefulPostgrest(row)
        store = self.make_store(row, fixture)

        first = await store.claim_refinement_render(row["id"])
        first_token = marker(first)["render_claim_id"]
        competed = await store.claim_refinement_render(row["id"])
        self.assertIsNone(competed)

        fixture.row["grounding"]["_refinement"]["render_lease_until"] = iso_at(
            timedelta(seconds=-1)
        )
        replacement = await store.claim_refinement_render(row["id"])
        replacement_token = marker(replacement)["render_claim_id"]
        self.assertNotEqual(first_token, replacement_token)

        stale = await store.update_refinement_images(
            row["id"], first_token, {"images": ["/stale.png"], "images_status": "ready"}
        )
        current = await store.update_refinement_images(
            row["id"], replacement_token, {"images": ["/current.png"], "images_status": "ready"}
        )
        patches = [request for request in fixture.requests if request.method == "PATCH"]

        self.assertFalse(stale)
        self.assertTrue(current)
        self.assertEqual(fixture.row["images"], ["/current.png"])
        self.assertTrue(any(
            "grounding->_refinement->>render_claim_id" in request.url.params
            for request in patches
        ))

    async def test_supabase_render_uploads_are_attempt_scoped_and_guarded_route_serves_current_bytes(self) -> None:
        row = venture(slug="supabase-token-storage")
        row["grounding"] = {
            **row["grounding"],
            "_refinement": {
                "state": "completed", "claim_id": "completed-refinement",
                "render_claim_id": "new-render-worker",
                "render_lease_until": iso_at(timedelta(seconds=300)),
            },
        }
        row["images"] = [f"/api/venture-images/{row['id']}/0.png"]
        row["images_status"] = "ready"
        row["updated_at"] = iso_at()
        fixture = StatefulPostgrest(row)
        store = self.make_store(row, fixture)

        current_url = await store.save_render(
            row["id"], 0, b"bytes-from-new-worker", render_claim_id="new-render-worker"
        )
        old_url = await store.save_render(
            row["id"], 0, b"bytes-from-late-old-worker", render_claim_id="old-render-worker"
        )
        self.assertEqual(current_url, old_url)
        self.assertEqual(
            fixture.storage_objects[f"renders/{row['id']}/new-render-worker/0.png"],
            b"bytes-from-new-worker",
        )
        self.assertEqual(
            fixture.storage_objects[f"renders/{row['id']}/old-render-worker/0.png"],
            b"bytes-from-late-old-worker",
        )
        self.assertEqual(await store.read_render(row, 0), (b"bytes-from-new-worker", "image/png"))

        original_store = server.store
        server.store = store
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url="http://testserver",
            ) as anonymous:
                denied = await anonymous.get(
                    f"/api/venture-images/{row['id']}/0.png"
                )
            self.assertEqual(denied.status_code, 404)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url="http://testserver",
                headers={"Cookie": f"{server.COOKIE}={OWNER}"},
            ) as owner:
                allowed = await owner.get(f"/api/venture-images/{row['id']}/0.png")
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.content, b"bytes-from-new-worker")

            fixture.row["visibility"] = "public"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url="http://testserver",
            ) as public_client:
                public = await public_client.get(f"/api/venture-images/{row['id']}/0.png")
            self.assertEqual(public.status_code, 200)
            self.assertEqual(public.content, b"bytes-from-new-worker")
        finally:
            server.store = original_store


if __name__ == "__main__":
    unittest.main()