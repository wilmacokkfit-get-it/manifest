"""Owner-checked delivery using real SQLite and mocked Supabase HTTP."""

import tempfile
import unittest

import httpx

from app.store import SQLiteStore, SupabaseStore
from test_regressions import private_venture, server


class ImageAccessChecks:
    def client(self):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="https://testserver",
        )

    async def test_owner_outsider_and_release_access(self):
        before = await self.store.get_by_id(self.row["id"])
        guarded = self.store.render_url(self.row, 0)
        urls = [guarded]
        if self.store.kind == "sqlite":
            urls.append(self.row["images"][0])

        async with self.client() as client:
            for url in urls:
                for cookie in (None, "someone-elses-token"):
                    client.cookies.clear()
                    headers = {"Cookie": f"{server.COOKIE}={cookie}"} if cookie else {}
                    denied = await client.get(url, headers=headers)
                    self.assertEqual(denied.status_code, 404)
                    self.assertEqual(denied.headers["cache-control"], "private, no-store")
                    self.assertNotIn(self.image, denied.content)
                self.assertEqual(self.downloads, [])
                owner = await client.get(url, headers=self.owner_headers)
                self.assertEqual(owner.status_code, 200)
                self.assertEqual(owner.content, self.image)
                self.assertEqual(owner.headers["content-type"], "image/png")
                self.assertEqual(owner.headers["cache-control"], "private, no-store")
                self.assertEqual(owner.headers["x-content-type-options"], "nosniff")
                self.downloads.clear()

            detail = await client.get(
                f"/api/ventures/{self.row['slug']}", headers=self.owner_headers,
            )
            self.assertEqual(detail.json()["images"], [guarded])
            self.assertEqual(await self.store.get_by_id(self.row["id"]), before)
            # No private image appears in share metadata.
            page = await client.get(f"/v/{self.row['slug']}")
            self.assertNotIn(guarded, page.text)
            released = await client.post(
                f"/api/ventures/{self.row['slug']}/release", headers=self.owner_headers,
            )
            self.assertEqual(released.status_code, 200)
            self.assertEqual(released.json()["images"], [guarded])
            for url in urls:
                client.cookies.clear()
                public = await client.get(url)
                self.assertEqual(public.status_code, 200)
                self.assertEqual(public.content, self.image)
                outsider = await client.get(
                    url, headers={"Cookie": f"{server.COOKIE}=someone-elses-token"},
                )
                self.assertEqual(outsider.status_code, 200)
            page = await client.get(f"/v/{self.row['slug']}")
            self.assertIn(guarded, page.text)
            self.assertNotIn("/storage/v1/object/public/", page.text)
            self.assertEqual(server._card_view(await self.store.get_by_id(self.row["id"]))["image"], guarded)

    async def test_missing_unregistered_and_unsafe_images_are_denied(self):
        root = f"/api/venture-images/{self.row['id']}"
        async with self.client() as client:
            for url in (
                f"{root}/1.png", f"{root}/-1.png", f"{root}/0.webp", f"{root}/0.jpg",
                "/api/venture-images/unknown/0.png",
                "/renders/%2e%2e/manifest.db", "/renders/orphan-0.png",
            ):
                response = await client.get(url, headers=self.owner_headers)
                self.assertEqual(response.status_code, 404, url)
            self.assertEqual(self.downloads, [])
            for reference in (
                "https://attacker.invalid/image.png",
                self.row["images"][0].replace(self.row["id"], "another-venture"),
                "/api/venture-images/../../private/0.png",
            ):
                v = {**self.row, "images": [reference]}
                self.assertIsNone(self.store.render_url(v, 0))
                self.assertIsNone(await self.store.read_render(v, 0))
            self.assertEqual(self.downloads, [])

    async def test_new_render_reference_is_protected(self):
        url = await self.store.save_render(self.row["id"], 1, self.image)
        updated = await self.store.update_venture(
            self.row["id"], {"images": [self.row["images"][0], url]},
        )
        guarded = self.store.render_url(updated, 1)
        self.assertTrue(guarded.startswith("/api/venture-images/"))
        self.assertNotIn("object/public", url)
        async with self.client() as client:
            self.assertEqual((await client.get(guarded)).status_code, 404)
            owner = await client.get(guarded, headers=self.owner_headers)
            self.assertEqual(owner.status_code, 200)
            self.assertEqual(owner.content, self.image)


class SQLiteImageTests(ImageAccessChecks, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(self.temp.name)
        self.original_store = server.store
        self.original_trust = server.TRUST_VISITOR_HEADER
        server.store = self.store
        server.TRUST_VISITOR_HEADER = False
        self.owner_headers = {"Cookie": f"{server.COOKIE}=owner-token-for-test"}
        self.image = b"test-only-png-bytes"
        row = private_venture()
        legacy = f"/renders/{row['id']}-0.png"
        (self.store.renders_dir / f"{row['id']}-0.png").write_bytes(self.image)
        self.row = await self.store.create_venture({**row, "images": [legacy]})
        self.downloads = []

    async def asyncTearDown(self):
        server.store = self.original_store
        server.TRUST_VISITOR_HEADER = self.original_trust
        self.store._db.close()
        self.temp.cleanup()

    async def test_webp_and_missing_file_and_symlink(self):
        path = self.store.renders_dir / f"{self.row['id']}-0.webp"
        path.write_bytes(self.image)
        row = await self.store.update_venture(
            self.row["id"], {"images": [f"/renders/{path.name}"]},
        )
        url = self.store.render_url(row, 0)
        async with self.client() as client:
            response = await client.get(url, headers=self.owner_headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "image/webp")
            path.unlink()
            self.assertEqual((await client.get(url, headers=self.owner_headers)).status_code, 404)
            path.symlink_to(self.store.dir / "manifest.db")
            self.assertEqual((await client.get(url, headers=self.owner_headers)).status_code, 404)


class SupabaseImageTests(ImageAccessChecks, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SupabaseStore("https://test.invalid", "sb_secret_test-only", "custom-renders")
        await self.store._client.aclose()
        self.original_store = server.store
        self.original_trust = server.TRUST_VISITOR_HEADER
        server.store = self.store
        server.TRUST_VISITOR_HEADER = False
        self.owner_headers = {"Cookie": f"{server.COOKIE}=owner-token-for-test"}
        self.image = b"test-only-png-bytes"
        self.row = private_venture()
        self.row["images"] = [
            f"{self.store.url}/storage/v1/object/public/custom-renders/{self.row['id']}/0.png",
        ]
        self.requests = []
        self.downloads = []
        self.bucket_public = True
        self.storage = {f"{self.row['id']}/0.png": self.image}
        self.store._client = httpx.AsyncClient(
            base_url=self.store.url, headers=self.store._auth_headers,
            transport=httpx.MockTransport(self.respond),
        )

    def respond(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/storage/v1/bucket/custom-renders":
            if request.method == "PUT":
                import json
                self.bucket_public = json.loads(request.content)["public"]
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"public": self.bucket_public})
        if path == "/rest/v1/ventures":
            if request.method == "PATCH":
                import json
                self.row.update(json.loads(request.content))
            if "id" in request.url.params and request.url.params["id"] != f"eq.{self.row['id']}":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[dict(self.row)])
        if path in ("/rest/v1/signals", "/rest/v1/needs"):
            return httpx.Response(200, json=[])
        download_prefix = "/storage/v1/object/authenticated/custom-renders/"
        upload_prefix = "/storage/v1/object/custom-renders/"
        if path.startswith(download_prefix):
            self.downloads.append(request)
            self.assertEqual(request.headers["apikey"], "sb_secret_test-only")
            key = path.removeprefix(download_prefix)
            if key not in self.storage:
                return httpx.Response(404)
            return httpx.Response(200, content=self.storage[key])
        if path.startswith(upload_prefix) and request.method == "POST":
            self.assertEqual(request.headers["cache-control"], "0")
            self.storage[path.removeprefix(upload_prefix)] = request.content
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    async def asyncTearDown(self):
        server.store = self.original_store
        server.TRUST_VISITOR_HEADER = self.original_trust
        await self.store._client.aclose()

    async def test_startup_privatizes_existing_bucket_without_changing_data(self):
        objects = dict(self.storage)
        row = dict(self.row)
        async with server.app.router.lifespan_context(server.app):
            self.assertFalse(self.bucket_public)
        self.assertEqual(self.storage, objects)
        self.assertEqual(self.row, row)
        self.assertEqual([r.method for r in self.requests], ["PUT", "GET"])
        self.assertTrue(all(r.url.path == "/storage/v1/bucket/custom-renders" for r in self.requests))

    async def test_startup_fails_closed_if_bucket_cannot_be_secured(self):
        for response in (httpx.Response(403), httpx.Response(200, json={"public": True})):
            await self.store._client.aclose()
            self.store._client = httpx.AsyncClient(
                base_url=self.store.url,
                transport=httpx.MockTransport(lambda request: response),
            )
            with self.assertRaises(RuntimeError):
                async with server.app.router.lifespan_context(server.app):
                    self.fail("App must not serve with a public or unverified bucket")

    async def test_webp_and_missing_storage_object(self):
        self.row["images"] = [
            f"{self.store.url}/storage/v1/object/public/custom-renders/{self.row['id']}/0.webp",
        ]
        self.storage[f"{self.row['id']}/0.webp"] = self.image
        url = self.store.render_url(self.row, 0)
        async with self.client() as client:
            response = await client.get(url, headers=self.owner_headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "image/webp")
            self.storage.clear()
            self.assertEqual((await client.get(url, headers=self.owner_headers)).status_code, 404)
            # A local file URL cannot bypass authorization on the Supabase backend.
            response = await client.get(f"/renders/{self.row['id']}-0.png", headers=self.owner_headers)
            self.assertEqual(response.status_code, 404)