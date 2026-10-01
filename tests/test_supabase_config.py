"""Supabase key configuration tests; credentials and HTTP are test fixtures."""

import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.store import SupabaseStore, build_store


class SupabaseConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_sqlite_preserves_local_storage_with_supabase_secrets(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(os.environ, {
                "STORE": "sqlite",
                "MANIFEST_DATA_DIR": data_dir,
                "SUPABASE_URL": "https://test.invalid",
                "SUPABASE_SECRET_KEY": "sb_secret_test-only",
            }, clear=True):
                store = build_store()
            try:
                self.assertEqual(store.kind, "sqlite")
            finally:
                store._db.close()

    async def test_explicit_supabase_without_secrets_fails(self):
        with patch.dict(os.environ, {"STORE": "supabase"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Supabase requires"):
                build_store()

    async def test_bare_project_hostname_gets_https(self):
        store = SupabaseStore("example.supabase.co/", "sb_secret_test-only")
        try:
            self.assertEqual(store.url, "https://example.supabase.co")
        finally:
            await store._client.aclose()

    async def test_invalid_url_fails_without_disclosing_value(self):
        with self.assertRaisesRegex(ValueError, "SUPABASE_URL must be"):
            SupabaseStore("invalid-test-value", "sb_secret_test-only")

    async def test_modern_secret_uses_apikey_without_bearer(self):
        store = SupabaseStore("https://test.invalid", "sb_secret_test-only")
        try:
            self.assertEqual(store._client.headers["apikey"], "sb_secret_test-only")
            self.assertNotIn("Authorization", store._client.headers)
        finally:
            await store._client.aclose()

    async def test_legacy_service_role_retains_bearer(self):
        store = SupabaseStore("https://test.invalid", "legacy-test-only")
        try:
            self.assertEqual(
                store._client.headers["Authorization"], "Bearer legacy-test-only"
            )
        finally:
            await store._client.aclose()

    async def test_modern_secret_environment_selects_supabase(self):
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://test.invalid",
            "SUPABASE_SECRET_KEY": "sb_secret_test-only",
            "SUPABASE_SERVICE_ROLE_KEY": "legacy-test-only",
        }, clear=True):
            store = build_store()
        try:
            self.assertEqual(store.kind, "supabase")
            self.assertEqual(store.key, "sb_secret_test-only")
        finally:
            await store._client.aclose()

    async def test_legacy_environment_still_selects_supabase(self):
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://test.invalid",
            "SUPABASE_SERVICE_ROLE_KEY": "legacy-test-only",
        }, clear=True):
            store = build_store()
        try:
            self.assertEqual(store.kind, "supabase")
            self.assertEqual(store.key, "legacy-test-only")
        finally:
            await store._client.aclose()

    async def test_slug_check_uses_same_modern_auth_headers(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json=[{"id": "test-only"}])

        store = SupabaseStore("https://test.invalid", "sb_secret_test-only")
        client = httpx.Client(transport=httpx.MockTransport(respond))
        try:
            with patch("app.store.httpx.Client", return_value=client):
                self.assertTrue(store.slug_exists("test-venture"))
            self.assertEqual(requests[0].headers["apikey"], "sb_secret_test-only")
            self.assertNotIn("Authorization", requests[0].headers)
        finally:
            client.close()
            await store._client.aclose()