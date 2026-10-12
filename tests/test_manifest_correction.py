"""Bounded same-request draft correction; no live providers or account data."""
import asyncio
from copy import deepcopy
import json
import unittest
import uuid
from unittest.mock import patch

from app import llm
from app.plan_quality import blocking_findings
import server
import test_manifest_recovery as recovery_fixture
from test_refinement import refined_output


class ManifestCorrectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = recovery_fixture.ManifestRecoveryTests.asyncSetUp
    asyncTearDown = recovery_fixture.ManifestRecoveryTests.asyncTearDown
    lookup = recovery_fixture.ManifestRecoveryTests.lookup
    output = recovery_fixture.ManifestRecoveryTests.output

    def bad_plan(self):
        plan = deepcopy(refined_output())
        plan["description"] += " This product is fully compliant."
        return plan

    async def bad_output(self, *args, **kwargs):
        yield json.dumps(self.bad_plan())

    def errors(self, response):
        return [json.loads(block.split("data: ", 1)[1]) for block in
                response.text.split("\n\n") if block.startswith("event: error")]

    def counts(self):
        return (
            self.store._db.execute("SELECT COUNT(*) FROM ventures").fetchone()[0],
            self.store._db.execute("SELECT COUNT(*) FROM generation_admissions WHERE consumed = 1").fetchone()[0],
            self.store._db.execute("SELECT COUNT(*) FROM generation_admissions WHERE consumed = 0").fetchone()[0],
        )

    async def test_valid_draft_does_not_call_correction(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("A valid draft must not call correction")
        with patch.object(llm, "stream_venture_text", self.output), patch.object(
            llm, "stream_venture_correction_text", forbidden
        ):
            response = await self.client.post("/api/manifest", json=self.body)
        self.assertIn("event: done", response.text)
        self.assertNotIn('"phase": "correcting"', response.text)
        self.assertEqual(self.counts(), (1, 1, 0))

    async def test_correction_saves_once_using_original_identity_and_allowance(self):
        calls = []
        async def correct(idea, draft, findings, **kwargs):
            calls.append((idea, draft, findings, kwargs))
            corrected = deepcopy(refined_output())
            corrected["name"] = "Unrequested rebrand"
            yield json.dumps(corrected)
        with patch.object(llm, "stream_venture_text", self.bad_output), patch.object(
            llm, "stream_venture_correction_text", correct
        ):
            response = await self.client.post("/api/manifest", json=self.body)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], self.body["idea"])
        self.assertEqual(calls[0][1]["name"], refined_output()["name"])
        self.assertIn("unsupported_certification", calls[0][2])
        self.assertIn('"phase": "correcting"', response.text)
        self.assertIn('"replace": true', response.text)
        self.assertIn("event: done", response.text)
        self.assertEqual(self.counts(), (1, 1, 0))
        recovered = (await self.lookup()).json()
        self.assertEqual(recovered["state"], "completed")
        self.assertEqual(recovered["venture"]["name"], refined_output()["name"])
        def no_provider(*args, **kwargs):
            raise AssertionError("A replay must not restart either provider")
        with patch.object(llm, "stream_venture_text", no_provider), patch.object(
            llm, "stream_venture_correction_text", no_provider
        ):
            replay = await self.client.post("/api/manifest", json=self.body)
        self.assertEqual(replay.json()["state"], "completed")
        self.assertEqual(self.counts(), (1, 1, 0))

    async def test_still_rejected_correction_is_not_saved_or_looped_and_allows_manual_retry(self):
        calls = []
        async def correct(*args, **kwargs):
            calls.append(args)
            yield json.dumps(self.bad_plan())
        with patch.object(llm, "stream_venture_text", self.bad_output), patch.object(
            llm, "stream_venture_correction_text", correct
        ):
            response = await self.client.post("/api/manifest", json=self.body)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.errors(response)[0]["code"], "draft_rejected")
        self.assertNotIn('"replace": true', response.text)
        self.assertNotIn("event: done", response.text)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual((await self.lookup()).json()["state"], "failed")
        with patch.object(llm, "stream_venture_text", self.output):
            retry = await self.client.post("/api/manifest", json={
                **self.body, "request_id": str(uuid.uuid4()),
            })
        self.assertIn("event: done", retry.text)
        self.assertEqual(self.counts(), (1, 1, 0))

    async def test_initial_text_and_correction_share_one_deadline(self):
        calls = []
        async def initial(*args, **kwargs):
            await asyncio.sleep(.25)
            yield json.dumps(self.bad_plan())
        async def correct(*args, **kwargs):
            calls.append(True)
            await asyncio.sleep(.25)
            yield json.dumps(refined_output())
        with patch.object(llm, "stream_venture_text", initial), patch.object(
            llm, "stream_venture_correction_text", correct
        ), patch.object(server, "TEXT_TIMEOUT_SECONDS", .4):
            response = await self.client.post("/api/manifest", json=self.body)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.errors(response)[0]["code"], "draft_correction_failed")
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual((await self.lookup()).json()["state"], "failed")

    async def test_disconnect_during_correction_releases_request_and_allowance(self):
        started = asyncio.Event()
        async def correct(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
            yield ""
        with patch.object(llm, "stream_venture_text", self.bad_output), patch.object(
            llm, "stream_venture_correction_text", correct
        ):
            task = asyncio.create_task(self.client.post("/api/manifest", json=self.body))
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual((await self.lookup()).json()["state"], "failed")

    async def test_incomplete_correction_never_replaces_preview_or_saves(self):
        async def correct(*args, **kwargs):
            yield '{"name": "Incomplete"'
        with patch.object(llm, "stream_venture_text", self.bad_output), patch.object(
            llm, "stream_venture_correction_text", correct
        ):
            response = await self.client.post("/api/manifest", json=self.body)
        self.assertEqual(self.errors(response)[0]["code"], "draft_correction_failed")
        self.assertNotIn('"replace": true', response.text)
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_selected_mix_mechanisms_are_passed_to_correction(self):
        concept = {"sentence": self.body["idea"], "mechanisms": ["over-center clamp", "rail indexing"]}
        contexts = []
        async def correct(*args, **kwargs):
            contexts.append(kwargs["angle"])
            yield json.dumps(refined_output())
        with patch.object(server, "_mix_snapshot", return_value={"concepts": [concept]}), patch.object(
            llm, "stream_venture_text", self.bad_output
        ), patch.object(llm, "stream_venture_correction_text", correct):
            response = await self.client.post("/api/manifest", json={**self.body, "mix_id": "synthetic-mix"})
        self.assertIn("event: done", response.text)
        self.assertIn("over-center clamp", contexts[0])
        self.assertIn("rail indexing", contexts[0])
        self.assertEqual(self.counts(), (1, 1, 0))

    async def test_normalized_correction_must_also_pass_checks(self):
        normalizer = server._normalize
        def invalid_normalized(plan):
            normalized = normalizer(plan)
            normalized["tagline"] = "This product is fully compliant."
            return normalized
        with patch.object(llm, "stream_venture_text", self.output), patch.object(
            llm, "stream_venture_correction_text", self.output
        ), patch.object(server, "_normalize", invalid_normalized):
            response = await self.client.post("/api/manifest", json=self.body)
        self.assertEqual(self.errors(response)[0]["code"], "draft_rejected")
        self.assertNotIn('"replace": true', response.text)
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_unlimited_creator_still_uses_one_durable_request_identity(self):
        with patch.object(server, "is_app_owner", return_value=True), patch.object(
            llm, "stream_venture_text", self.bad_output
        ), patch.object(llm, "stream_venture_correction_text", self.output):
            response = await self.client.post("/api/manifest", json=self.body)
            replay = await self.client.post("/api/manifest", json=self.body)
        self.assertIn("event: done", response.text)
        self.assertEqual(replay.json()["state"], "completed")
        self.assertEqual(self.counts(), (1, 0, 0))


class CorrectionProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_receives_original_draft_constraints_and_rejection_codes(self):
        captured = []
        async def provider(message, system):
            captured.append((message, system))
            yield json.dumps(refined_output())
        idea = "A rail-clamped greenhouse ladder; no subscriptions or electronics."
        with patch.object(llm, "text_provider", return_value="openai"), patch.object(
            llm, "_stream_openai", provider
        ):
            text = "".join([chunk async for chunk in llm.stream_venture_correction_text(
                idea, refined_output(), ["missing_qualified_review"], angle="Keep the rail-clamp transfer.",
            )])
        self.assertEqual(json.loads(text)["name"], refined_output()["name"])
        message, system = captured[0]
        self.assertIn(idea, message)
        self.assertIn("rail-clamp transfer", message)
        self.assertIn("missing_qualified_review", message)
        self.assertNotIn("VARIETY IS MANDATORY.", system)
        self.assertIn("Keep its name, core product", system)
        self.assertIn("Do not evade checks", system)

    async def test_mock_does_not_fake_a_successful_repair(self):
        plan = deepcopy(refined_output())
        plan["description"] += " The product is fully compliant."
        with patch.object(llm, "text_provider", return_value="mock"):
            text = "".join([chunk async for chunk in llm.stream_venture_correction_text(
                "Synthetic organizer", plan, ["unsupported_certification"],
            )])
        self.assertIn("unsupported_certification", blocking_findings(json.loads(text)))
