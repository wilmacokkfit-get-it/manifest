"""Focused tests for sourced market mixing and the /api/mix routes."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

# Importing server builds a store. Ensure its one-time initialization only ever
# sees disposable SQLite data, matching the isolation pattern in test_regressions.
_IMPORT_DATA = tempfile.TemporaryDirectory(prefix="manifest-mixer-import-")
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

from app import llm, mixer  # noqa: E402
from app.store import SQLiteStore  # noqa: E402


def venture(
    slug: str,
    *,
    visibility: str = "public",
    status: str = "ready",
    owner: str = "venture-owner-token",
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "slug": slug,
        "owner_token": owner,
        "idea_raw": "A compact tool for organizing a workbench",
        "idea_hash": "mixer-test-hash",
        "provenance_code": "MIXTEST",
        "name": f"Venture {slug}",
        "tagline": "A useful test fixture.",
        "description": "A deterministic test-only venture fixture.",
        "category": "Workshop",
        "accent": "#aabbcc",
        "tiers": [],
        "roadmap": [],
        "grounding": {
            "exists": "",
            "who_pays": "",
            "first_hundred": "",
            "private_ask": "must not be exposed",
            "creator_email": "private@example.test",
        },
        "image_prompts": [],
        "images": [],
        "images_status": "failed",
        "visibility": visibility,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "buy_count": 2,
        "build_count": 3,
        "fund_count": 1,
    }


def trend(trend_id: str, url: str = "https://news.example.test/story") -> dict:
    return {
        "id": trend_id,
        "title": f"Shift {trend_id}",
        "product": "A named physical product and what it does.",
        "mechanism": "A specific mechanism that could transfer to another field.",
        "evidence": "The cited source describes this product and its launch.",
        "source_date": None,
        "sources": [{"url": url, "title": "Example reporting"}],
    }


def concepts() -> list[dict]:
    return [
        {
            "name": "Field Kit",
            "stole": "A repair ritual from one product and a refill mechanism from another.",
            "hybrid": "A physical kit combines these mechanisms for a specific recurring job.",
            "buyer": "Independent repair technicians handling small jobs.",
            "why_now": "The supplied evidence shows related products; local demand remains uncertain.",
            "why_it_does": "Hypothesis: compared with replacing a whole kit, a refill mechanism could keep repairs equally effective with less waste if refills stay available; dependence on a supplier is the tradeoff.",
            "first_test": "Offer ten kits to technicians and record paid reservations.",
            "closest_existing": "A basic repair kit, with the comparison unverified.",
            "sentence": "A refillable repair kit helps independent technicians finish small jobs faster.",
        },
        {
            "name": "Second concept",
            "stole": "A second concrete mechanism.",
            "hybrid": "A distinct physical product for a different use.",
            "buyer": "A specific buyer with a specific problem.",
            "why_now": "Evidence is supplied but demand remains uncertain.",
            "why_it_does": "Hypothesis: a passive clamp could hold as well as screws without wall damage if the surface is suitable, but it supports less weight.",
            "first_test": "Measure interest from ten potential buyers.",
            "closest_existing": "An existing alternative, comparison unverified.",
            "sentence": "A second concrete product serves a distinct buyer and need.",
        },
        {
            "name": "Third concept",
            "stole": "A third concrete mechanism.",
            "hybrid": "A different precise service with a testable use.",
            "buyer": "A third exact buyer.",
            "why_now": "The supplied evidence motivates an experiment, not a demand claim.",
            "why_it_does": "Hypothesis: pooling deliveries could match individual delivery reliability at lower cost if routes overlap, but scheduled windows reduce flexibility.",
            "first_test": "Run a small paid pilot with ten buyers.",
            "closest_existing": "An existing alternative, comparison unverified.",
            "sentence": "A distinct service solves a defined problem for a named buyer.",
        },
    ]


class MixerUnitTests(unittest.IsolatedAsyncioTestCase):
    def test_citations_are_allowlisted_and_missing_dates_remain_unknown(self) -> None:
        accepted_url = "https://credible.example.test/press-release"

        class SearchResponse:
            def model_dump(self):
                return {
                    "output": [
                        {
                            "action": {
                                "sources": [
                                    {"url": accepted_url, "title": "Credible release"},
                                    {"url": "javascript:alert(1)", "title": "Unsafe"},
                                    {"url": "https://user:pass@unsafe.example.test/", "title": "Credentials"},
                                ]
                            }
                        }
                    ]
                }

        allowlist = mixer.cited_sources(SearchResponse())
        self.assertEqual(list(allowlist), [accepted_url])

        cleaned = mixer.clean_trends(
            {
                "trends": [
                    {
                        "title": "Cited product shift",
                        "product": "A concrete named product.",
                        "mechanism": "A specific mechanism used by the product.",
                        "evidence": "The linked release describes the product.",
                        "source_urls": [accepted_url, "https://invented.example.test/story"],
                    },
                    {
                        "title": "Hallucinated citation",
                        "product": "A concrete product.",
                        "mechanism": "A specific mechanism.",
                        "evidence": "No search result supports this invented URL.",
                        "source_urls": ["https://invented.example.test/story"],
                    },
                    {
                        "title": "Unsafe citation",
                        "product": "A concrete product.",
                        "mechanism": "A specific mechanism.",
                        "evidence": "Unsafe URLs are not citations.",
                        "source_urls": ["javascript:alert(1)"],
                    },
                ]
            },
            allowlist,
        )

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["sources"], [{"url": accepted_url, "title": "Credible release"}])
        self.assertIsNone(cleaned[0]["source_date"])

    def test_clean_trends_rejects_malformed_and_oversized_fields_without_truncation(self) -> None:
        cited = {"https://credible.example.test/story": {"url": "https://credible.example.test/story", "title": "Source"}}
        valid = {
            "title": "Valid product shift",
            "product": "A specific named product.",
            "mechanism": "A concrete mechanism used by that product.",
            "evidence": "The linked source describes what this product does.",
            "source_urls": ["https://credible.example.test/story"],
        }
        result = mixer.clean_trends(
            {
                "trends": [
                    valid,
                    None,
                    {**valid, "title": 17},
                    {**valid, "title": "x" * 101},
                    {**valid, "mechanism": "x" * 401},
                    {**valid, "source_urls": "https://credible.example.test/story"},
                ]
            },
            cited,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], valid["title"])
        self.assertEqual(result[0]["mechanism"], valid["mechanism"])

        for malformed in (None, {"trends": "not a list"}, {"trends": [None, {**valid, "evidence": "x" * 601}]}):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                mixer.clean_trends(malformed if isinstance(malformed, dict) else {"trends": malformed}, cited)

    def test_clean_concepts_requires_three_complete_bounded_distinct_concepts(self) -> None:
        valid = concepts()
        self.assertEqual(mixer.clean_concepts({"concepts": valid}), valid)

        invalid_cases = []
        invalid_cases.append([{}, valid[1], valid[2]])  # malformed / missing required strings
        invalid_cases.append([{**valid[0], "name": valid[1]["name"].upper()}, valid[1], valid[2]])
        invalid_cases.append([{**valid[0], "hybrid": valid[1]["hybrid"]}, valid[1], valid[2]])
        invalid_cases.append([{**valid[0], "sentence": valid[1]["sentence"]}, valid[1], valid[2]])
        invalid_cases.append([{**valid[0], "sentence": "tiny"}, valid[1], valid[2]])
        invalid_cases.append([{**valid[0], "sentence": "s" * 301}, valid[1], valid[2]])
        invalid_cases.append([{**valid[0], "name": "n" * 81}, valid[1], valid[2]])
        invalid_cases.append([{k: v for k, v in valid[0].items() if k != "why_it_does"}, valid[1], valid[2]])
        invalid_cases.append([{**valid[0], "why_it_does": ""}, valid[1], valid[2]])
        invalid_cases.append([{**valid[0], "why_it_does": "x" * 1001}, valid[1], valid[2]])
        invalid_cases.extend((valid[:2], valid + [valid[0]]))

        for malformed in invalid_cases:
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                mixer.clean_concepts({"concepts": malformed})

    async def test_research_requests_current_evidence_and_private_mechanism_analysis(self) -> None:
        create = AsyncMock(return_value=SimpleNamespace(
            status="completed", output_text="{}",
            model_dump=lambda: {"output": []},
        ))
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-test-key"}), patch(
            "openai.AsyncOpenAI", return_value=client
        ), patch.object(mixer, "clean_trends", return_value=[trend("trend-1")]):
            await mixer.research("Repairable tools")
        request = create.await_args.kwargs
        self.assertEqual(request["tool_choice"], "required")
        self.assertEqual(request["tools"][0]["type"], "web_search")
        for phrase in (
            "Privately reverse-engineer", "root cause", "critical dependency",
            "source actually verifies", "behind the scenes", "hypotheses",
            "last six months", "Hard maximum characters",
        ):
            self.assertIn(phrase, request["instructions"])

    async def test_combine_requests_and_returns_comparative_hypotheses(self) -> None:
        response = type("Response", (), {
            "status": "completed", "output_text": json.dumps({"concepts": concepts()})
        })()
        create = AsyncMock(return_value=response)
        client = type("Client", (), {
            "responses": type("Responses", (), {"create": create})()
        })()
        with patch("openai.AsyncOpenAI", return_value=client):
            result = await mixer.combine([trend("trend-1"), trend("trend-2")], [])
        self.assertEqual(result, concepts())
        instructions = create.await_args.kwargs["instructions"]
        for phrase in (
            "do not reject a plausible combination",
            "better than OR equal to the normal way",
            "explicitly label an untested benefit",
            "same task and conditions",
            "tradeoff or failure",
            "strongest realistic original",
            "original still wins",
            "falsifiable side-by-side test",
            "not originality",
            "invent a proposed trend",
            "main adoption friction",
        ):
            self.assertIn(phrase, instructions)
        self.assertTrue(all(c["why_it_does"].startswith("Hypothesis:") for c in result))

    async def test_venture_stream_prompt_selection_for_both_provider_paths(self) -> None:
        selected_angle = "Preserve this selected physical repair kit."
        avoid = [{"name": "Old concept", "tagline": "Do not repeat this."}]
        captured: list[tuple[str, str]] = []

        async def stub_stream(message: str, *, system: str):
            captured.append((message, system))
            yield "test"

        for provider, path_name in (("anthropic", "_stream_anthropic"), ("openai", "_stream_openai")):
            with self.subTest(provider=provider), patch.object(
                llm, "text_provider", return_value=provider
            ), patch.object(llm, path_name, new=stub_stream):
                normal = [chunk async for chunk in llm.stream_venture_text(
                    "A repair kit for independent technicians", selected_angle, avoid
                )]
                preserved = [chunk async for chunk in llm.stream_venture_text(
                    "A repair kit for independent technicians", selected_angle, avoid, preserve=True
                )]

            self.assertEqual(normal, ["test"])
            self.assertEqual(preserved, ["test"])
            normal_message, normal_system = captured[-2]
            preserve_message, preserve_system = captured[-1]
            self.assertIn("AVOID.", normal_message)
            self.assertIn("Old concept", normal_message)
            self.assertIn("VARIETY IS MANDATORY.", normal_system)
            self.assertNotIn("AVOID.", preserve_message)
            self.assertIn(selected_angle, preserve_message)
            self.assertIn("SELECTED HYBRID", preserve_system)
            self.assertNotIn("VARIETY IS MANDATORY.", preserve_system)

    def test_floor_brief_is_public_signal_allowlist(self) -> None:
        brief = mixer.floor_brief(
            {
                "slug": "public-test",
                "name": "Public venture",
                "tagline": "A public tagline.",
                "description": "Public description.",
                "category": "Workshop",
                "buy_count": "4",
                "build_count": None,
                "fund_count": 2,
                "owner_token": "secret-owner",
                "creator_email": "secret@example.test",
                "private_ask": "Do not leak",
            }
        )
        self.assertEqual(
            brief,
            {
                "slug": "public-test",
                "name": "Public venture",
                "tagline": "A public tagline.",
                "description": "Public description.",
                "category": "Workshop",
                "signals": {"buy": 4, "build": 0, "fund": 2},
            },
        )


class MixRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="manifest-mixer-")
        self.store = SQLiteStore(self.tempdir.name)
        self.original_store = server.store
        self.original_trust_visitor_header = server.TRUST_VISITOR_HEADER
        server.store = self.store
        server.TRUST_VISITOR_HEADER = False
        server._MIX_SNAPSHOTS.clear()
        server._MIX_HITS.clear()
        server._MIX_GLOBAL.clear()
        self.env_patch = patch.dict(
            os.environ,
            {"LLM_PROVIDER": "mock", "IMAGE_PROVIDER": "none"},
            clear=False,
        )
        self.env_patch.start()

    async def asyncTearDown(self) -> None:
        self.env_patch.stop()
        server.store = self.original_store
        server.TRUST_VISITOR_HEADER = self.original_trust_visitor_header
        server._MIX_SNAPSHOTS.clear()
        server._MIX_HITS.clear()
        server._MIX_GLOBAL.clear()
        self.store._db.close()
        self.tempdir.cleanup()

    def client(self) -> httpx.AsyncClient:
        # Model forwarding from a trusted local proxy, not raw application headers.
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=ProxyHeadersMiddleware(server.app, trusted_hosts=["127.0.0.1"]),
                client=("127.0.0.1", 1234),
            ),
            base_url="http://testserver",
        )

    @staticmethod
    def headers(owner: str = "mixer-owner-token-00000001", caller: str = "203.0.113.20") -> dict:
        return {
            "Cookie": f"{server.COOKIE}={owner}",
            "X-Forwarded-For": caller,
        }

    async def test_floor_route_returns_only_ready_public_rows_and_safe_fields(self) -> None:
        public = await self.store.create_venture(venture("mixer-floor-public"))
        await self.store.create_venture(venture("mixer-floor-private", visibility="private"))
        await self.store.create_venture(venture("mixer-floor-draft", status="building"))

        async with self.client() as client:
            response = await client.get("/api/mix/floor")

        self.assertEqual(response.status_code, 200)
        rows = response.json()["ventures"]
        self.assertEqual([row["slug"] for row in rows], [public["slug"]])
        self.assertEqual(rows[0]["signals"], {"buy": 2, "build": 3, "fund": 1})
        self.assertNotIn("owner_token", rows[0])
        self.assertNotIn("grounding", rows[0])
        self.assertNotIn("private_ask", json.dumps(rows))

    async def test_research_snapshot_is_owner_bound_and_expires(self) -> None:
        with patch.object(server.mixer, "research", new=AsyncMock(return_value=[trend("trend-1")])):
            async with self.client() as client:
                created = await client.post(
                    "/api/mix/research", headers=self.headers("research-owner-token-0001"),
                    json={"topic": "  small   workshops "},
                )
                self.assertEqual(created.status_code, 200)
                research_id = created.json()["research_id"]
                self.assertEqual(created.json()["trends"], [trend("trend-1")])

                body = {"research_id": research_id, "trend_ids": ["trend-1", "trend-2"]}
                other_owner = await client.post(
                    "/api/mix/combine", headers=self.headers("different-owner-token-0002"), json=body,
                )
                self.assertEqual(other_owner.status_code, 410)

                server._MIX_SNAPSHOTS[research_id]["expires"] = time.time() - 1
                expired = await client.post(
                    "/api/mix/combine", headers=self.headers("research-owner-token-0001"), json=body,
                )
                self.assertEqual(expired.status_code, 410)

    async def test_two_custom_inputs_work_without_floor_or_research(self) -> None:
        owner = "custom-mix-owner-00000001"
        with patch.object(server.mixer, "combine", new=AsyncMock(return_value=concepts())) as provider:
            async with self.client() as client:
                response = await client.post("/api/mix/combine", headers=self.headers(owner),
                    json={"custom_ideas": ["Origami folding tool tray", "Library checkout tokens"]})
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(result["floor"], [])
                self.assertEqual(result["sources"], [])
                self.assertTrue(result["mix_id"])
                ingredients, floor = provider.await_args.args
                self.assertEqual(floor, [])
                self.assertEqual(len(ingredients), 2)
                self.assertEqual(ingredients[0]["origin"], "creator_input")
                self.assertIn("not sourced market evidence", ingredients[0]["evidence"])
                selected = await client.post("/api/manifest", headers=self.headers(owner),
                    json={"idea": "not a selected sentence", "mix_id": result["mix_id"]})
                self.assertEqual(selected.status_code, 422)

    async def test_partial_render_failure_is_terminal_not_an_endless_empty_slot(self) -> None:
        storage = SimpleNamespace(
            save_render=AsyncMock(return_value="/renders/synthetic.webp"),
            update_venture=AsyncMock(),
        )
        row = {"id": "synthetic-render-check", "slug": "synthetic-render-check",
               "image_prompts": ["mechanism view", "action view"], "grounding": {}}
        with patch.object(server, "store", storage), patch.object(
            server.llm, "generate_render", new=AsyncMock(side_effect=[
                b"synthetic-image", RuntimeError("synthetic image failure")
            ])
        ):
            await server._render_images(row)
        fields = storage.update_venture.await_args.args[1]
        self.assertEqual(fields["images_status"], "failed")
        self.assertEqual(fields["images"], ["/renders/synthetic.webp", None])

    async def test_custom_inputs_are_validated_before_provider_call(self) -> None:
        owner = "custom-invalid-owner-00001"
        bodies = [
            {"custom_ideas": ["Same idea", " same   IDEA "]},
            {"custom_ideas": ["", "Another idea"]},
            {"custom_ideas": ["One idea"]},
            {"custom_ideas": ["x"*601, "Another idea"]},
            {"custom_ideas": ["First", "Second"], "trend_ids": ["unresearched"]},
            {"custom_ideas": ["First", "Second", "Third", "Fourth"]},
        ]
        with patch.object(server.mixer, "combine", new=AsyncMock()) as provider:
            async with self.client() as client:
                for body in bodies:
                    with self.subTest(body=body):
                        response = await client.post("/api/mix/combine", headers=self.headers(owner), json=body)
                        self.assertEqual(response.status_code, 422)
            provider.assert_not_awaited()

    async def test_combine_rejects_duplicate_wrong_count_and_stale_ingredients(self) -> None:
        owner = "combine-owner-token-0000001"
        research_id = server._mix_save(
            "research", owner, {"trends": [trend("trend-1"), trend("trend-2"), trend("trend-3")]}
        )
        private = await self.store.create_venture(venture("mixer-private-selection", visibility="private"))
        await self.store.create_venture(venture("mixer-public-selection"))
        base = {"research_id": research_id}
        invalid_bodies = [
            ({**base, "trend_ids": ["trend-1"], "floor_slugs": []}, 422),  # one ingredient
            ({**base, "trend_ids": ["trend-1", "trend-1"], "floor_slugs": []}, 422),  # duplicate trends
            ({**base, "trend_ids": ["trend-1", "trend-2", "trend-3", "extra"], "floor_slugs": []}, 422),
            ({**base, "trend_ids": ["trend-1", "trend-2", "trend-3"],
              "floor_slugs": ["mixer-public-selection"]}, 422),  # four ingredients
            ({**base, "trend_ids": ["trend-1"], "floor_slugs": ["mixer-public-selection"] * 2}, 422),
            ({**base, "trend_ids": ["trend-1", "missing-trend"], "floor_slugs": []}, 422),
            ({**base, "trend_ids": ["trend-1"], "floor_slugs": ["missing-floor-selection"]}, 404),
            ({**base, "trend_ids": ["trend-1"], "floor_slugs": [private["slug"]]}, 404),
        ]

        with patch.object(server.mixer, "combine", new=AsyncMock()) as combine:
            async with self.client() as client:
                for body, expected in invalid_bodies:
                    with self.subTest(body=body):
                        response = await client.post(
                            "/api/mix/combine", headers=self.headers(owner), json=body,
                        )
                        self.assertEqual(response.status_code, expected)
            combine.assert_not_awaited()

    async def test_research_and_combine_provider_failures_are_explicit_502s(self) -> None:
        owner = "failure-owner-token-0000001"
        research_id = server._mix_save("research", owner, {"trends": [trend("trend-1"), trend("trend-2")]})

        async def fail(*args, **kwargs):
            raise RuntimeError("provider detail must not leak")

        with patch.object(server.mixer, "research", new=fail), patch.object(
            server.mixer, "combine", new=fail
        ):
            async with self.client() as client:
                research = await client.post(
                    "/api/mix/research", headers=self.headers(owner), json={"topic": "useful products"},
                )
                combine = await client.post(
                    "/api/mix/combine",
                    headers=self.headers(owner),
                    json={"research_id": research_id, "trend_ids": ["trend-1", "trend-2"]},
                )

        self.assertEqual(research.status_code, 502)
        self.assertEqual(
            research.json()["error"],
            "Live research could not return verified source links. Try again shortly.",
        )
        self.assertEqual(combine.status_code, 502)
        self.assertEqual(
            combine.json()["error"],
            "The engine couldn't make three concrete combinations. Try different ingredients.",
        )
        self.assertNotIn("provider detail", research.text + combine.text)

    async def test_quota_reservation_precedes_provider_and_blocks_concurrent_global_overflow(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        now = time.time()
        server._MIX_GLOBAL.extend([now - 2] * 99)
        provider_calls = 0

        async def slow_research(topic: str) -> list[dict]:
            nonlocal provider_calls
            provider_calls += 1
            entered.set()
            await release.wait()
            return [trend("trend-1")]

        with patch.object(server, "is_app_owner", new=AsyncMock(return_value=False)), patch.object(
            server.mixer, "research", new=slow_research
        ):
            async with self.client() as client:
                first = asyncio.create_task(
                    client.post(
                        "/api/mix/research",
                        headers=self.headers("first-owner-token-000001", "203.0.113.31"),
                        json={"topic": "test topic"},
                    )
                )
                await asyncio.wait_for(entered.wait(), timeout=2)
                self.assertEqual(len(server._MIX_GLOBAL), 100)
                self.assertEqual(len(server._MIX_HITS["ip:203.0.113.31"]), 1)

                second = await client.post(
                    "/api/mix/research",
                    headers=self.headers("second-owner-token-000002", "203.0.113.32"),
                    json={"topic": "test topic"},
                )
                self.assertEqual(second.status_code, 429)
                self.assertEqual(provider_calls, 1)
                release.set()
                first_response = await first

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(provider_calls, 1)

    async def test_mix_quota_exemption_depends_only_on_app_owner_check(self) -> None:
        owner = "cookie-does-not-exempt-owner"
        caller = "203.0.113.41"
        now = time.time()
        server._MIX_HITS[f"ip:{caller}"].extend([now - 1] * 6)
        provider = AsyncMock(return_value=[trend("trend-1")])

        with patch.object(server, "is_app_owner", new=AsyncMock(return_value=False)), patch.object(
            server.mixer, "research", new=provider
        ):
            async with self.client() as client:
                denied = await client.post(
                    "/api/mix/research", headers=self.headers(owner, caller), json={"topic": "test"},
                )
        self.assertEqual(denied.status_code, 429)
        provider.assert_not_awaited()

        with patch.object(server, "is_app_owner", new=AsyncMock(return_value=True)), patch.object(
            server.mixer, "research", new=provider
        ):
            async with self.client() as client:
                allowed = await client.post(
                    "/api/mix/research", headers=self.headers(owner, caller), json={"topic": "test"},
                )
        self.assertEqual(allowed.status_code, 200)
        provider.assert_awaited_once_with("test")
        self.assertEqual(len(server._MIX_HITS[f"ip:{caller}"]), 6)
        self.assertEqual(len(server._MIX_GLOBAL), 0)

    async def test_successful_research_combine_uses_selected_ingredients(self) -> None:
        owner = "success-owner-token-000001"
        floor_row = await self.store.create_venture(venture("mixer-success-floor"))
        found = [trend("trend-1"), trend("trend-2", "https://journal.example.test/second")]
        returned_concepts = concepts()
        research_provider = AsyncMock(return_value=found)
        combine_provider = AsyncMock(return_value=returned_concepts)

        with patch.object(server.mixer, "research", new=research_provider), patch.object(
            server.mixer, "combine", new=combine_provider
        ):
            async with self.client() as client:
                research_response = await client.post(
                    "/api/mix/research", headers=self.headers(owner), json={"topic": "  useful   tools "},
                )
                self.assertEqual(research_response.status_code, 200)
                research_id = research_response.json()["research_id"]

                combined = await client.post(
                    "/api/mix/combine",
                    headers=self.headers(owner),
                    json={
                        "research_id": research_id,
                        "trend_ids": ["trend-1", "trend-2"],
                        "floor_slugs": [floor_row["slug"]],
                    },
                )

        self.assertEqual(combined.status_code, 200)
        self.assertEqual(combined.json()["concepts"], returned_concepts)
        self.assertEqual(combined.json()["floor"], [{"slug": floor_row["slug"], "name": floor_row["name"]}])
        self.assertEqual(
            {source["url"] for source in combined.json()["sources"]},
            {"https://news.example.test/story", "https://journal.example.test/second"},
        )
        combine_provider.assert_awaited_once_with(
            found,
            [mixer.floor_brief(floor_row)],
        )
        self.assertEqual(server._mix_snapshot(combined.json()["mix_id"], "mix", owner)["concepts"], returned_concepts)

    async def test_manifest_mix_concept_preserves_angle_and_generation_limits(self) -> None:
        owner = "manifest-mix-owner-token-001"
        selected = concepts()[0]
        mix_id = server._mix_save("mix", owner, {"concepts": [selected]})
        captured: list[tuple[str, str, list, bool]] = []
        generated = {
            "name": "Refillable Field Kit",
            "tagline": "Keep repair supplies ready.",
            "description": "A test-generated venture that preserves the selected concrete concept.",
            "category": "Workshop",
            "tiers": [],
            "roadmap": [],
            "grounding": {"exists": "Test fixture.", "who_pays": "Test buyer.", "first_hundred": "Test outreach."},
            "image_prompts": [],
        }

        async def stub_stream(idea: str, *, angle: str, avoid: list, preserve: bool = False):
            captured.append((idea, angle, avoid, preserve))
            yield json.dumps(generated)

        request_body = {"idea": selected["sentence"], "mix_id": mix_id}
        caller = "203.0.113.51"
        with patch.object(server, "is_app_owner", new=AsyncMock(return_value=False)), patch.object(
            server.llm, "stream_venture_text", new=stub_stream
        ), patch.object(server.llm, "image_provider", return_value="none"):
            async with self.client() as client:
                # Mix-selected generations remain subject to the visitor/hour limit.
                with patch.object(server, "GEN_PER_HOUR", 0):
                    hourly = await client.post(
                        "/api/manifest", headers=self.headers(owner, caller), json=request_body,
                    )
                self.assertEqual(hourly.status_code, 429)
                self.assertEqual(captured, [])

                # They also remain subject to the original global daily generation cap.
                with patch.object(
                    server, "GLOBAL_PER_DAY", 0,
                ):
                    global_cap = await client.post(
                        "/api/manifest", headers=self.headers(owner, caller), json=request_body,
                    )
                self.assertEqual(global_cap.status_code, 429)
                self.assertEqual(captured, [])

                successful = await client.post(
                    "/api/manifest", headers=self.headers(owner, caller), json=request_body,
                )

        self.assertEqual(successful.status_code, 200)
        self.assertIn("event: done", successful.text)
        self.assertEqual(len(captured), 1)
        idea, angle, avoid, preserve = captured[0]
        self.assertEqual(idea, selected["sentence"])
        self.assertIn("Preserve this selected hybrid's exact product, buyer and borrowed mechanisms.", angle)
        self.assertIn(selected["hybrid"], angle)
        self.assertIn(selected["buyer"], angle)
        self.assertIn(selected["stole"], angle)
        self.assertEqual(avoid, [])
        self.assertTrue(preserve)


if __name__ == "__main__":
    unittest.main()