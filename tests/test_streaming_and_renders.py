"""Isolated keep-alive, cancellation and render-provider checks."""
import asyncio
import base64
import unittest
import anyio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import llm
from app.render_brief import render_brief
from app.streaming import with_heartbeats


class StreamingAndRenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_quiet_source_keeps_alive_without_restarting_work(self):
        release = asyncio.Event()
        calls = []
        async def source():
            calls.append("started")
            await release.wait()
            yield 'event: done\ndata: {}\n\n'
        stream = with_heartbeats(source(), interval=0.01)
        self.assertEqual(await anext(stream), ": still-working\n\n")
        self.assertEqual(await anext(stream), ": still-working\n\n")
        release.set()
        self.assertIn("event: done", await anext(stream))
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)
        self.assertEqual(calls, ["started"])

    async def test_disconnect_cancels_and_closes_source(self):
        cleaned = asyncio.Event()
        async def source():
            try:
                await asyncio.Event().wait()
                yield ""
            finally:
                cleaned.set()
        stream = with_heartbeats(source(), interval=0.01)
        await anext(stream)
        await stream.aclose()
        self.assertTrue(cleaned.is_set())

    async def test_source_errors_are_not_hidden_by_heartbeats(self):
        async def source():
            raise RuntimeError("test provider failure")
            yield ""
        with self.assertRaisesRegex(RuntimeError, "test provider failure"):
            await anext(with_heartbeats(source()))

    async def test_anyio_scope_spanning_yields_stays_in_one_task(self):
        async def source():
            with anyio.fail_after(1):
                yield "first"
                await asyncio.sleep(0.02)
                yield "second"
        received = [item async for item in with_heartbeats(source(), interval=0.005)]
        self.assertEqual([item for item in received if not item.startswith(":")], ["first", "second"])

    def test_render_direction_preserves_mechanism_and_distinct_views(self):
        venture = {"name": "Petal", "description": "Six folding tool compartments borrow origami hinges.",
                   "grounding": {"exists": "Compared with a loose tray, folded compartments travel together."}}
        hero = render_brief(venture, "Cork petals, brass hinges, teal cord", 0)
        action = render_brief(venture, "Same organizer opened on a workbench", 1)
        self.assertIn(venture["description"], hero)
        self.assertIn("Cork petals", hero)
        self.assertIn("hero view", hero)
        self.assertIn("real-world action", action)
        self.assertIn("Keep the same product form", action)
        self.assertIn("do not change the mechanism", hero)
        self.assertNotEqual(hero, action)

    async def test_image_provider_is_bounded_and_closed_on_success_and_failure(self):
        create = AsyncMock(return_value=SimpleNamespace(data=[
            SimpleNamespace(b64_json=base64.b64encode(b"synthetic-image").decode())
        ]))
        close = AsyncMock()
        client = SimpleNamespace(images=SimpleNamespace(generate=create), close=close)
        with patch.dict("os.environ", {"IMAGE_PROVIDER": "openai"}), patch(
            "openai.AsyncOpenAI", return_value=client
        ) as provider:
            self.assertEqual(await llm.generate_render("A specific concept"), b"synthetic-image")
            provider.assert_called_once_with(timeout=180, max_retries=0)
            create.side_effect = RuntimeError("provider failure")
            with self.assertRaisesRegex(RuntimeError, "provider failure"):
                await llm.generate_render("Another concept")
        self.assertEqual(close.await_count, 2)


if __name__ == "__main__":
    unittest.main()
