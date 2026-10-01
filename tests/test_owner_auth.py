"""Offline security tests for optional Clerk app-owner identity."""

from __future__ import annotations

import base64
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app import owner_auth


def request_with_cookie(token: str | None = "signed-session", host: str = "manifest.example") -> Request:
    headers = [(b"host", host.encode()), (b"x-forwarded-proto", b"https")]
    if token:
        headers.append((b"cookie", f"__session={token}".encode()))
    return Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/owner-auth/session",
        "raw_path": b"/api/owner-auth/session",
        "query_string": b"",
        "headers": headers,
        "server": (host, 443),
        "client": ("127.0.0.1", 12345),
    })


class OwnerAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "MANIFEST_OWNER_EMAIL": "owner@example.com",
                "CLERK_SECRET_KEY": "test-secret",
                "VITE_CLERK_PUBLISHABLE_KEY": "test-publishable",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    async def test_owner_requires_a_session_cookie(self):
        with patch.object(owner_auth, "_decode_session_token", new_callable=AsyncMock) as decode:
            self.assertFalse(await owner_auth.is_app_owner(request_with_cookie(None)))
            decode.assert_not_awaited()

    async def test_owner_allowlist_uses_verified_primary_email(self):
        claims = {"sub": "user_123", "sid": "sess_123"}
        with (
            patch.object(owner_auth, "_decode_session_token", new_callable=AsyncMock, return_value=claims),
            patch.object(owner_auth, "_verified_primary_email", new_callable=AsyncMock, return_value="Owner@Example.com"),
        ):
            self.assertTrue(await owner_auth.is_app_owner(request_with_cookie()))

    async def test_non_owner_and_unverified_account_do_not_get_privileges(self):
        claims = {"sub": "user_123", "sid": "sess_123"}
        with (
            patch.object(owner_auth, "_decode_session_token", new_callable=AsyncMock, return_value=claims),
            patch.object(owner_auth, "_verified_primary_email", new_callable=AsyncMock, return_value="visitor@example.com"),
        ):
            self.assertFalse(await owner_auth.is_app_owner(request_with_cookie()))
        with (
            patch.object(owner_auth, "_decode_session_token", new_callable=AsyncMock, return_value=claims),
            patch.object(owner_auth, "_verified_primary_email", new_callable=AsyncMock, return_value=None),
        ):
            self.assertFalse(await owner_auth.is_app_owner(request_with_cookie()))

    async def test_missing_owner_email_fails_closed(self):
        claims = {"sub": "user_123", "sid": "sess_123"}
        with (
            patch.dict(os.environ, {"MANIFEST_OWNER_EMAIL": ""}),
            patch.object(owner_auth, "_decode_session_token", new_callable=AsyncMock) as decode,
        ):
            self.assertFalse(await owner_auth.is_app_owner(request_with_cookie()))
            decode.assert_not_awaited()

    async def test_primary_address_must_match_and_be_verified_in_clerk(self):
        session = {"id": "sess_123", "status": "active", "user_id": "user_123"}
        user = {
            "id": "user_123",
            "primary_email_address_id": "email_primary",
            "email_addresses": [
                {
                    "id": "email_primary",
                    "email_address": "owner@example.com",
                    "verification": {"status": "verified"},
                },
                {
                    "id": "email_other",
                    "email_address": "attacker@example.com",
                    "verification": {"status": "verified"},
                },
            ],
        }
        with patch.object(owner_auth, "_clerk_api_get", new_callable=AsyncMock, side_effect=[session, user]):
            self.assertEqual(
                await owner_auth._verified_primary_email("sess_123", "user_123"),
                "owner@example.com",
            )
        user["email_addresses"][0]["verification"]["status"] = "unverified"
        with patch.object(owner_auth, "_clerk_api_get", new_callable=AsyncMock, side_effect=[session, user]):
            self.assertIsNone(await owner_auth._verified_primary_email("sess_123", "user_123"))

    async def test_session_must_be_active_and_belong_to_claimed_user(self):
        with patch.object(
            owner_auth, "_clerk_api_get", new_callable=AsyncMock,
            return_value={"status": "revoked", "user_id": "user_123"},
        ) as get:
            self.assertIsNone(await owner_auth._verified_primary_email("sess_123", "user_123"))
            get.assert_awaited_once()

    async def test_issuer_is_derived_from_the_clerk_publishable_key(self):
        encoded = base64.urlsafe_b64encode(b"owner.clerk.accounts.dev$").decode().rstrip("=")
        self.assertEqual(
            owner_auth._issuer_from_publishable_key(f"pk_test_{encoded}"),
            "https://owner.clerk.accounts.dev",
        )
        self.assertIsNone(owner_auth._issuer_from_publishable_key("not-a-key"))

    async def test_publishable_key_matches_clerk_host_helper_semantics(self):
        dev_host_key = "pk_test_" + base64.urlsafe_b64encode(
            b"dev-app-1.clerk.accounts.dev$"
        ).decode().rstrip("=")
        with (
            patch.dict(
                os.environ,
                {
                    "VITE_CLERK_PUBLISHABLE_KEY": "pk_live_ignored",
                },
            ),
        ):
            expected = "pk_live_" + base64.urlsafe_b64encode(
                b"clerk.manifest.example$"
            ).decode().rstrip("=")
            self.assertEqual(owner_auth._publishable_key(request_with_cookie()), expected)
            expected_dev_hostname = "pk_live_" + base64.urlsafe_b64encode(
                b"clerk.dev-app-1.clerk.accounts.dev$"
            ).decode().rstrip("=")
            self.assertEqual(
                owner_auth._publishable_key(request_with_cookie(host="dev-app-1.clerk.accounts.dev")),
                expected_dev_hostname,
            )

        with patch.dict(os.environ, {"VITE_CLERK_PUBLISHABLE_KEY": dev_host_key}):
            self.assertEqual(
                owner_auth._publishable_key(request_with_cookie(host="localhost:5000")),
                dev_host_key,
            )

    async def test_owner_config_uses_auto_proxy_and_request_specific_key(self):
        with patch.dict(
            os.environ,
            {
                "VITE_CLERK_PUBLISHABLE_KEY": "pk_live_configured-fallback",
                "VITE_CLERK_PROXY_URL": "https://auth-proxy.example.test",
                "NODE_ENV": "",
            },
        ):
            config = await owner_auth.owner_auth_config(
                request_with_cookie(host="www.manifest.example")
            )
        expected_key = "pk_live_" + base64.urlsafe_b64encode(
            b"clerk.www.manifest.example$"
        ).decode().rstrip("=")
        self.assertEqual(config["publishableKey"], expected_key)
        self.assertEqual(config["proxyUrl"], "https://auth-proxy.example.test")
        self.assertEqual(config["frontendApiHost"], "clerk.www.manifest.example")

    async def test_session_verifies_signature_expiry_issuer_and_authorized_party(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        request = request_with_cookie()
        key = owner_auth._publishable_key(request)
        issuer = owner_auth._issuer_from_publishable_key(key)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = owner_auth.jwt.algorithms.RSAAlgorithm.to_jwk(
            private_key.public_key(), as_dict=True,
        )
        public_jwk["kid"] = "test-key"
        claims = {
            "sub": "user_123",
            "sid": "sess_123",
            "iss": issuer,
            "exp": int(time.time()) + 120,
            "azp": "https://manifest.example",
        }
        with patch.object(owner_auth, "_jwks", new_callable=AsyncMock, return_value={"keys": [public_jwk]}):
            token = owner_auth.jwt.encode(
                claims, private_key, algorithm="RS256", headers={"kid": "test-key"},
            )
            self.assertEqual(await owner_auth._decode_session_token(token, request), claims)

            impostor_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            forged_token = owner_auth.jwt.encode(
                claims, impostor_key, algorithm="RS256", headers={"kid": "test-key"},
            )
            self.assertIsNone(await owner_auth._decode_session_token(forged_token, request))

            expired = {**claims, "exp": int(time.time()) - 10}
            expired_token = owner_auth.jwt.encode(
                expired, private_key, algorithm="RS256", headers={"kid": "test-key"},
            )
            self.assertIsNone(await owner_auth._decode_session_token(expired_token, request))

            wrong_issuer = {**claims, "iss": "https://attacker.example"}
            wrong_issuer_token = owner_auth.jwt.encode(
                wrong_issuer, private_key, algorithm="RS256", headers={"kid": "test-key"},
            )
            self.assertIsNone(await owner_auth._decode_session_token(wrong_issuer_token, request))

            wrong_azp = {**claims, "azp": "https://attacker.example"}
            wrong_azp_token = owner_auth.jwt.encode(
                wrong_azp, private_key, algorithm="RS256", headers={"kid": "test-key"},
            )
            self.assertIsNone(await owner_auth._decode_session_token(wrong_azp_token, request))

    async def test_owner_token_is_stable_per_account_and_cached_per_request(self):
        claims = {
            "sub": "user_123",
            "sid": "sess_123",
        }
        with (
            patch.object(owner_auth, "_decode_session_token", new_callable=AsyncMock, return_value=claims),
            patch.object(owner_auth, "_verified_primary_email", new_callable=AsyncMock, return_value="owner@example.com"),
        ):
            first_request = request_with_cookie()
            first = await owner_auth.app_owner_token(first_request)
            self.assertEqual(await owner_auth.app_owner_token(first_request), first)
            self.assertIsNotNone(first)
            self.assertEqual(await owner_auth.app_owner_token(request_with_cookie()), first)