import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import test_regressions as fixtures
import server
from app import llm, step_assistance
from app.store import SupabaseStore


def result():
    return {
        "summary": "Prepared an unsent, private review brief.",
        "deliverables": [{"title": "Review brief", "content":
                          "CONFIDENTIAL DRAFT: Please review this nonfunctional concept and identify evidence needed before any physical work."}],
        "human_handoff": ["A qualified specialist must review the actual design."],
        "limitations": ["No current sources or suppliers have been verified."],
    }


class StepAssistanceTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixtures.ServerRegressionTests.asyncTearDown
    client = fixtures.ServerRegressionTests.client

    async def asyncSetUp(self):
        await fixtures.ServerRegressionTests.asyncSetUp(self)
        row = fixtures.private_venture()
        row["path"] = {"tier": 0, "tier_label": "The Lean Build", "steps": [
            {"n": 1, "title": "Request review", "why": "Validate before building",
             "how": ["Prepare a nonfunctional brief"], "status": "todo", "note": "Keep my unusual mechanism"},
            {"n": 2, "title": "Interview buyers", "status": "todo"},
        ]}
        self.row = await self.store.create_venture(row)
        self.url = f"/api/ventures/{row['slug']}/path/steps/1/assist"
        self.headers = {"Cookie": f"{server.COOKIE}=owner-token-for-test"}

    async def output(self, *args, **kwargs):
        yield json.dumps(result())

    async def approve(self, client, n=1):
        return await client.post(self.url.replace("/steps/1/", f"/steps/{n}/"),
                                 json={"approve": True}, headers=self.headers)

    async def test_yes_prepares_actual_private_work_once_without_marking_step_done(self):
        calls = []
        async def output(*args):
            calls.append(args)
            yield json.dumps(result())
        with patch.object(llm, "stream_step_work", output):
            async with self.client() as client:
                response = await self.approve(client)
                replay = await self.approve(client)
        self.assertIn("event: done", response.text)
        self.assertIn("event: done", replay.text)
        self.assertEqual(len(calls), 1)
        fresh = await self.store.get_by_id(self.row["id"])
        step = fresh["path"]["steps"][0]
        self.assertEqual(step["status"], "todo")
        self.assertEqual(step["note"], "Keep my unusual mechanism")
        self.assertEqual(step["automation"]["state"], "ready")
        self.assertIn("CONFIDENTIAL DRAFT", step["automation"]["result"]["deliverables"][0]["content"])
        self.assertNotIn("automation", server._path_view(fresh["path"], False)["steps"][0])
        self.assertIn("automation", server._path_view(fresh["path"], True)["steps"][0])

    async def test_no_does_not_call_provider_and_can_be_reconsidered(self):
        def forbidden(*args):
            raise AssertionError("No must not call a model")
        async with self.client() as client:
            with patch.object(llm, "stream_step_work", forbidden):
                response = await client.post(self.url, json={"approve": False}, headers=self.headers)
            self.assertEqual(response.json()["path"]["steps"][0]["automation"]["state"], "declined")
            with patch.object(llm, "stream_step_work", self.output):
                response = await self.approve(client)
            self.assertIn("event: done", response.text)

    async def test_each_step_has_independent_preparation(self):
        with patch.object(llm, "stream_step_work", self.output):
            async with self.client() as client:
                for n in (1, 2):
                    self.assertIn("event: done", (await self.approve(client, n)).text)
        steps = (await self.store.get_by_id(self.row["id"]))["path"]["steps"]
        self.assertTrue(all(s["automation"]["state"] == "ready" for s in steps))
        self.assertTrue(all(s["status"] == "todo" for s in steps))

    async def test_public_viewer_cannot_approve_private_preparation(self):
        await self.store.update_venture(self.row["id"], {"visibility": "public"})
        def forbidden(*args):
            raise AssertionError("Viewer must not invoke the provider")
        with patch.object(llm, "stream_step_work", forbidden):
            async with self.client() as client:
                response = await client.post(self.url, json={"approve": True})
        self.assertEqual(response.status_code, 403)

    async def test_same_step_cannot_start_twice_and_notes_survive_completion(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def output(*args):
            calls.append(args)
            entered.set()
            await release.wait()
            yield json.dumps(result())
        with patch.object(llm, "stream_step_work", output):
            async with self.client() as client:
                first = asyncio.create_task(self.approve(client))
                await asyncio.wait_for(entered.wait(), 5)
                second = await self.approve(client)
                self.assertIn("already being prepared", second.text)
                note = await client.patch(self.url.removesuffix("/assist"), json={"note": "A concurrent note"}, headers=self.headers)
                self.assertEqual(note.status_code, 200)
                release.set()
                self.assertIn("event: done", (await first).text)
        self.assertEqual(len(calls), 1)
        step = (await self.store.get_by_id(self.row["id"]))["path"]["steps"][0]
        self.assertEqual(step["note"], "A concurrent note")
        self.assertEqual(step["automation"]["state"], "ready")

    async def test_reset_fences_out_late_result(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def output(*args):
            entered.set()
            await release.wait()
            yield json.dumps(result())
        with patch.object(llm, "stream_step_work", output):
            async with self.client() as client:
                first = asyncio.create_task(self.approve(client))
                await asyncio.wait_for(entered.wait(), 5)
                await self.store.reset_path(self.row["id"])
                release.set()
                response = await first
        self.assertNotIn("event: done", response.text)
        self.assertIsNone((await self.store.get_by_id(self.row["id"]))["path"])

    async def test_disconnect_releases_running_state(self):
        entered = asyncio.Event()
        async def output(*args):
            entered.set()
            await asyncio.Event().wait()
            yield ""
        with patch.object(llm, "stream_step_work", output):
            async with self.client() as client:
                first = asyncio.create_task(self.approve(client))
                await asyncio.wait_for(entered.wait(), 5)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
        step = (await self.store.get_by_id(self.row["id"]))["path"]["steps"][0]
        self.assertEqual(step["automation"]["state"], "failed")
        self.assertEqual(step["status"], "todo")

    async def test_bad_output_fails_before_error_and_retries_are_bounded(self):
        async def bad(*args):
            yield '{"summary": "incomplete"}'
        with patch.object(llm, "stream_step_work", bad):
            async with self.client() as client:
                for _ in range(3):
                    response = await self.approve(client)
                    self.assertIn("event: error", response.text)
                    step = (await self.store.get_by_id(self.row["id"]))["path"]["steps"][0]
                    self.assertEqual(step["automation"]["state"], "failed")
                fourth = await self.approve(client)
        self.assertIn("three preparation attempts", fourth.text)
        self.assertEqual(step["status"], "todo")

    async def test_timeout_and_expired_claim_are_retryable(self):
        async def slow(*args):
            await asyncio.sleep(1)
            yield json.dumps(result())
        with patch.object(llm, "stream_step_work", slow), patch.object(server, "TEXT_TIMEOUT_SECONDS", .03):
            async with self.client() as client:
                response = await self.approve(client)
        self.assertIn("two-minute limit", response.text)
        path = deepcopy((await self.store.get_by_id(self.row["id"]))["path"])
        path["steps"][0]["automation"] = {
            "state": "running", "claim_id": "old", "lease_until": "2000-01-01T00:00:00Z", "attempts": 1,
        }
        await self.store.update_venture(self.row["id"], {"path": path})
        self.assertEqual(server._path_view(path, True)["steps"][0]["automation"]["state"], "failed")
        with patch.object(llm, "stream_step_work", self.output):
            async with self.client() as client:
                self.assertIn("event: done", (await self.approve(client)).text)

    async def test_verified_app_owner_is_exempt_from_preparation_attempt_limit(self):
        path = deepcopy(self.row["path"])
        path["steps"][0]["automation"] = {"state": "failed", "attempts": 3}
        await self.store.update_venture(self.row["id"], {"path": path})
        with patch.object(server, "is_app_owner", return_value=True), patch.object(llm, "stream_step_work", self.output):
            async with self.client() as client:
                self.assertIn("event: done", (await self.approve(client)).text)

    async def test_cas_retry_uses_cached_result_instead_of_starting_duplicate_work(self):
        compare = self.store.compare_path
        raced = False
        async def other_tab_finished(venture_id, old, new):
            nonlocal raced
            if not raced:
                raced = True
                replacement = deepcopy(new)
                replacement["steps"][0]["automation"] = {"state": "ready", "result": result(), "attempts": 1}
                await compare(venture_id, old, replacement)
                return None
            return await compare(venture_id, old, new)
        def forbidden(*args):
            raise AssertionError("A retry discovered completed work; no duplicate model call")
        with patch.object(self.store, "compare_path", other_tab_finished), patch.object(llm, "stream_step_work", forbidden):
            async with self.client() as client:
                response = await self.approve(client)
        self.assertIn("event: done", response.text)
        self.assertNotIn("event: error", response.text)

    async def test_old_snapshot_cannot_restore_reset_or_regenerated_path(self):
        old = deepcopy(self.row["path"])
        edited = deepcopy(old)
        edited["steps"][0]["note"] = "A stale edit"
        await self.store.reset_path(self.row["id"])
        self.assertIsNone(await self.store.compare_path(self.row["id"], old, edited))
        regenerated = {**old, "_revision": "new-path-identity"}
        await self.store.update_venture(self.row["id"], {"path": regenerated})
        self.assertIsNone(await self.store.compare_path(self.row["id"], old, edited))


class SupabaseCASTests(unittest.IsolatedAsyncioTestCase):
    async def test_path_updates_are_snapshot_fenced_without_new_rpc_or_schema(self):
        from unittest.mock import AsyncMock
        store = SupabaseStore.__new__(SupabaseStore)
        old = {"steps": [{"n": 1, "automation": {"claim_id": "first"}}]}
        new = {"steps": [{"n": 1, "automation": {"claim_id": "second"}}]}
        store._rest = AsyncMock(return_value=[{"id": "test-only", "path": new}])
        saved = await store.compare_path("test-only", old, new)
        self.assertEqual(saved["path"], new)
        args = store._rest.call_args
        self.assertEqual(args.args, ("PATCH", "ventures"))
        self.assertEqual(args.kwargs["params"]["path"], "not.is.null")
        self.assertEqual(args.kwargs["params"]["path->>_revision"], "is.null")
        submitted = args.kwargs["json_body"]["path"]
        self.assertEqual(submitted["steps"], new["steps"])
        self.assertTrue(submitted["_revision"])
        store._rest.return_value = []
        old["_revision"] = "previous-revision"
        old["large_deliverable"] = "content" * 10000
        self.assertIsNone(await store.compare_path("test-only", old, new))
        params = store._rest.call_args.kwargs["params"]
        self.assertEqual(params["path->>_revision"], "eq.previous-revision")
        self.assertLess(len(json.dumps(params)), 200)


class PacketTests(unittest.TestCase):
    def test_untrusted_completion_and_html_remain_data_not_capabilities(self):
        data = result()
        data["done"] = True
        data["email_sent"] = True
        parsed = step_assistance.packet(json.dumps(data))
        self.assertNotIn("done", parsed)
        self.assertNotIn("email_sent", parsed)
        self.assertIn("No messages were sent", parsed["limitations"][0])

    def test_unsupported_guarantee_is_rejected(self):
        data = result()
        data["summary"] = "This product is guaranteed safe."
        with self.assertRaises(ValueError):
            step_assistance.packet(json.dumps(data))
