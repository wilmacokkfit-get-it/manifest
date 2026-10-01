"""Focused store tests for owner-authorized repeated refinement claims."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from app.store import SQLiteStore, SupabaseStore


def iso_at(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


def make_venture(*, slug: str, grounding: dict | None = None, **overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "slug": slug,
        "owner_token": "test-owner",
        "idea_raw": "A modular bench organizer",
        "idea_hash": "owner-refinement-test",
        "provenance_code": "OWNER1",
        "name": "Bench Organizer",
        "grounding": {"market": "Small workshops", **(grounding or {})},
        "images": [],
        "images_status": "failed",
        "visibility": "private",
        "status": "ready",
        "created_at": iso_at(),
        "updated_at": iso_at(),
        "path": None,
    }
    row.update(overrides)
    return row


class OwnerRefinementSQLiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="manifest-owner-refinement-")
        self.store = SQLiteStore(self.tempdir.name)

    async def asyncTearDown(self) -> None:
        self.store._db.close()
        self.tempdir.cleanup()

    async def row_with_marker(self, state: str, slug: str) -> tuple[dict, dict]:
        prior = {
            "state": state,
            "claim_id": f"prior-{slug}",
            "context": "Original owner-approved refinement",
        } if state == "completed" else {"state": state}
        row = await self.store.create_venture(
            make_venture(slug=slug, grounding={"_refinement": prior})
        )
        return row, prior

    async def test_default_claim_denies_completed_and_skipped_markers(self) -> None:
        for state in ("completed", "skipped"):
            with self.subTest(state=state):
                row, _ = await self.row_with_marker(state, f"default-denied-{state}")
                self.assertIsNone(await self.store.claim_refinement(row["id"], "another try"))

    async def test_owner_repeat_accepts_completed_or_skipped_and_preserves_grounding(self) -> None:
        for state in ("completed", "skipped"):
            with self.subTest(state=state):
                row, prior = await self.row_with_marker(state, f"repeat-{state}")
                claimed = await self.store.claim_refinement(
                    row["id"], "Owner's next refinement", claim_id=f"repeat-worker-{state}",
                    allow_repeat=True,
                )
                self.assertIsNotNone(claimed)
                self.assertEqual(claimed["status"], "refining")
                self.assertEqual(claimed["grounding"]["market"], "Small workshops")
                marker = claimed["grounding"]["_refinement"]
                self.assertEqual(marker["state"], "pending")
                self.assertEqual(marker["previous_refinement"], prior)

    async def test_unmarked_venture_still_accepts_repeat_flag_and_malformed_marker_does_not(self) -> None:
        unmarked = await self.store.create_venture(make_venture(slug="unmarked-repeat"))
        self.assertIsNotNone(await self.store.claim_refinement(
            unmarked["id"], "First refinement", allow_repeat=True
        ))

        pending_images = await self.store.create_venture(
            make_venture(slug="unmarked-pending-images", images_status="pending")
        )
        self.assertIsNone(await self.store.claim_refinement(
            pending_images["id"], "Images are not finished", allow_repeat=True,
            require_images_finished=False,
        ))

        malformed = await self.store.create_venture(
            make_venture(slug="malformed-repeat", grounding={"_refinement": "completed"})
        )
        self.assertIsNone(await self.store.claim_refinement(
            malformed["id"], "Must not repeat", allow_repeat=True
        ))

    async def test_repeated_skip_rejects_pending_render_and_preserves_finished_render_claim(self) -> None:
        row, _ = await self.row_with_marker("completed", "repeat-render-pending")
        original_marker = {
            **row["grounding"]["_refinement"],
            "render_claim_id": "active-render-worker",
            "render_lease_until": iso_at(timedelta(minutes=5)),
        }
        await self.store.update_venture(row["id"], {
            "images_status": "pending",
            "grounding": {**row["grounding"], "_refinement": original_marker},
        })

        self.assertIsNone(await self.store.claim_refinement(
            row["id"], "Do not overlap rendering", allow_repeat=True,
            require_images_finished=False,
        ))

        self.assertIsNone(await self.store.claim_refinement(
            row["id"], skipped=True, allow_repeat=True, require_images_finished=False,
        ))
        still_rendering = await self.store.get_by_id(row["id"])
        self.assertEqual(still_rendering["grounding"]["_refinement"], original_marker)

        await self.store.update_venture(row["id"], {"images_status": "failed"})
        skipped = await self.store.claim_refinement(
            row["id"], skipped=True, allow_repeat=True, require_images_finished=False,
        )
        self.assertEqual(skipped["grounding"]["_refinement"], original_marker)

    async def test_racing_repeats_have_only_one_winner(self) -> None:
        row, _ = await self.row_with_marker("completed", "repeat-race")
        results = await asyncio.gather(
            self.store.claim_refinement(
                row["id"], "First competing repeat", allow_repeat=True, claim_id="repeat-one"
            ),
            self.store.claim_refinement(
                row["id"], "Second competing repeat", allow_repeat=True, claim_id="repeat-two"
            ),
        )
        self.assertEqual(sum(result is not None for result in results), 1)

    async def test_expired_repeat_recovery_restores_original_marker(self) -> None:
        for state in ("completed", "skipped"):
            with self.subTest(state=state):
                row, prior = await self.row_with_marker(state, f"repeat-recovery-{state}")
                claimed = await self.store.claim_refinement(
                    row["id"], "Interrupted repeat", allow_repeat=True,
                    claim_id=f"interrupted-repeat-{state}",
                )
                expired_grounding = dict(claimed["grounding"])
                expired_grounding["_refinement"] = {
                    **expired_grounding["_refinement"],
                    "lease_until": iso_at(timedelta(seconds=-1)),
                }
                await self.store.update_venture(row["id"], {
                    "status": "refining", "grounding": expired_grounding,
                })

                recovered = await self.store.reconcile_venture(await self.store.get_by_id(row["id"]))

                self.assertEqual(recovered["status"], "ready")
                self.assertEqual(recovered["grounding"]["market"], "Small workshops")
                self.assertEqual(recovered["grounding"]["_refinement"], prior)


class OwnerRefinementSupabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeat_claim_uses_observed_grounding_snapshot(self) -> None:
        prior = {"state": "completed", "claim_id": "old-claim", "context": "Prior result"}
        row = make_venture(
            slug="supabase-repeat",
            grounding={"market": "Small workshops", "_refinement": prior},
        )
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=[row])
            payload = json.loads(request.content)
            return httpx.Response(200, json=[{**row, **payload}])

        store = SupabaseStore("https://example.supabase.co", "test-key")
        await store._client.aclose()
        store._client = httpx.AsyncClient(
            base_url=store.url,
            headers={"apikey": "test-key", "Authorization": "Bearer test-key"},
            transport=httpx.MockTransport(handler),
        )
        try:
            claimed = await store.claim_refinement(
                row["id"], "Next owner refinement", allow_repeat=True, claim_id="new-claim"
            )
            request = requests[-1]
            params = request.url.params
            self.assertEqual(params["status"], "eq.ready")
            self.assertEqual(params["visibility"], "eq.private")
            self.assertEqual(params["path"], "is.null")
            self.assertEqual(params["grounding"], f"eq.{json.dumps(row['grounding'])}")
            self.assertNotIn("grounding->_refinement", params)
            self.assertEqual(
                json.loads(request.content)["grounding"]["_refinement"]["previous_refinement"],
                prior,
            )
            self.assertEqual(claimed["status"], "refining")
        finally:
            await store._client.aclose()

    async def test_repeated_skip_rejects_pending_render_and_is_noop_afterward(self) -> None:
        prior = {
            "state": "completed",
            "claim_id": "old-claim",
            "render_claim_id": "active-render-worker",
        }
        row = make_venture(
            slug="supabase-repeat-skip",
            grounding={"market": "Small workshops", "_refinement": prior},
            images_status="pending",
        )
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=[row])

        store = SupabaseStore("https://example.supabase.co", "test-key")
        await store._client.aclose()
        store._client = httpx.AsyncClient(
            base_url=store.url,
            headers={"apikey": "test-key", "Authorization": "Bearer test-key"},
            transport=httpx.MockTransport(handler),
        )
        try:
            self.assertIsNone(await store.claim_refinement(
                row["id"], skipped=True, allow_repeat=True, require_images_finished=False,
            ))
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].method, "GET")

            row["images_status"] = "failed"
            unchanged = await store.claim_refinement(
                row["id"], skipped=True, allow_repeat=True, require_images_finished=False,
            )
            self.assertEqual(unchanged["grounding"]["_refinement"], prior)
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(request.method == "GET" for request in requests))
        finally:
            await store._client.aclose()


if __name__ == "__main__":
    unittest.main()