"""Cleanup only touches disposable files and mocked Storage, never live objects."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.render_cleanup import SQLiteCleanup, SupabaseCleanup, delete_approved, inventory
from app.store import SQLiteStore, SupabaseStore


OLD = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()


def record(venture_id="venture"):
    return {"id": venture_id, "slug": venture_id, "owner_token": "disposable-owner",
            "idea_raw": "fixture", "idea_hash": "fixture", "provenance_code": "fixture",
            "status": "ready", "images_status": "ready", "updated_at": OLD,
            "grounding": {"_refinement": {"state": "completed", "render_claim_id": "new",
                                        "render_lease_until": OLD}},
            "images": [f"/api/venture-images/{venture_id}/0.png"]}


class SQLiteCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(self.temp.name)
        await self.store.create_venture(record())
        self.set_row(updated_at=OLD)
        self.backend = SQLiteCleanup(self.temp.name)
        self.old = self.file("venture-old-0.png")
        self.file("venture-new-0.png")
        self.file("venture-0.png")
        self.file("another-old-0.png")
        self.file("venture-old-not-an-image.txt")

    async def asyncTearDown(self):
        await self.backend.close()
        self.store._db.close()
        self.temp.cleanup()

    def file(self, name, *, old=True):
        path = Path(self.temp.name) / "renders" / name
        path.write_bytes(b"disposable")
        if old:
            age = datetime.fromisoformat(OLD).timestamp()
            os.utime(path, (age, age))
        return path

    def set_row(self, **fields):
        fields = {key: json.dumps(value) if isinstance(value, (dict, list)) else value
                  for key, value in fields.items()}
        self.store._db.execute(
            "UPDATE ventures SET " + ", ".join(f"{key}=?" for key in fields) + " WHERE id='venture'",
            list(fields.values()),
        )
        self.store._db.commit()

    async def plan(self, attempt="old"):
        return await inventory(self.backend, "venture", attempt)

    async def test_inventory_and_approved_exact_deletion_preserve_records(self):
        before = await self.backend.records()
        plan = await self.plan()
        self.assertEqual([x["path"] for x in plan["candidates"]], ["venture-old-0.png"])
        self.assertTrue(self.old.exists())  # default dry run
        with self.assertRaises(ValueError):
            await delete_approved(self.backend, plan, plan["approval"])
        with self.assertRaises(ValueError):
            await delete_approved(self.backend, plan, "wrong", workers_stopped=True)
        self.assertEqual(await delete_approved(self.backend, plan, plan["approval"], workers_stopped=True),
                         ["venture-old-0.png"])
        self.assertEqual(before, await self.backend.records())
        for name in ("venture-new-0.png", "venture-0.png", "another-old-0.png",
                     "venture-old-not-an-image.txt"):
            self.assertTrue((self.old.parent / name).exists())

    async def test_current_pending_unknown_and_live_lease_protected(self):
        self.assertFalse((await self.plan("new"))["candidates"])
        for fields in (
            {"images_status": "pending"}, {"status": "refining"}, {"status": "planning"},
            {"images_status": "unknown"}, {"updated_at": "invalid"},
            {"grounding": {}},
            {"grounding": {"_refinement": {"state": "completed", "render_claim_id": "new",
                                          "render_lease_until": datetime.now(timezone.utc).isoformat()}}},
        ):
            self.set_row(**{key: record()[key] for key in ("status", "images_status", "updated_at", "grounding")})
            self.set_row(**fields)
            self.assertFalse((await self.plan())["candidates"], fields)

    async def test_recent_and_unknown_ages_retention_and_scopes(self):
        self.file("venture-old-0.png", old=False)
        self.assertFalse((await self.plan())["candidates"])
        for value in ("", "../old", "old/other", "old%2Fother", "old--"):
            with self.assertRaises(ValueError):
                await inventory(self.backend, "venture", value)
        for retention in (0, 600, 86399, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                await inventory(self.backend, "venture", "old", retention)
        with self.assertRaises(ValueError):
            await inventory(self.backend, "missing", "old")

    async def test_shared_explicit_and_implicit_references(self):
        shared = record("other")
        shared["images"] = ["/renders/venture-old-0.png"]
        await self.store.create_venture(shared)
        self.assertFalse((await self.plan())["candidates"])
        self.store._db.execute("DELETE FROM ventures WHERE id='other'")
        self.store._db.commit()
        await self.store.create_venture(record("venture-old"))  # ambiguous legacy file
        self.assertFalse((await self.plan())["candidates"])

    async def test_ambiguous_other_attempt_and_recent_venture_stability(self):
        self.file("venture-old-extra-0.png")
        await self.store.create_venture(record("venture-old"))
        self.assertFalse((await self.plan("old-extra"))["candidates"])
        self.set_row(updated_at=datetime.now(timezone.utc).isoformat())
        self.assertFalse((await self.plan())["candidates"])

    async def test_cli_dry_run_approval_scope_and_output_safety(self):
        command = [sys.executable, "scripts/render_attempt_cleanup.py", "--backend", "sqlite",
                   "--data-dir", self.temp.name, "--venture-id", "venture", "--attempt", "old"]
        output = Path(self.temp.name) / "plan.json"
        result = subprocess.run(command + ["--output", str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan, json.loads(output.read_text()))
        self.assertTrue(self.old.exists())
        result = subprocess.run(command + ["--output", str(self.old)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.old.read_bytes(), b"disposable")
        deletion = command + ["--delete-plan", str(output), "--approve", plan["approval"]]
        result = subprocess.run(deletion, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run(
            deletion + ["--workers-stopped", "--attempt", "new"], capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run(deletion + ["--workers-stopped"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["deleted"], ["venture-old-0.png"])

    async def test_readonly_backend_does_not_create_missing_database(self):
        missing = Path(self.temp.name) / "missing"
        with self.assertRaises(Exception):
            SQLiteCleanup(missing)
        self.assertFalse(missing.exists())

    async def test_symlink_hardlink_and_changed_inventory_fail_closed(self):
        plan = await self.plan()
        self.old.write_bytes(b"late worker")
        with self.assertRaises(ValueError):
            await delete_approved(self.backend, plan, plan["approval"], workers_stopped=True)
        self.old.unlink()
        outside = Path(self.temp.name) / "outside.png"
        outside.write_bytes(b"do not delete")
        self.old.symlink_to(outside)
        self.assertFalse((await self.plan())["candidates"])
        self.old.unlink()
        os.link(outside, self.old)
        self.assertFalse((await self.plan())["candidates"])
        self.assertTrue(outside.exists())

    async def test_new_reference_record_and_tampered_plan_block_deletion(self):
        plan = await self.plan()
        tampered = copy.deepcopy(plan)
        tampered["candidates"][0]["path"] = "venture-new-0.png"
        with self.assertRaises(ValueError):
            await delete_approved(self.backend, tampered, plan["approval"], workers_stopped=True)
        self.set_row(images=["/renders/venture-old-0.png"])
        with self.assertRaises(ValueError):
            await delete_approved(self.backend, plan, plan["approval"], workers_stopped=True)
        self.assertTrue(self.old.exists())


class SupabaseCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rows = [record()]
        self.objects = [{"name": f"{index}.png", "id": str(index), "created_at": OLD,
                         "updated_at": OLD, "metadata": {"size": 10}} for index in range(105)]
        self.deleted = []
        self.calls = []
        self.partial = False
        self.failure = False
        self.store = SupabaseStore("https://disposable.supabase.co", "mock-only-key")
        await self.store._client.aclose()
        self.store._client = httpx.AsyncClient(base_url=self.store.url, transport=httpx.MockTransport(self.handle))
        self.backend = SupabaseCleanup(self.store)

    async def asyncTearDown(self):
        await self.backend.close()

    def handle(self, request):
        self.calls.append(request)
        if self.failure:
            return httpx.Response(403)
        if request.url.path == "/rest/v1/ventures":
            self.assertEqual(request.method, "GET")
            offset = int(request.url.params["offset"])
            page = self.rows[offset:offset + 100]
            headers = {} if self.partial else {"Content-Range": f"{offset}-{offset + len(page)-1}/{len(self.rows)}"}
            return httpx.Response(200, json=page, headers=headers)
        if request.url.path == "/storage/v1/object/list/renders":
            body = json.loads(request.content)
            self.assertEqual(body["prefix"], "venture/old")
            offset = body["offset"]
            return httpx.Response(200, json=self.objects[offset:offset + 100])
        if request.method == "DELETE" and request.url.path == "/storage/v1/object/renders":
            self.deleted.extend(json.loads(request.content)["prefixes"])
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected storage operation: {request.method} {request.url.path}")

    async def plan(self):
        return await inventory(self.backend, "venture", "old")

    async def test_paginated_inventory_exact_delete_no_record_or_bucket_writes(self):
        for index in range(105):
            self.rows.append(record(f"other-{index}"))
        plan = await self.plan()
        self.assertEqual(len(plan["candidates"]), 105)
        self.assertFalse(self.deleted)
        await delete_approved(self.backend, plan, plan["approval"], workers_stopped=True)
        self.assertEqual(set(self.deleted), {f"venture/old/{i}.png" for i in range(105)})
        self.assertEqual(len(self.rows), 106)

    async def test_shared_unknown_recent_folder_and_legacy_protection(self):
        self.rows.append({**record("other"), "images": ["https://example/venture%2Fold%2F0.png"]})
        self.objects[1]["updated_at"] = "unknown"
        self.objects[2]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.objects.extend([{"name": "folder", "id": None}, {"name": "../0.png", "id": "bad"},
                             {"name": "new/0.png", "id": "bad"}])
        plan = await self.plan()
        paths = {obj["path"] for obj in plan["candidates"]}
        self.assertNotIn("venture/old/0.png", paths)
        self.assertNotIn("venture/old/1.png", paths)
        self.assertNotIn("venture/old/2.png", paths)
        self.assertEqual(len(paths), 102)

    async def test_partial_listing_errors_and_changed_object_reject_deletion(self):
        self.partial = True
        with self.assertRaises(ValueError):
            await self.plan()
        self.partial = False
        self.failure = True
        with self.assertRaises(httpx.HTTPStatusError):
            await self.plan()
        self.failure = False
        plan = await self.plan()
        self.objects[0]["metadata"]["size"] = 11
        with self.assertRaises(ValueError):
            await delete_approved(self.backend, plan, plan["approval"], workers_stopped=True)
        self.assertFalse(self.deleted)

    async def test_backend_scope_mismatch_and_shared_claim(self):
        plan = await self.plan()
        self.backend.identity["bucket"] = "different"
        with self.assertRaises(ValueError):
            await delete_approved(self.backend, plan, plan["approval"], workers_stopped=True)
        self.rows.append({**record("other"), "grounding": {"_refinement": {"render_claim_id": "old"}}})
        self.assertFalse((await self.plan())["candidates"])


if __name__ == "__main__":
    unittest.main()