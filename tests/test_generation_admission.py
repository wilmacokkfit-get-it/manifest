"""Quota contract checks against disposable SQLite and real PostgreSQL RPCs."""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import httpx

from app.store import SQLiteStore, SupabaseStore
from generation_process_worker import reserve_from_process
from test_refinement import OWNER, refined_output, server, venture
import test_storage_policy_audit_postgres as pg_harness


class AdmissionContract:
    """Same behavior through both actual store adapters."""

    async def test_visitor_hour_and_day_include_pending_work(self):
        token = str(uuid.uuid4())
        self.assertIsNone(await self.store.reserve_generation(token, "visitor", 1, 2, 10))
        self.assertEqual(await self.store.reserve_generation("blocked", "visitor", 1, 2, 10), "hour")
        self.assertEqual(await self.store.reserve_generation("day-blocked", "visitor", 10, 1, 10), "day")
        await self.store.release_generation(token)
        self.assertIsNone(await self.store.reserve_generation("replacement", "visitor", 1, 1, 10))
        # Late cleanup cannot remove the replacement's distinct token.
        await self.store.release_generation(token)
        self.assertEqual(await self.store.reserve_generation("late", "visitor", 1, 1, 10), "hour")

    async def test_global_boundary_includes_existing_and_pending_without_double_count(self):
        await self.store.create_venture(venture("existing"))
        self.assertIsNone(await self.store.reserve_generation("winner", "a", 10, 10, 2))
        self.assertEqual(await self.store.reserve_generation("loser", "b", 10, 10, 2), "global")
        row = venture("committed")
        saved = await self.store.create_reserved_venture("winner", row)
        await self.store.release_generation("winner")
        again = await self.store.create_reserved_venture("winner", venture("ignored-retry"))
        self.assertEqual(again["id"], saved["id"])
        self.assertEqual(await self.store.reserve_generation("still-full", "c", 10, 10, 2), "global")
        # Two births + one new reservation, not two births + a duplicate hit.
        self.assertIsNone(await self.store.reserve_generation("last", "c", 10, 10, 3))
        self.assertEqual(await self.store.reserve_generation("over", "d", 10, 10, 3), "global")

    async def test_failed_insert_rolls_back_both_birth_and_charge(self):
        await self.store.create_venture(venture("duplicate"))
        self.assertIsNone(await self.store.reserve_generation("retry", "a", 1, 1, 2))
        with self.assertRaises(Exception):
            await self.store.create_reserved_venture("retry", venture("duplicate"))
        saved = await self.store.create_reserved_venture("retry", venture("valid"))
        self.assertEqual(saved["slug"], "valid")
        self.assertEqual(await self.store.reserve_generation("next", "a", 1, 1, 20), "hour")

    async def test_crash_recovery_fences_expired_worker_and_keeps_success(self):
        self.assertIsNone(await self.store.reserve_generation("crashed", "a", 1, 1, 1))
        await self.expire("crashed")
        with self.assertRaises(Exception):
            await self.store.create_reserved_venture("crashed", venture("too-late"))
        self.assertIsNone(await self.store.reserve_generation("new", "a", 1, 1, 1))
        with self.assertRaises(Exception):
            await self.store.create_reserved_venture("crashed", venture("late-after-recovery"))
        await self.store.create_reserved_venture("new", venture("saved"))
        await self.expire("new")
        self.assertEqual(await self.store.reserve_generation("after-restart", "a", 1, 1, 1), "hour")

    async def test_refinement_shares_visitor_capacity_but_not_global_capacity(self):
        self.assertIsNone(await self.store.reserve_generation("refine", "a", 1, 1, None))
        self.assertEqual(await self.store.reserve_generation("birth", "a", 1, 1, 1), "hour")
        self.assertIsNone(await self.store.reserve_generation("other", "b", 1, 1, 1))
        await self.store.consume_refinement_admission("refine")
        await self.store.release_generation("refine")
        self.assertEqual(await self.store.reserve_generation("retry", "a", 1, 1, None), "hour")

    async def test_midnight_completion_counts_today_without_changing_provenance(self):
        self.assertIsNone(await self.store.reserve_generation("overnight", "a", 10, 10, 1))
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        saved = await self.store.create_reserved_venture("overnight", venture("overnight", created_at=yesterday))
        self.assertEqual(saved["created_at"][:10], yesterday[:10])
        self.assertEqual(await self.store.reserve_generation("today", "b", 10, 10, 1), "global")

    async def test_old_visitor_charges_roll_off(self):
        self.assertIsNone(await self.store.reserve_generation("old", "a", 1, 1, None))
        await self.store.consume_refinement_admission("old")
        await self.age_charge("old", 3601)
        self.assertEqual(await self.store.reserve_generation("day-full", "a", 1, 1, None), "day")
        await self.age_charge("old", 86401)
        self.assertIsNone(await self.store.reserve_generation("tomorrow", "a", 1, 1, None))


class SQLiteAdmissionTests(AdmissionContract, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="generation-quota-")
        self.store = SQLiteStore(self.directory.name)

    async def asyncTearDown(self):
        self.store._db.close()
        self.directory.cleanup()

    async def expire(self, token):
        self.store._db.execute("UPDATE generation_admissions SET expires_at = 0 WHERE token = ?", (token,))
        self.store._db.commit()

    async def age_charge(self, token, seconds):
        self.store._db.execute("UPDATE generation_admissions SET admitted_at = ? WHERE token = ?",
                               (time.time() - seconds, token))
        self.store._db.commit()

    async def test_separate_processes_cannot_bypass_cap_one(self):
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
            for shared_caller, hour, global_cap, expected in ((True, 1, 10, "hour"), (False, 10, 1, "global")):
                with self.store._lock:
                    self.store._db.execute("DELETE FROM generation_admissions")
                    self.store._db.commit()
                results = await asyncio.gather(*[
                    loop.run_in_executor(pool, reserve_from_process, self.directory.name, str(uuid.uuid4()),
                                         "same" if shared_caller else str(i), hour, 10, global_cap)
                    for i in range(8)
                ])
                self.assertEqual(results.count(None), 1, results)
                self.assertEqual(results.count(expected), 7, results)

    async def test_new_connection_observes_persisted_capacity(self):
        await self.store.reserve_generation("work", "a", 1, 1, 1)
        second = SQLiteStore(self.directory.name)
        try:
            self.assertEqual(await second.reserve_generation("new", "a", 1, 1, 1), "hour")
            await second.release_generation("work")
            self.assertIsNone(await self.store.reserve_generation("replacement", "a", 1, 1, 1))
        finally:
            second._db.close()


class PostgresAdmissionTests(AdmissionContract, unittest.IsolatedAsyncioTestCase):
    """Mock only the HTTP transport; run production RPC SQL in real PG sessions."""

    @classmethod
    def setUpClass(cls):
        cls.cluster = type("AdmissionCluster", (pg_harness.StoragePolicyAuditPostgresTests,), {})
        cls.cluster.setUpClass()
        cls.addClassCleanup(cls.cleanup)
        base = Path("supabase/migration.sql").read_text().split("-- Lock everything down.")[0]
        cls.sql("create role anon; create role authenticated; create role service_role bypassrls;")
        cls.sql(base + """
            alter table ventures add column path jsonb;
            alter table ventures add column splices jsonb;
            alter table ventures enable row level security;
            grant usage on schema public to service_role;
            grant select, insert, update, delete on ventures to service_role;
        """)
        cls.sql(Path("supabase/generation-admission.sql").read_text())

    @classmethod
    def cleanup(cls):
        cls.cluster.doClassCleanups()
        if cls.cluster.tearDown_exceptions:
            raise RuntimeError("Disposable PostgreSQL shutdown failed; recovery directory preserved.")

    @classmethod
    def sql(cls, query):
        return cls.cluster.run_tool(
            "psql", "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1",
            "-h", str(cls.cluster.socket), "-U", "audit_owner", "-d", "postgres", "-c", query).strip()

    async def asyncSetUp(self):
        self.sql("truncate generation_admissions, signals, ventures;")
        self.store = SupabaseStore("https://quota-fixture.invalid", "fixture-only")
        await self.store._client.aclose()
        self.store._client = httpx.AsyncClient(transport=httpx.MockTransport(self.transport),
                                                base_url=self.store.url)

    async def asyncTearDown(self):
        await self.store._client.aclose()

    async def transport(self, request):
        body = json.loads(request.content)
        name = request.url.path.removeprefix("/rest/v1/rpc/")
        allowed = {"reserve_generation", "release_generation", "consume_refinement_admission",
                   "create_reserved_venture"}
        if name == "/rest/v1/ventures":
            row = body
            # Seed historical/owner records using the same atomic insertion SQL,
            # then remove their test-only admission so they count as legacy births.
            token = str(uuid.uuid4())
            await self.store.reserve_generation(token, token, 100, 100, 100)
            saved = await self.store.create_reserved_venture(token, row)
            await asyncio.to_thread(self.sql, f"delete from generation_admissions where token = '{token}';")
            return httpx.Response(201, json=[saved])
        self.assertIn(name, allowed)
        def literal(value):
            if value is None:
                return "NULL"
            if isinstance(value, int):
                return str(value)
            text = json.dumps(value) if isinstance(value, dict) else value
            return "'" + text.replace("'", "''") + "'" + ("::jsonb" if isinstance(value, dict) else "")
        args = ", ".join(f"{key} => {literal(value)}" for key, value in body.items())
        try:
            output = await asyncio.to_thread(
                self.sql, f"set role service_role; select coalesce(to_jsonb(public.{name}({args})), 'null'::jsonb);")
            return httpx.Response(200, json=json.loads(output))
        except RuntimeError as exc:
            return httpx.Response(400, json={"message": str(exc)})

    async def expire(self, token):
        self.sql(f"update generation_admissions set expires_at = '1970-01-01' where token = '{token}';")

    async def age_charge(self, token, seconds):
        self.sql(f"update generation_admissions set admitted_at = clock_timestamp() - "
                 f"interval '{seconds} seconds' where token = '{token}';")

    async def test_separate_postgres_sessions_serialize_cap_one(self):
        for shared_caller, hour, global_cap, expected in ((True, 1, 10, "hour"), (False, 10, 1, "global")):
            self.sql("truncate generation_admissions;")
            results = await asyncio.gather(*[
                self.store.reserve_generation(str(uuid.uuid4()), "same" if shared_caller else str(i),
                                              hour, 10, global_cap) for i in range(8)
            ])
            self.assertEqual(results.count(None), 1, results)
            self.assertEqual(results.count(expected), 7, results)

    async def test_browser_roles_cannot_call_quota_functions_or_read_ledger(self):
        for role in ("anon", "authenticated"):
            for query in (
                "select * from generation_admissions",
                "select reserve_generation('x', 'visitor', 10, 10, 10)",
                "select release_generation('x')",
                "select consume_refinement_admission('x')",
                "select create_reserved_venture('x', '{}'::jsonb)",
            ):
                with self.assertRaisesRegex(RuntimeError, "permission denied"):
                    self.sql(f"set role {role}; {query};")


class GenerationAdmissionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="generation-routes-")
        self.store = SQLiteStore(self.directory.name)
        self.patches = [
            patch.object(server, "store", self.store),
            patch.object(server, "is_app_owner", AsyncMock(return_value=False)),
            patch.object(server.llm, "image_provider", return_value="none"),
            patch.object(server, "GEN_PER_HOUR", 1),
            patch.object(server, "GEN_PER_DAY", 1),
            patch.object(server, "GLOBAL_PER_DAY", 1),
        ]
        for p in self.patches:
            p.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
            base_url="https://manifest.test", headers={"Cookie": f"{server.COOKIE}={OWNER}"})

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self.store._db.close()
        self.directory.cleanup()

    async def test_simultaneous_manifest_rejected_before_second_provider_call(self):
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def provider(*args, **kwargs):
            calls.append(args)
            started.set()
            await finish.wait()
            yield json.dumps(refined_output())
        with patch.object(server.llm, "stream_venture_text", provider):
            first = asyncio.create_task(self.client.post("/api/manifest", json={"idea": "A compact workbench organizer"}))
            await asyncio.wait_for(started.wait(), 5)
            second = await self.client.post("/api/manifest", json={"idea": "A second workbench organizer"})
            finish.set()
            successful = await first
        self.assertEqual(second.status_code, 429)
        self.assertEqual(len(calls), 1)
        self.assertIn("event: done", successful.text)

    async def test_provider_failure_releases_slot_for_retry(self):
        async def failing(*args, **kwargs):
            raise RuntimeError("provider unavailable")
            yield ""
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())
        with patch.object(server.llm, "stream_venture_text", failing):
            failed = await self.client.post("/api/manifest", json={"idea": "A compact workbench organizer"})
        self.assertIn("event: error", failed.text)
        with patch.object(server.llm, "stream_venture_text", output):
            retry = await self.client.post("/api/manifest", json={"idea": "A compact workbench organizer"})
        self.assertIn("event: done", retry.text)

    async def test_text_timeout_before_or_after_first_token_releases_slot(self):
        for partial in (False, True):
            async def stalled(*args, **kwargs):
                if partial:
                    yield '{"name":"Preview",'
                await asyncio.Event().wait()
                yield ""
            with patch.object(server, "TEXT_TIMEOUT_SECONDS", .01), patch.object(
                server.llm, "stream_venture_text", stalled
            ):
                response = await self.client.post("/api/manifest", json={"idea": "A compact workbench organizer"})
            self.assertIn("two-minute limit", response.text)
            self.assertNotIn("event: done", response.text)
            # Retrying admission proves visitor + global pending slots were released.
            reservation = "timeout-retry-" + str(partial)
            self.assertIsNone(await self.store.reserve_generation(reservation, "ip:127.0.0.1", 1, 1, 1))
            await self.store.release_generation(reservation)

    async def test_cancelled_request_releases_slot(self):
        started = asyncio.Event()
        async def stalled(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
            yield ""
        with patch.object(server.llm, "stream_venture_text", stalled):
            task = asyncio.create_task(self.client.post("/api/manifest", json={"idea": "A compact workbench organizer"}))
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNone(await self.store.reserve_generation("after-cancel", "ip:127.0.0.1", 1, 1, 1))

    async def test_ambiguous_reservation_commit_is_cleaned_up(self):
        original = self.store.reserve_generation
        async def lost_response(*args):
            await original(*args)
            raise RuntimeError("response lost")
        with patch.object(self.store, "reserve_generation", lost_response):
            with self.assertRaisesRegex(RuntimeError, "response lost"):
                await server._reserve_admission("a", global_slot=True)
        self.assertIsNone(await original("retry", "a", 1, 1, 1))

    async def test_ambiguous_success_commit_is_not_refunded_or_duplicated(self):
        original = self.store.create_reserved_venture
        async def lost_response(*args):
            await original(*args)
            raise RuntimeError("commit response lost")
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())
        with patch.object(self.store, "create_reserved_venture", lost_response), \
                patch.object(server.llm, "stream_venture_text", output):
            response = await self.client.post("/api/manifest", json={"idea": "A compact workbench organizer"})
        self.assertIn("event: error", response.text)
        self.assertEqual(await self.store.reserve_generation("again", "ip:127.0.0.1", 1, 1, 1), "hour")
        self.assertEqual(await self.store.count_generations_since("1970-01-01T00:00:00Z"), 1)

    async def test_refinements_on_different_ventures_share_inflight_visitor_cap(self):
        rows = [await self.store.create_venture(venture(f"refine-{i}")) for i in range(2)]
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def provider(*args, **kwargs):
            calls.append(args)
            started.set()
            await finish.wait()
            yield json.dumps(refined_output())
        with patch.object(server.llm, "stream_refinement_text", provider):
            first = asyncio.create_task(self.client.post(
                f"/api/ventures/{rows[0]['slug']}/refine", json={"context": "For compact workshops"}))
            await asyncio.wait_for(started.wait(), 5)
            second = await self.client.post(
                f"/api/ventures/{rows[1]['slug']}/refine", json={"context": "For compact workshops"})
            birth = await self.client.post("/api/manifest", json={"idea": "A compact workbench organizer"})
            finish.set()
            successful = await first
        self.assertEqual(second.status_code, 429)
        self.assertEqual(birth.status_code, 429)
        self.assertEqual(successful.status_code, 200, successful.text)
        self.assertEqual(len(calls), 1)

    async def test_unavailable_refinement_releases_visitor_reservation(self):
        row = await self.store.create_venture(venture("unavailable", images_status="pending"))
        response = await self.client.post(
            f"/api/ventures/{row['slug']}/refine", json={"context": "For compact workshops"})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(await self.store.reserve_generation("new-refine", "ip:127.0.0.1", 1, 1, None))

    async def test_failed_started_refinement_preserves_existing_usage_charge(self):
        row = await self.store.create_venture(venture("failed-refine"))
        async def failing(*args, **kwargs):
            raise RuntimeError("provider unavailable")
            yield ""
        with patch.object(server.llm, "stream_refinement_text", failing):
            failed = await self.client.post(
                f"/api/ventures/{row['slug']}/refine", json={"context": "For compact workshops"})
        self.assertEqual(failed.status_code, 502)
        fresh = await self.store.get_by_id(row["id"])
        self.assertNotIn("_refinement", fresh["grounding"])
        self.assertEqual(await self.store.reserve_generation("retry", "ip:127.0.0.1", 1, 1, None), "hour")

    async def test_cancellation_during_reservation_commit_cleans_known_token(self):
        original = self.store.reserve_generation
        committed, respond = asyncio.Event(), asyncio.Event()
        async def delayed_response(*args):
            result = await original(*args)
            committed.set()
            await respond.wait()
            return result
        with patch.object(self.store, "reserve_generation", delayed_response):
            task = asyncio.create_task(server._reserve_admission("a", global_slot=True))
            await asyncio.wait_for(committed.wait(), 5)
            task.cancel()
            respond.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNone(await original("replacement", "a", 1, 1, 1))

    async def test_cancelled_success_commit_keeps_saved_result_and_charge(self):
        original = self.store.create_reserved_venture
        committed, respond = asyncio.Event(), asyncio.Event()
        async def delayed_response(*args):
            saved = await original(*args)
            committed.set()
            await respond.wait()
            return saved
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())
        with patch.object(self.store, "create_reserved_venture", delayed_response), \
                patch.object(server.llm, "stream_venture_text", output):
            task = asyncio.create_task(self.client.post(
                "/api/manifest", json={"idea": "A compact workbench organizer"}))
            await asyncio.wait_for(committed.wait(), 5)
            task.cancel()
            respond.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await self.store.count_generations_since("1970-01-01T00:00:00Z"), 1)
        self.assertEqual(await self.store.reserve_generation("next", "ip:127.0.0.1", 1, 1, 1), "hour")