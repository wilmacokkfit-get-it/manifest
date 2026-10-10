"""Provider-free disconnect recovery with disposable storage and no images."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import server
from app.store import SQLiteStore, SupabaseStore, ManifestRecoveryUnavailable
from test_refinement import refined_output
import test_generation_admission as quota_fixtures


OWNER = "manifest-recovery-creator-token"
OTHER = "manifest-recovery-stranger-token"
IDEA = "A compact workbench organizer"


class RetirementChecks:
    """Run the same arbitration checks on SQLite and real PostgreSQL."""

    async def retirement_fixture(self):
        request_id = str(uuid.uuid4())
        row = quota_fixtures.venture("retirement-" + request_id)
        row["owner_token"] = OWNER
        fingerprint = server.hashlib.sha256(json.dumps([IDEA, None], ensure_ascii=False).encode()).hexdigest()
        await self.store.claim_manifest_request(OWNER, request_id, fingerprint, row["id"])
        token = str(uuid.uuid4())
        self.assertIsNone(await self.store.reserve_generation(token, token, 100, 100, 100))
        return request_id, token, row

    def expire_request(self, request_id):
        if isinstance(self.store, SQLiteStore):
            self.store._db.execute("UPDATE manifest_requests SET lease_until = ? WHERE request_id = ?",
                                   ("2000-01-01T00:00:00Z", request_id))
            self.store._db.commit()
        else:
            self.sql(f"update manifest_requests set lease_until = '2000-01-01' where request_id = '{request_id}';")

    async def retire(self, request_id, owner=OWNER):
        return await self.client.post(f"/api/manifest/requests/{request_id}/retire",
                                     headers={"Cookie": f"{server.COOKIE}={owner}"})

    async def test_retirement_fences_delayed_commit_and_keeps_id_single_use(self):
        request_id, token, row = await self.retirement_fixture()
        self.expire_request(request_id)
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed_commit():
            started.set()
            await release.wait()
            return await self.store.create_reserved_venture(token, row)
        task = asyncio.create_task(delayed_commit())
        await started.wait()
        try:
            response = await self.retire(request_id)
            self.assertEqual(response.json(), {"state": "retired"})
        finally:
            release.set()
        with self.assertRaisesRegex((RuntimeError, sqlite3.IntegrityError), "retired"):
            await task
        # Unlimited-owner saves take the same fence, without any admission.
        with self.assertRaisesRegex((RuntimeError, sqlite3.IntegrityError), "retired"):
            await self.store.create_venture(row)
        self.assertIsNone(await self.store.get_by_id(row["id"]))
        with patch.object(server.llm, "stream_venture_text", side_effect=AssertionError("No replay")):
            response = await self.client.post("/api/manifest", json={"idea": IDEA, "request_id": request_id})
        self.assertEqual(response.json(), {"state": "retired"})
        self.assertEqual((await self.retire(request_id)).json(), {"state": "retired"})
        self.assertIsNone(await self.store.reserve_generation("new-after-retirement", "new", 1, 1, 100))

    async def test_commit_wins_retirement_recovers_without_refund_even_after_ledger_cleanup(self):
        request_id, token, row = await self.retirement_fixture()
        saved = await self.store.create_reserved_venture(token, row)
        self.expire_request(request_id)
        result = (await self.retire(request_id)).json()
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["venture"]["id"], saved["id"])
        self.assertEqual(await self.store.reserve_generation("no-refund", token, 1, 1, 100), "hour")
        if isinstance(self.store, SQLiteStore):
            self.store._db.execute("delete from generation_admissions")
            self.store._db.commit()
        else:
            self.sql("delete from generation_admissions;")
        self.assertEqual((await self.retire(request_id)).json()["state"], "completed")
        self.assertFalse((await self.store.get_manifest_request(OWNER, request_id))["retired"])

    async def test_only_creator_can_retire_and_live_work_is_not_retired(self):
        request_id, token, row = await self.retirement_fixture()
        self.assertEqual((await self.retire(request_id, OTHER)).status_code, 404)
        self.assertEqual((await self.retire(str(uuid.uuid4()), OTHER)).status_code, 404)
        self.assertEqual((await self.retire(request_id)).json(), {"state": "pending"})
        self.expire_request(request_id)
        self.assertEqual((await self.retire(request_id, OTHER)).status_code, 404)
        saved = await self.store.create_reserved_venture(token, row)
        self.assertEqual(saved["id"], row["id"])

    async def test_concurrent_retirement_and_commit_have_one_durable_winner(self):
        for _ in range(4):
            request_id, token, row = await self.retirement_fixture()
            self.expire_request(request_id)
            results = await asyncio.gather(
                self.store.retire_manifest_request(OWNER, request_id),
                self.store.create_reserved_venture(token, row), return_exceptions=True)
            result = await self.store.manifest_result(await self.store.get_manifest_request(OWNER, request_id))
            saved = await self.store.get_by_id(row["id"])
            if result["state"] == "retired":
                self.assertIsNone(saved)
                self.assertIsInstance(results[1], (RuntimeError, sqlite3.IntegrityError))
                self.assertIn("retired", str(results[1]))
            else:
                self.assertEqual(result["state"], "completed")
                self.assertIsNotNone(saved)
                self.assertNotIsInstance(results[1], Exception)


class ManifestRecoveryTests(RetirementChecks, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="manifest-recovery-")
        self.store = SQLiteStore(self.directory.name)
        self.patches = [
            patch.object(server, "store", self.store),
            patch.object(server, "is_app_owner", AsyncMock(return_value=False)),
            patch.object(server, "app_owner_token", AsyncMock(return_value=None)),
            patch.object(server.llm, "image_provider", return_value="none"),
            patch.object(server.llm, "generate_render", AsyncMock(side_effect=AssertionError("Images disabled"))),
            patch.object(server, "GEN_PER_HOUR", 1),
            patch.object(server, "GEN_PER_DAY", 1),
            patch.object(server, "GLOBAL_PER_DAY", 1),
        ]
        for p in self.patches:
            p.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
            base_url="https://manifest.test", headers={"Cookie": f"{server.COOKIE}={OWNER}"})
        self.request_id = str(uuid.uuid4())
        self.body = {"idea": IDEA, "request_id": self.request_id}

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self.store._db.close()
        self.directory.cleanup()

    async def lookup(self, owner=OWNER):
        return await self.client.get(f"/api/manifest/requests/{self.request_id}",
            headers={"Cookie": f"{server.COOKIE}={owner}"})

    async def output(self, *args, **kwargs):
        yield json.dumps(refined_output())

    async def test_retirement_survives_reopening_and_fences_a_separate_connection(self):
        request_id, token, row = await self.retirement_fixture()
        late_worker = SQLiteStore(self.directory.name)
        self.expire_request(request_id)
        try:
            self.assertEqual((await self.retire(request_id)).json()["state"], "retired")
            reopened = SQLiteStore(self.directory.name)
            try:
                record = await reopened.get_manifest_request(OWNER, request_id)
                self.assertEqual((await reopened.manifest_result(record))["state"], "retired")
            finally:
                reopened._db.close()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "retired"):
                await late_worker.create_reserved_venture(token, row)
            admission = late_worker._db.execute(
                "select consumed from generation_admissions where token = ?", (token,)).fetchone()
            self.assertEqual(admission["consumed"], 0)
        finally:
            late_worker._db.close()

    async def test_disconnect_before_text_and_during_stream_releases_only_pending_charge(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                self.request_id = str(uuid.uuid4())
                self.body["request_id"] = self.request_id
                started = asyncio.Event()
                async def stream(*args, **kwargs):
                    if partial:
                        yield '{"name":"Preview","tagline":"'
                    started.set()
                    await asyncio.Event().wait()
                    yield ""
                with patch.object(server.llm, "stream_venture_text", stream):
                    task = asyncio.create_task(self.client.post("/api/manifest", json=self.body))
                    await asyncio.wait_for(started.wait(), 5)
                    pending = await self.lookup()
                    self.assertEqual(pending.json(), {"state": "pending"})
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                recovered = await self.lookup()
                self.assertEqual(recovered.json(), {"state": "failed"})
                self.assertEqual(await self.store.count_generations_since("1970-01-01T00:00:00Z"), 0)
                with patch.object(server.llm, "stream_venture_text", side_effect=AssertionError("No replay")):
                    replay = await self.client.post("/api/manifest", json=self.body)
                self.assertEqual(replay.json(), {"state": "failed"})
                self.assertIsNone(await self.store.reserve_generation("retry", "ip:127.0.0.1", 1, 1, 1))
                await self.store.release_generation("retry")

    async def test_disconnect_immediately_after_atomic_save_recovers_without_refund_or_replay(self):
        original = self.store.create_reserved_venture
        committed, respond = asyncio.Event(), asyncio.Event()
        async def lost_response(*args):
            saved = await original(*args)
            committed.set()
            await respond.wait()
            return saved
        with patch.object(self.store, "create_reserved_venture", lost_response), \
                patch.object(server.llm, "stream_venture_text", self.output):
            task = asyncio.create_task(self.client.post("/api/manifest", json=self.body))
            await asyncio.wait_for(committed.wait(), 5)
            # Lookup works while the save's response is still lost.
            recovered = await self.lookup()
            self.assertEqual(recovered.json()["state"], "completed")
            task.cancel()
            respond.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        result = (await self.lookup()).json()
        self.assertEqual(result["state"], "completed")
        self.assertEqual(recovered.json()["venture"]["id"], result["venture"]["id"])
        self.assertEqual(result["venture"]["images_status"], "failed")
        self.assertEqual(await self.store.reserve_generation("next", "ip:127.0.0.1", 1, 1, 1), "hour")
        with patch.object(server.llm, "stream_venture_text", side_effect=AssertionError("No replay")):
            replay = await self.client.post("/api/manifest", json=self.body)
        self.assertEqual(replay.json()["venture"]["id"], result["venture"]["id"])
        self.assertEqual(await self.store.count_generations_since("1970-01-01T00:00:00Z"), 1)
        # Request recovery survives process-local store replacement.
        reopened = SQLiteStore(self.directory.name)
        try:
            record = await reopened.get_manifest_request(OWNER, self.request_id)
            self.assertEqual((await reopened.manifest_result(record))["state"], "completed")
        finally:
            reopened._db.close()

    async def test_ambiguous_save_error_and_expired_lease_still_find_success(self):
        original = self.store.create_reserved_venture
        async def lost_response(*args):
            await original(*args)
            raise RuntimeError("save response lost")
        with patch.object(self.store, "create_reserved_venture", lost_response), \
                patch.object(server.llm, "stream_venture_text", self.output):
            response = await self.client.post("/api/manifest", json=self.body)
        self.assertIn("event: error", response.text)
        self.store._db.execute("UPDATE manifest_requests SET lease_until = '2000-01-01T00:00:00Z'")
        self.store._db.commit()
        self.assertEqual((await self.lookup()).json()["state"], "completed")
        self.assertEqual(await self.store.reserve_generation("next", "ip:127.0.0.1", 1, 1, 1), "hour")

    async def test_concurrent_duplicate_requests_including_unlimited_owner_call_provider_once(self):
        with patch.object(server, "is_app_owner", AsyncMock(return_value=True)):
            started, finish = asyncio.Event(), asyncio.Event()
            calls = []
            async def output(*args, **kwargs):
                calls.append(args)
                started.set()
                await finish.wait()
                yield json.dumps(refined_output())
            with patch.object(server.llm, "stream_venture_text", output):
                first = asyncio.create_task(self.client.post("/api/manifest", json=self.body))
                await asyncio.wait_for(started.wait(), 5)
                replay = await self.client.post("/api/manifest", json=self.body)
                self.assertEqual(replay.json(), {"state": "pending"})
                finish.set()
                response = await first
            self.assertIn("event: done", response.text)
            self.assertEqual(len(calls), 1)
            self.assertEqual((await self.lookup()).json()["state"], "completed")

    async def test_lookup_is_creator_only_even_when_result_is_public(self):
        with patch.object(server.llm, "stream_venture_text", self.output):
            await self.client.post("/api/manifest", json=self.body)
        recovered = await self.lookup()
        result = recovered.json()["venture"]
        self.assertIn("no-store", recovered.headers["cache-control"])
        self.assertEqual((await self.lookup(OTHER)).status_code, 404)
        await self.store.update_venture(result["id"], {"visibility": "public"})
        stranger = await self.lookup(OTHER)
        self.assertEqual(stranger.status_code, 404)
        self.assertNotIn(result["name"], stranger.text)
        missing = await self.client.get(f"/api/manifest/requests/{uuid.uuid4()}",
            headers={"Cookie": f"{server.COOKIE}={OTHER}"})
        self.assertEqual(stranger.json(), missing.json())

    async def test_owner_login_keeps_browser_cookie_request_recovery(self):
        with patch.object(server.llm, "stream_venture_text", self.output):
            await self.client.post("/api/manifest", json=self.body)
        with patch.object(server, "app_owner_token", AsyncMock(return_value="verified-app-owner")):
            self.assertEqual((await self.lookup()).json()["state"], "completed")
            with patch.object(server.llm, "stream_venture_text", side_effect=AssertionError("No replay")):
                self.assertEqual((await self.client.post("/api/manifest", json=self.body)).json()["state"], "completed")

    async def test_request_id_cannot_change_sentence_or_mix(self):
        with patch.object(server.llm, "stream_venture_text", self.output):
            await self.client.post("/api/manifest", json=self.body)
        for changes in ({"idea": "A different product for workshops"}, {"mix_id": "other-mix"}):
            with patch.object(server.llm, "stream_venture_text", side_effect=AssertionError("No replay")):
                response = await self.client.post("/api/manifest", json={**self.body, **changes})
            self.assertEqual(response.status_code, 409)

    async def test_crashed_expired_claim_never_restarts_or_reports_definite_failure(self):
        fingerprint = server.hashlib.sha256(json.dumps([IDEA, None], ensure_ascii=False).encode()).hexdigest()
        await self.store.claim_manifest_request(OWNER, self.request_id, fingerprint, str(uuid.uuid4()))
        self.store._db.execute("UPDATE manifest_requests SET lease_until = '2000-01-01T00:00:00Z'")
        self.store._db.commit()
        self.assertEqual((await self.lookup()).json(), {"state": "unconfirmed"})
        with patch.object(server.llm, "stream_venture_text", side_effect=AssertionError("No replay")):
            self.assertEqual((await self.client.post("/api/manifest", json=self.body)).json(), {"state": "unconfirmed"})

    async def test_missing_remote_migration_fails_explicitly_before_generation_or_quota(self):
        with patch.object(self.store, "get_manifest_request",
                          side_effect=ManifestRecoveryUnavailable("Apply supabase/manifest-recovery.sql")), \
                patch.object(server.llm, "stream_venture_text", side_effect=AssertionError("No generation")):
            for response in (await self.lookup(), await self.client.post("/api/manifest", json=self.body)):
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "manifest_recovery_unavailable")
                self.assertIn("manifest-recovery.sql", response.json()["error"])
        self.assertEqual(self.store._db.execute("select count(*) from generation_admissions").fetchone()[0], 0)

    async def test_legacy_generation_preserved_without_recovery_schema(self):
        with patch.object(self.store, "get_manifest_request",
                          side_effect=ManifestRecoveryUnavailable("Missing recovery table")), \
                patch.object(self.store, "claim_manifest_request", side_effect=AssertionError("No claim")), \
                patch.object(self.store, "fail_manifest_request", side_effect=AssertionError("No cleanup")), \
                patch.object(server.llm, "stream_venture_text", self.output):
            response = await self.client.post("/api/manifest", json={"idea": IDEA})
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: done", response.text)
        self.assertIn('"recovery_supported": false', response.text)
        self.assertEqual(await self.store.count_generations_since("1970-01-01T00:00:00Z"), 1)
        self.assertEqual(await self.store.reserve_generation("next", "ip:127.0.0.1", 1, 1, 1), "hour")


class SupabaseRecoveryAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_table_and_permissions_have_explicit_setup_error(self):
        store = SupabaseStore("https://recovery-fixture.invalid", "fixture-only")
        await store._client.aclose()
        for code in ("PGRST205", "42P01", "42501"):
            with self.subTest(code=code):
                store._client = httpx.AsyncClient(base_url=store.url, transport=httpx.MockTransport(
                    lambda request: httpx.Response(404, json={"code": code})))
                try:
                    with self.assertRaisesRegex(ManifestRecoveryUnavailable, "manifest-recovery.sql"):
                        await store.get_manifest_request(OWNER, str(uuid.uuid4()))
                finally:
                    await store._client.aclose()

    async def test_owner_scoped_queries_and_conflict_safe_insert(self):
        store = SupabaseStore("https://recovery-fixture.invalid", "fixture-only")
        request_id = str(uuid.uuid4())
        row = {"owner_token": OWNER, "request_id": request_id, "venture_id": str(uuid.uuid4()),
               "fingerprint": "hash", "lease_until": "2999-01-01T00:00:00Z", "failed": False}
        seen = []
        def transport(request):
            seen.append(request)
            if request.method == "POST":
                self.assertEqual(request.url.params["on_conflict"], "owner_token,request_id")
                self.assertIn("resolution=ignore-duplicates", request.headers["prefer"])
                body = json.loads(request.content)
                self.assertEqual(body["owner_token"], OWNER)
                return httpx.Response(201, json=[row] if len(seen) == 1 else [])
            self.assertEqual(request.url.params["owner_token"], f"eq.{OWNER}")
            self.assertEqual(request.url.params["request_id"], f"eq.{request_id}")
            return httpx.Response(200, json=[row]) if request.method == "GET" else httpx.Response(204)
        await store._client.aclose()
        store._client = httpx.AsyncClient(base_url=store.url, transport=httpx.MockTransport(transport))
        try:
            self.assertTrue(await store.claim_manifest_request(OWNER, request_id, "hash", row["venture_id"]))
            self.assertFalse(await store.claim_manifest_request(OWNER, request_id, "hash", str(uuid.uuid4())))
            self.assertEqual(await store.get_manifest_request(OWNER, request_id), row)
            await store.fail_manifest_request(OWNER, request_id)
        finally:
            await store._client.aclose()


class PostgresManifestRecoveryTests(RetirementChecks, unittest.IsolatedAsyncioTestCase):
    """Real disposable PG transactions behind the Supabase HTTP adapter."""

    # Reuse the existing isolated-cluster bootstrap (no environment credentials).
    sql = quota_fixtures.PostgresAdmissionTests.__dict__["sql"]
    cleanup = quota_fixtures.PostgresAdmissionTests.__dict__["cleanup"]

    @classmethod
    def setUpClass(cls):
        quota_fixtures.PostgresAdmissionTests.setUpClass.__func__(cls)
        cls.sql(Path("supabase/manifest-recovery.sql").read_text())

    async def asyncSetUp(self):
        self.sql("truncate manifest_requests, generation_admissions, signals, ventures;")
        self.store = SupabaseStore("https://recovery-pg-fixture.invalid", "fixture-only")
        await self.store._client.aclose()
        self.store._client = httpx.AsyncClient(base_url=self.store.url,
            transport=httpx.MockTransport(self.transport))
        self.patches = [
            patch.object(server, "store", self.store),
            # Slug minting uses a separate synchronous HTTP client. Keep that
            # path on the disposable database too, never the public network.
            patch.object(self.store, "slug_exists", side_effect=lambda slug:
                self.sql("select exists(select 1 from ventures where slug = '" +
                         slug.replace("'", "''") + "');") == "t"),
            patch.object(server, "is_app_owner", AsyncMock(return_value=False)),
            patch.object(server, "app_owner_token", AsyncMock(return_value=None)),
            patch.object(server.llm, "image_provider", return_value="none"),
            patch.object(server.llm, "generate_render", AsyncMock(side_effect=AssertionError("Images disabled"))),
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
        await self.store._client.aclose()
        for p in reversed(self.patches):
            p.stop()

    async def transport(self, request):
        if request.url.path.endswith("/rpc/retire_manifest_request"):
            body = json.loads(request.content)
            owner = body["p_owner"].replace("'", "''")
            request_id = body["p_request_id"].replace("'", "''")
            output = await asyncio.to_thread(self.sql,
                f"set role service_role; select public.retire_manifest_request('{owner}', '{request_id}');")
            return httpx.Response(200, json=json.loads(output))
        if "/rpc/" in request.url.path:
            return await quota_fixtures.PostgresAdmissionTests.transport(self, request)
        def quote(value):
            if value is None:
                return "NULL"
            if isinstance(value, bool):
                return "true" if value else "false"
            return "'" + str(value).replace("'", "''") + "'"
        table = request.url.path.rsplit("/", 1)[1]
        self.assertIn(table, ("manifest_requests", "ventures"))
        if request.method == "POST":
            body = json.loads(request.content)
            if table == "ventures":
                # Exercise the real direct-insert path used by unlimited owners.
                values = [("'" + json.dumps(v).replace("'", "''") + "'::jsonb")
                          if isinstance(v, (dict, list)) else quote(v) for v in body.values()]
                try:
                    output = await asyncio.to_thread(self.sql,
                        f"set role service_role; with rows as (insert into ventures ({','.join(body)}) "
                        f"values ({','.join(values)}) returning *) select jsonb_agg(to_jsonb(rows)) from rows;")
                    return httpx.Response(201, json=json.loads(output))
                except RuntimeError as exc:
                    return httpx.Response(400, json={"message": str(exc)})
            self.assertEqual(set(body), {"owner_token", "request_id", "fingerprint", "venture_id", "lease_until"})
            query = (
                f"with rows as (insert into manifest_requests ({','.join(body)}) values "
                f"({','.join(quote(v) for v in body.values())}) "
                "on conflict(owner_token, request_id) do nothing returning *) "
                "select coalesce(jsonb_agg(to_jsonb(rows)), '[]'::jsonb) from rows;")
        else:
            filters = []
            for key, value in request.url.params.items():
                if key in ("limit", "select", "order"):
                    continue
                self.assertIn(key, ("owner_token", "request_id", "id"))
                self.assertTrue(value.startswith("eq."))
                filters.append(f"{key} = {quote(value[3:])}")
            self.assertTrue(filters)
            where = " and ".join(filters)
            if request.method == "PATCH":
                self.assertEqual(table, "manifest_requests")
                self.assertEqual(json.loads(request.content), {"failed": True})
                query = f"update manifest_requests set failed = true where {where}; select 'null';"
            else:
                self.assertEqual(request.method, "GET")
                query = (f"select coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb) "
                         f"from {table} t where {where};")
        output = await asyncio.to_thread(self.sql, "set role service_role; " + query)
        return httpx.Response(200, json=json.loads(output))

    async def test_disconnect_stages_recover_against_real_postgres_atomic_save(self):
        for stage in ("before-text", "streaming", "saved"):
            with self.subTest(stage=stage):
                self.sql("truncate manifest_requests, generation_admissions, signals, ventures;")
                request_id = str(uuid.uuid4())
                body = {"idea": IDEA, "request_id": request_id}
                started, respond = asyncio.Event(), asyncio.Event()
                original = self.store.create_reserved_venture
                async def stream(*args, **kwargs):
                    if stage == "saved":
                        yield json.dumps(refined_output())
                    else:
                        if stage == "streaming":
                            yield '{"name":"Preview","tagline":"'
                        started.set()
                        await asyncio.Event().wait()
                        yield ""
                async def delayed_save(*args):
                    saved = await original(*args)
                    started.set()
                    await respond.wait()
                    return saved
                with patch.object(server.llm, "stream_venture_text", stream), \
                        patch.object(self.store, "create_reserved_venture", delayed_save):
                    task = asyncio.create_task(self.client.post("/api/manifest", json=body))
                    waiting = asyncio.create_task(started.wait())
                    completed, _ = await asyncio.wait((waiting, task), timeout=5,
                                                       return_when=asyncio.FIRST_COMPLETED)
                    if waiting not in completed:
                        waiting.cancel()
                        if task.done():
                            self.fail(f"Generation ended before the fixture stage: {(await task).text}")
                        task.cancel()
                        self.fail("Generation did not reach the fixture stage in five seconds.")
                    task.cancel()
                    respond.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                lookup = await self.client.get(f"/api/manifest/requests/{request_id}")
                state = "completed" if stage == "saved" else "failed"
                self.assertEqual(lookup.json()["state"], state)
                self.assertEqual(self.sql("select count(*) from ventures;"), "1" if stage == "saved" else "0")
                reason = await self.store.reserve_generation("next", "ip:127.0.0.1", 1, 1, 1)
                self.assertEqual(reason, "hour" if stage == "saved" else None)
                with patch.object(server.llm, "stream_venture_text", side_effect=AssertionError("No replay")):
                    self.assertEqual((await self.client.post("/api/manifest", json=body)).json()["state"], state)
                stranger = await self.client.get(f"/api/manifest/requests/{request_id}",
                    headers={"Cookie": f"{server.COOKIE}={OTHER}"})
                self.assertEqual(stranger.status_code, 404)
                if stage == "saved":
                    # Quota records can expire without losing request identity.
                    self.sql("delete from generation_admissions;")
                    self.assertEqual((await self.client.get(
                        f"/api/manifest/requests/{request_id}")).json()["state"], "completed")

    async def test_concurrent_request_claims_and_browser_role_permissions(self):
        request_id = str(uuid.uuid4())
        claims = await asyncio.gather(*[
            self.store.claim_manifest_request(OWNER, request_id, "hash", str(uuid.uuid4()))
            for _ in range(6)])
        self.assertEqual(claims.count(True), 1)
        self.assertEqual(self.sql("select count(*) from manifest_requests;"), "1")
        for role in ("anon", "authenticated"):
            for query in ("select * from manifest_requests",
                          "update manifest_requests set failed = true",
                          "delete from manifest_requests",
                          "insert into manifest_requests(owner_token,request_id,fingerprint,venture_id,lease_until) "
                          f"values ('x','y','h','{uuid.uuid4()}', now())",
                          "select retire_manifest_request('x','y')",
                          "select fence_retired_manifest()"):
                with self.assertRaisesRegex(RuntimeError, "permission denied"):
                    self.sql(f"set role {role}; {query};")
