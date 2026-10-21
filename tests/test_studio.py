"""Disposable Studio tests. Fixtures never enter a production app or provider."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

_IMPORT_DATA = tempfile.TemporaryDirectory(prefix="manifest-studio-import-")
with patch.dict(os.environ, {"STORE": "sqlite", "MANIFEST_DATA_DIR": _IMPORT_DATA.name,
                             "SUPABASE_URL": "", "SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": ""}):
    import server
from app import studio
from app.store import SQLiteStore, SupabaseStore

HTML = """<!DOCTYPE html><html><head><style>body{background:#111;color:white}</style></head>
<body><h1>Test fixture only</h1><button id="add">Increase</button><output id="n">0</output>
<script>let n=0;document.querySelector('#add').onclick=()=>document.querySelector('#n').textContent=++n;</script></body></html>"""
MATCHES = [{"name": f"Fixture tool {i}", "summary": "A search fixture, not real research.",
            "url": f"https://example.test/tool-{i}", "source_url": f"https://example.test/tool-{i}"} for i in range(3)]
DIRECTIONS = [{"id": key, "title": f"Direction {key}", "pitch": f"Pitch {key}.",
               "difference": f"Specific difference {key}."} for key in "abc"]


class DocumentTests(unittest.TestCase):
    def test_complete_inline_app_gets_policy_before_scripts(self):
        html = studio.validate_html(HTML)
        self.assertLess(html.index("data-manifest-policy"), html.index("<style>"))
        self.assertIn("connect-src 'none'", html)
        self.assertEqual(studio.validate_html(html).count("data-manifest-policy"), 1)

    def test_fenced_document_is_normalized(self):
        self.assertTrue(studio.validate_html("```html\n" + HTML + "\n```").startswith("<!DOCTYPE html>"))

    def test_policy_is_inserted_in_the_real_head_not_a_comment(self):
        source = HTML.replace("<head>", "<!-- fake <head> --> <head>")
        rendered = studio.validate_html(source)
        actual_head = rendered.index("<head>", rendered.index("-->"))
        policy = rendered.index("data-manifest-policy")
        self.assertGreater(policy, actual_head)
        self.assertLess(policy, rendered.index("<style>"))

    def test_incomplete_external_or_navigation_documents_fail(self):
        bad = [
            HTML[:-7], "", None,
            HTML.replace("<style>", '<script src="https://example.test/a.js"></script><style>'),
            HTML.replace("<style>", '<meta http-equiv="refresh" content="0;url=https://example.test"><style>'),
            HTML.replace("<style>", '<iframe srcdoc="x"></iframe><style>'),
            HTML.replace("<style>", '<link href="https://example.test/a.css"><style>'),
            HTML.replace("<style>", '<base href="/"><style>'),
            HTML.replace("</body>", '<a href="https://example.test">Leave</a></body>'),
            HTML.replace("<head>", '<script>window.test=1</script><head>'),
            '<!DOCTYPE html><!--<html><head></head><body></body></html>--></html>',
        ]
        for html in bad:
            with self.subTest(html=str(html)[:70]), self.assertRaises(ValueError):
                studio.validate_html(html)

    def test_version_cap_undo_and_branch(self):
        p = {"versions": [], "cursor": -1}
        for i in range(7):
            p = studio.append_version(p, HTML, f"change-{i}")
        self.assertEqual([v["label"] for v in p["versions"]], [f"change-{i}" for i in range(2, 7)])
        p["cursor"] = 1
        p = studio.append_version(p, HTML, "new branch")
        self.assertEqual([v["label"] for v in p["versions"]], ["change-2", "change-3", "new branch"])
        self.assertEqual(p["cursor"], 2)

    def test_no_mock_provider_or_canned_app_contract(self):
        self.assertIn("No crypto", studio.APP_RULES)
        self.assertIn("wallets", studio.APP_RULES)
        self.assertIn("self-replication", studio.APP_RULES)
        self.assertIn('sandbox="allow-scripts"', studio.APP_RULES)


class ProviderValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_scout_rejects_model_only_links(self):
        response = SimpleNamespace(output_text=json.dumps({"matches": MATCHES}),
                                   model_dump=lambda: {"output": []})
        with patch.object(studio, "_response", AsyncMock(return_value=response)):
            with self.assertRaisesRegex(ValueError, "citation metadata"):
                await studio.scout("A tiny counter app")

    async def test_scout_accepts_only_search_returned_links(self):
        response = SimpleNamespace(output_text=json.dumps({"matches": MATCHES}),
                                   model_dump=lambda: {"output": [{"action": {"sources": [
                                       {"url": m["source_url"], "title": m["name"]} for m in MATCHES]}}]})
        with patch.object(studio, "_response", AsyncMock(return_value=response)) as call:
            result = await studio.scout("A tiny counter app")
            self.assertEqual(len(result["matches"]), 3)
            self.assertTrue(call.call_args.kwargs["search"])
            forged = copy.deepcopy(MATCHES)
            forged[0]["source_url"] = "https://invented.test/"
            response.output_text = json.dumps({"matches": forged})
            with self.assertRaises(ValueError):
                await studio.scout("A tiny counter app")

    async def test_diverge_requires_three_distinct_choices(self):
        response = SimpleNamespace(output_text=json.dumps({"directions": DIRECTIONS[:2]}))
        with patch.object(studio, "_response", AsyncMock(return_value=response)):
            with self.assertRaises(ValueError):
                await studio.diverge({"idea": "test idea", "payload": {"matches": MATCHES}})

    async def test_diverge_tolerates_a_minor_model_length_overrun_without_truncation(self):
        directions = copy.deepcopy(DIRECTIONS)
        directions[1]["difference"] = "A comparison with a real mechanism. " * 20
        response = SimpleNamespace(output_text=json.dumps({"directions": directions}))
        with patch.object(studio, "_response", AsyncMock(return_value=response)) as call:
            result = await studio.diverge({"idea": "test idea", "payload": {
                "requirements": "Must work offline.", "matches": MATCHES}})
        self.assertEqual(result[1]["difference"], directions[1]["difference"].strip())
        self.assertGreater(len(result[1]["difference"]), 600)
        self.assertEqual(call.call_args.args[1]["requirements"], "Must work offline.")

    def test_original_direction_retains_idea_and_requirements_in_build_brief(self):
        project = {"idea": "A very specific app idea", "payload": {
            "requirements": "Keyboard accessible; no account required.",
            "matches": MATCHES, "directions": DIRECTIONS, "selected_id": "original",
        }}
        result = studio.brief(project)
        self.assertEqual(result["direction"]["id"], "original")
        self.assertEqual(result["direction"]["pitch"], project["idea"])
        self.assertEqual(result["requirements"], project["payload"]["requirements"])

    async def test_build_and_mutate_pass_actual_html_and_direction(self):
        payload = studio.append_version({"versions": [], "cursor": -1, "matches": MATCHES,
                                         "directions": DIRECTIONS, "selected_id": "b"}, HTML, "Original")
        project = {"idea": "The intended tool", "payload": payload}
        with patch.object(studio, "_response", AsyncMock(return_value=SimpleNamespace(output_text=HTML))) as call:
            await studio.mutate(project, "Make the counter amber")
            data = call.call_args.args[1]
            self.assertEqual(data["html"], HTML)
            self.assertEqual(data["change_request"], "Make the counter amber")
            self.assertEqual(data["direction"]["id"], "b")

    async def test_missing_credentials_fails_without_fallback(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                await studio._response("test", {})


class StudioAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="manifest-studio-test-")
        self.store = SQLiteStore(self.temp.name)
        self.store_patch = patch.object(server, "store", self.store)
        self.store_patch.start()
        self.env = patch.dict(os.environ, {"STUDIO_ALLOW_LOCAL": "1", "OPENAI_API_KEY": "test-fixture-not-a-key"})
        self.env.start()
        self.rate = patch("server._mix_rate", AsyncMock())
        # Router callback was bound on include, so isolate its existing counters.
        server._MIX_HITS.clear(); server._MIX_GLOBAL.clear()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://test.local")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.store_patch.stop(); self.env.stop()
        self.store._db.close(); self.temp.cleanup()

    async def create(self, requirements=""):
        body = {"id": str(uuid.uuid4()), "idea": "An app for meaningful little counters",
                "requirements": requirements}
        r = await self.client.post("/api/studio/projects", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    async def op(self, project, kind, **values):
        return await self.client.post(f"/api/studio/projects/{project['id']}/{kind}", json={
            "request_id": str(uuid.uuid4()), "revision": project["revision"], **values})

    async def researched(self):
        p = await self.create()
        with patch.object(studio, "scout", AsyncMock(return_value={"matches": MATCHES, "researched_at": "2026-10-19T00:00:00Z"})):
            p = (await self.op(p, "scout")).json()
        with patch.object(studio, "diverge", AsyncMock(return_value=DIRECTIONS)):
            p = (await self.op(p, "diverge")).json()
        return p

    async def built(self):
        p = await self.researched()
        with patch.object(studio, "build", AsyncMock(return_value=studio.validate_html(HTML))):
            r = await self.op(p, "build", direction_id="a")
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()

    async def test_full_workflow_is_saved_and_reopened_with_owner_checks(self):
        p = await self.built()
        fixed = studio.validate_html(HTML.replace("Test fixture only", "Fixed fixture"))
        fixes = [{"category": "state", "issue": "Missing reset", "fixed": "Added reset."}]
        with patch.object(studio, "autopsy", AsyncMock(return_value={"html": fixed, "fixes": fixes})):
            p = (await self.op(p, "autopsy")).json()
        self.assertEqual(p["payload"]["autopsy"], "complete")
        with patch.object(studio, "mutate", AsyncMock(return_value=studio.validate_html(HTML.replace("Test fixture only", "Mutation fixture")))) as model:
            p = (await self.op(p, "mutate", change="Use amber")).json()
            self.assertEqual(model.call_args.args[0]["payload"]["versions"][1]["html"], fixed)
        reopened = await self.client.get(f"/api/studio/projects/{p['id']}")
        self.assertEqual(reopened.json()["payload"]["versions"], p["payload"]["versions"])
        self.assertNotIn("owner_token", reopened.json())
        listing = (await self.client.get("/api/studio/projects")).json()["projects"]
        self.assertEqual(listing[0]["id"], p["id"])
        self.assertNotIn("payload", listing[0])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://test.local") as stranger:
            self.assertEqual((await stranger.get(f"/api/studio/projects/{p['id']}")).status_code, 404)
            self.assertEqual((await stranger.post(f"/api/studio/projects/{p['id']}/mutate", json={
                "request_id": str(uuid.uuid4()), "revision": p["revision"], "change": "steal"})).status_code, 404)
            self.assertEqual((await stranger.get("/api/studio/projects")).json()["projects"], [])
            # Replit forwards client-supplied visitor headers: do not trust them.
            self.assertEqual((await stranger.get(f"/api/studio/projects/{p['id']}", headers={
                "X-Visitor-Id": self.client.cookies.get(server.COOKIE)})).status_code, 404)

    async def test_original_choice_and_explicit_requirements_are_saved_through_build(self):
        project = await self.create("Keyboard accessible; no account required.")
        self.assertEqual(project["payload"]["requirements"], "Keyboard accessible; no account required.")
        with patch.object(studio, "scout", AsyncMock(return_value={"matches": MATCHES, "researched_at": "2026-10-19T00:00:00Z"})):
            project = (await self.op(project, "scout")).json()
        with patch.object(studio, "diverge", AsyncMock(return_value=DIRECTIONS)):
            project = (await self.op(project, "diverge")).json()
        selection = await self.client.post(f"/api/studio/projects/{project['id']}/selection", json={
            "revision": project["revision"], "direction_id": "original"})
        self.assertEqual(selection.status_code, 200, selection.text)
        project = selection.json()
        self.assertEqual(project["payload"]["selected_id"], "original")
        seen = {}

        async def build_original(received):
            seen.update(studio.brief(received))
            return studio.validate_html(HTML)

        with patch.object(studio, "build", AsyncMock(side_effect=build_original)):
            response = await self.op(project, "build", direction_id="original")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["payload"]["selected_id"], "original")
        self.assertEqual(seen["direction"]["pitch"], project["idea"])
        self.assertEqual(seen["requirements"], "Keyboard accessible; no account required.")
        reopened = await self.client.get(f"/api/studio/projects/{project['id']}")
        self.assertEqual(reopened.json()["payload"]["requirements"], "Keyboard accessible; no account required.")

    async def test_provider_failure_keeps_app_and_all_versions(self):
        p = await self.built()
        old = copy.deepcopy(p["payload"]["versions"])
        with patch.object(studio, "mutate", AsyncMock(side_effect=RuntimeError("provider-down"))):
            r = await self.op(p, "mutate", change="Amber")
        self.assertEqual(r.status_code, 502)
        self.assertIn("No template", r.json()["error"])
        saved = (await self.client.get(f"/api/studio/projects/{p['id']}")).json()
        self.assertEqual(saved["payload"]["versions"], old)
        self.assertIsNone(saved["payload"]["operation"])

    async def test_failed_autopsy_is_distinct_and_retryable(self):
        p = await self.built()
        with patch.object(studio, "autopsy", AsyncMock(side_effect=ValueError("bad JSON"))):
            self.assertEqual((await self.op(p, "autopsy")).status_code, 502)
        p = (await self.client.get(f"/api/studio/projects/{p['id']}")).json()
        self.assertEqual(p["payload"]["autopsy"], "failed")
        self.assertEqual(len(p["payload"]["versions"]), 1)
        with patch.object(studio, "autopsy", AsyncMock(return_value={"html": HTML, "fixes": []})):
            r = await self.op(p, "autopsy")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["payload"]["autopsy"], "complete")

    async def test_stale_revision_and_duplicate_completed_request_do_not_bill_again(self):
        p = await self.researched()
        request = {"request_id": str(uuid.uuid4()), "revision": p["revision"], "direction_id": "a"}
        with patch.object(studio, "build", AsyncMock(return_value=HTML)) as model:
            first = await self.client.post(f"/api/studio/projects/{p['id']}/build", json=request)
            replay = await self.client.post(f"/api/studio/projects/{p['id']}/build", json=request)
            self.assertEqual(first.json(), replay.json())
            self.assertEqual(model.await_count, 1)
            r = await self.op(p, "build", direction_id="a")
            self.assertEqual(r.status_code, 409)
            self.assertEqual(model.await_count, 1)

    async def test_concurrent_compare_has_exactly_one_winner(self):
        p = await self.create()
        storage = studio.StudioStore(self.store)
        raw = await storage.get(p["id"])
        wins = await asyncio.gather(storage.compare(raw, raw["payload"]), storage.compare(raw, raw["payload"]))
        self.assertEqual(sum(r is not None for r in wins), 1)

    async def test_undo_cap_five_branch_and_restart(self):
        p = await self.built()
        for i in range(6):
            with patch.object(studio, "mutate", AsyncMock(return_value=HTML)):
                p = (await self.op(p, "mutate", change=f"Mutation {i}")).json()
            server._MIX_HITS.clear(); server._MIX_GLOBAL.clear()
        self.assertEqual(len(p["payload"]["versions"]), 5)
        target = p["payload"]["versions"][1]["id"]
        r = await self.client.post(f"/api/studio/projects/{p['id']}/restore", json={
            "revision": p["revision"], "version_id": target})
        p = r.json()
        self.assertEqual(p["payload"]["cursor"], 1)
        with patch.object(studio, "mutate", AsyncMock(return_value=HTML)):
            p = (await self.op(p, "mutate", change="New branch")).json()
        self.assertEqual(len(p["payload"]["versions"]), 3)
        another_store = SQLiteStore(self.temp.name)
        try:
            reopened = await studio.StudioStore(another_store).get(p["id"])
            self.assertEqual(reopened["payload"]["versions"], p["payload"]["versions"])
        finally:
            another_store._db.close()

    async def test_invalid_order_input_and_forged_ownership_fail(self):
        p = await self.create()
        self.assertEqual((await self.op(p, "build", direction_id="a")).status_code, 409)
        self.assertEqual((await self.op(p, "diverge")).status_code, 409)
        self.assertEqual((await self.op(p, "mutate", change="")).status_code, 409)
        self.assertEqual((await self.client.post(f"/api/studio/projects/{p['id']}/selection", json={
            "revision": p["revision"], "direction_id": "original"})).status_code, 409)
        self.assertEqual((await self.client.post("/api/studio/projects", json={
            "id": str(uuid.uuid4()), "idea": "Valid idea input", "owner_token": "forged",
        })).status_code, 422)

    async def test_local_store_never_silently_substitutes_for_supabase(self):
        with patch.dict(os.environ, {"STUDIO_ALLOW_LOCAL": ""}):
            r = await self.client.get("/api/studio/projects")
            self.assertEqual(r.status_code, 503)
            self.assertIn("Supabase", r.json()["error"])

    async def test_lease_blocks_duplicate_but_expired_work_cannot_overwrite_newer(self):
        p = await self.researched()
        storage = studio.StudioStore(self.store)
        raw = await storage.get(p["id"])
        payload = copy.deepcopy(raw["payload"])
        payload["operation"] = {"id": str(uuid.uuid4()), "kind": "build",
                                "lease_until": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")}
        claimed = await storage.compare(raw, payload)
        p["revision"] = claimed["revision"]
        with patch.object(studio, "build", AsyncMock()) as model:
            self.assertEqual((await self.op(p, "build", direction_id="a")).status_code, 409)
            model.assert_not_awaited()
        payload["operation"]["lease_until"] = "2000-01-01T00:00:00Z"
        latest = await storage.compare(claimed, payload)
        self.assertIsNone(await storage.compare(claimed, claimed["payload"]))
        self.assertIsNotNone(latest)


class SupabaseStudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_supabase_reads_use_existing_client_and_revision_filter(self):
        store = SupabaseStore("https://test.invalid", "sb_secret_test-only")
        calls = []
        async def response(req):
            calls.append(req)
            if req.method == "PATCH":
                return httpx.Response(200, json=[{"id": "test", "revision": 5, "payload": {}}])
            return httpx.Response(200, json=[])
        await store._client.aclose()
        store._client = httpx.AsyncClient(base_url=store.url, transport=httpx.MockTransport(response))
        try:
            storage = studio.StudioStore(store)
            await storage.list(["one-owner"])
            self.assertEqual(calls[0].url.params["owner_token"], "eq.one-owner")
            self.assertNotIn("payload", calls[0].url.params["select"])
            await storage.compare({"id": "test", "owner_token": "one-owner", "revision": 4}, {})
            self.assertEqual(calls[-1].url.params["revision"], "eq.4")
            self.assertEqual(calls[-1].url.params["owner_token"], "eq.one-owner")
        finally:
            await store._client.aclose()

    async def test_missing_table_fails_without_sqlite_fallback(self):
        store = SimpleNamespace(kind="supabase", _rest=AsyncMock(side_effect=RuntimeError("missing table")))
        with self.assertRaises(HTTPExceptionForTest()) as raised:
            await studio.StudioStore(store).get(str(uuid.uuid4()))
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("app-studio.sql", raised.exception.detail)


def HTTPExceptionForTest():
    from fastapi import HTTPException
    return HTTPException


if __name__ == "__main__":
    unittest.main()
