"""Spoofed routing/visitor headers must not create fresh quota or creator identities."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections import defaultdict, deque
from unittest.mock import AsyncMock, patch

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from test_refinement import OWNER, SQLiteStore, refined_output, server, venture


class CallerIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def caller(self, peer, headers=(), trusted_hosts="127.0.0.1"):
        async def echo(scope, receive, send):
            response = JSONResponse({"caller": server._caller_id(Request(scope))})
            await response(scope, receive, send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=ProxyHeadersMiddleware(echo, trusted_hosts=trusted_hosts), client=peer,
            ), base_url="https://testserver",
        ) as client:
            response = await client.get("/", headers=headers)
        return response.json()["caller"]

    async def test_direct_clients_cannot_override_identity(self):
        for peer in ("203.0.113.5", "2001:db8::5"):
            for value in ("198.51.100.77", "198.51.100.77, 127.0.0.1", "", "garbage"):
                with self.subTest(peer=peer, forwarded=value):
                    self.assertEqual(await self.caller(
                        (peer, 1234), {
                            "X-Forwarded-For": value, "X-Visitor-Id": value,
                            "X-Real-IP": value, "Forwarded": f"for={value}",
                        },
                    ), f"ip:{peer}")

    async def test_trusted_proxy_uses_rightmost_untrusted_client(self):
        for actual in ("203.0.113.5", "2001:db8::5"):
            for forged in ("198.51.100.77", "garbage", "", "192.0.2.4, 198.51.100.77"):
                with self.subTest(actual=actual, forged=forged):
                    self.assertEqual(await self.caller(
                        ("127.0.0.1", 1234),
                        {"X-Forwarded-For": f"{forged}, {actual}, 127.0.0.1",
                         "X-Visitor-Id": forged},
                    ), f"ip:{actual}")

    async def test_duplicate_forwarded_headers_do_not_override_appended_client(self):
        self.assertEqual(await self.caller(
            ("127.0.0.1", 1234),
            [("X-Forwarded-For", "198.51.100.77"),
             ("X-Forwarded-For", "203.0.113.5, 127.0.0.1")],
        ), "ip:203.0.113.5")

    async def test_explicit_proxy_chain_preserves_distinct_legitimate_clients(self):
        for actual in ("203.0.113.5", "203.0.113.6", "2001:db8::6"):
            self.assertEqual(await self.caller(
                ("127.0.0.1", 1234),
                {"X-Forwarded-For": f"198.51.100.77, {actual}, 10.0.0.2, 127.0.0.1"},
                trusted_hosts=["127.0.0.1", "10.0.0.2"],
            ), f"ip:{actual}")

    async def test_missing_or_empty_forwarding_uses_transport_peer(self):
        for headers in ({}, {"X-Forwarded-For": ""}):
            self.assertEqual(await self.caller(("127.0.0.1", 1234), headers), "ip:127.0.0.1")

    def test_missing_peer_cannot_be_replaced_by_headers(self):
        request = Request({
            "type": "http", "client": None,
            "headers": [(b"x-forwarded-for", b"198.51.100.77"), (b"x-visitor-id", b"forged")],
        })
        self.assertEqual(server._caller_id(request), "ip:unknown")

    def test_legacy_environment_opt_in_is_disabled_at_boot(self):
        # Fresh import, isolated storage, and no provider/network calls.
        with tempfile.TemporaryDirectory() as data:
            result = subprocess.run(
                [sys.executable, "-c",
                 f"import os, sys; sys.path[:0] = {sys.path!r}; "
                 "os.environ.update(STORE='sqlite', TRUST_VISITOR_HEADER='1', "
                 "LLM_PROVIDER='mock', IMAGE_PROVIDER='none'); "
                 "import server; assert server.TRUST_VISITOR_HEADER is False"],
                env={"PATH": os.defpath, "MANIFEST_DATA_DIR": data},
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


class CallerQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(self.tempdir.name)
        self.patches = [
            patch.object(server, "store", self.store),
            patch.object(server, "RENDER_COUNT", 0),
            patch.object(server, "app_owner_token", AsyncMock(return_value=None)),
            patch.object(server, "_MIX_HITS", defaultdict(deque)),
            patch.object(server, "_MIX_GLOBAL", deque()),
            # Even old local fixtures/configuration cannot reactivate the unsafe branches.
            patch.object(server, "TRUST_VISITOR_HEADER", True),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.store._db.close()
        self.tempdir.cleanup()

    def client(self, peer="203.0.113.5", cookie=OWNER):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app, client=(peer, 1234)),
            base_url="https://testserver",
            headers={"Cookie": f"{server.COOKIE}={cookie}"} if cookie else {},
        )

    async def test_rotating_headers_and_cookies_cannot_reset_generation_limits(self):
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())

        # Exercise the unchanged default thresholds, both 3/hour and 8/day.
        for reason, count in (("hour", 3), ("day", 8)):
            self.store._db.execute("DELETE FROM generation_admissions")
            self.store._db.execute("DELETE FROM ventures")
            self.store._db.commit()
            with (
                patch.object(server, "GEN_PER_HOUR", 3),
                patch.object(server, "GEN_PER_DAY", 8),
                patch.object(server, "GLOBAL_PER_DAY", 150),
                patch.object(server, "is_app_owner", AsyncMock(return_value=False)),
                patch.object(server.llm, "stream_venture_text", output),
            ):
                for index in range(count):
                    token = f"{reason}-{index}"
                    self.assertIsNone(await self.store.reserve_generation(
                        token, "ip:203.0.113.5", 100, 100, None,
                    ))
                if reason == "day":
                    self.store._db.execute(
                        "UPDATE generation_admissions SET consumed=1, admitted_at=?",
                        (time.time() - 7200,),
                    )
                self.store._db.commit()
                for index in range(3):
                    async with self.client(cookie=f"rotated-cookie-token-{index}") as client:
                        response = await client.post(
                            "/api/manifest", json={"idea": "A compact wall rail for tiny workshops"},
                            headers={"X-Visitor-Id": f"forged-{index}",
                                     "X-Forwarded-For": f"198.51.100.{index + 1}"},
                        )
                    self.assertEqual(response.status_code, 429, response.text)
                    self.assertIn("last hour" if reason == "hour" else "today", response.text)
                # A different transport client still has its own allowance.
                async with self.client(peer="203.0.113.6") as client:
                    response = await client.post(
                        "/api/manifest", json={"idea": "A compact wall rail for tiny workshops"},
                    )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("event: done", response.text)

    async def test_refinement_uses_same_caller_despite_forged_headers(self):
        row = await self.store.create_venture(venture())
        self.assertIsNone(await self.store.reserve_generation(
            "occupied", "ip:203.0.113.5", 1, 8, None,
        ))
        with (
            patch.object(server, "GEN_PER_HOUR", 1),
            patch.object(server, "is_app_owner", AsyncMock(return_value=False)),
            patch.object(server.llm, "stream_refinement_text") as provider,
        ):
            async with self.client() as client:
                response = await client.post(
                    f"/api/ventures/{row['slug']}/refine", json={"context": "Make it smaller"},
                    headers={"X-Visitor-Id": "new-visitor", "X-Forwarded-For": "198.51.100.77"},
                )
        self.assertEqual(response.status_code, 429, response.text)
        provider.assert_not_called()

    async def test_mix_headers_cannot_reset_quota_or_grant_owner_exemption(self):
        server._MIX_HITS["ip:203.0.113.5"].extend([time.time()] * 6)
        with (
            patch.object(server, "is_app_owner", AsyncMock(return_value=False)),
            patch.object(server.mixer, "research", AsyncMock()) as provider,
        ):
            async with self.client() as client:
                for index in range(2):
                    response = await client.post(
                        "/api/mix/research", json={"topic": "Small workshops"},
                        headers={"X-Visitor-Id": f"forged-{index}",
                                 "X-Forwarded-For": f"198.51.100.{index + 1}"},
                    )
                    self.assertEqual(response.status_code, 429, response.text)
        provider.assert_not_awaited()
        self.assertEqual(len(server._MIX_HITS), 1)

    async def test_visitor_header_cannot_claim_creator_access_or_suppress_cookie(self):
        row = await self.store.create_venture(venture())
        async with self.client(cookie=None) as client:
            response = await client.get(
                f"/api/ventures/{row['slug']}", headers={"X-Visitor-Id": OWNER},
            )
            self.assertIn(server.COOKIE, response.headers.get("set-cookie", ""))
        self.assertEqual(response.status_code, 404, response.text)
        async with self.client() as client:
            response = await client.get(f"/api/ventures/{row['slug']}")
        self.assertEqual(response.status_code, 200, response.text)

    async def test_verified_owner_remains_exempt_with_forged_headers_present(self):
        async def output(*args, **kwargs):
            yield json.dumps(refined_output())

        with (
            patch.object(server, "is_app_owner", AsyncMock(return_value=True)),
            patch.object(server, "GEN_PER_HOUR", 0),
            patch.object(server, "GEN_PER_DAY", 0),
            patch.object(server, "GLOBAL_PER_DAY", 0),
            patch.object(server.llm, "stream_venture_text", output),
            patch.object(self.store, "reserve_generation", AsyncMock()) as reserve,
        ):
            async with self.client() as client:
                response = await client.post(
                    "/api/manifest", json={"idea": "A compact wall rail for tiny workshops"},
                    headers={"X-Visitor-Id": "forged", "X-Forwarded-For": "198.51.100.77"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("event: done", response.text)
        reserve.assert_not_awaited()