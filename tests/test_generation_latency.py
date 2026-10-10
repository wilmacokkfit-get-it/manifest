"""Latency, explicit reasoning settings, stream failure and bounded resource cleanup."""
import asyncio
import json
import os
import unittest
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import llm


class LatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_operations_emit_content_free_timings(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "mock"}):
            for stream in (
                llm.stream_venture_text("PRIVATE INPUT NEVER LOGGED"),
                llm.stream_refinement_text(llm.MOCK_VENTURE, "PRIVATE CONTEXT"),
                llm.stream_path_text(llm.MOCK_VENTURE, 0),
            ):
                with self.assertLogs(llm.log.name, level="INFO") as records:
                    async for _ in stream:
                        pass
                metric = json.loads(records.output[-1].split("llm_timing ", 1)[1])
                self.assertEqual(metric["outcome"], "completed")
                self.assertGreaterEqual(metric["elapsed_seconds"], metric["first_text_seconds"])
                self.assertGreater(metric["output_chars"], 0)
                self.assertNotIn("PRIVATE", records.output[-1])

    async def test_closing_consumer_closes_provider_and_records_interruption(self):
        closed = asyncio.Event()
        async def provider(*args, **kwargs):
            try:
                yield "first"
                await asyncio.Event().wait()
            finally:
                closed.set()
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}), patch.object(llm, "_stream_openai", provider):
            with self.assertLogs(llm.log.name, level="INFO") as records:
                async with aclosing(llm.stream_venture_text("synthetic input")) as stream:
                    self.assertEqual(await anext(stream), "first")
            self.assertTrue(closed.is_set())
            self.assertIn('"outcome": "cancelled"', records.output[-1])

    async def test_timeout_before_first_text_records_no_token_and_closes_client(self):
        class HangingStream:
            def __aiter__(self): return self
            async def __anext__(self):
                await asyncio.Event().wait()
            close = AsyncMock()
        events = HangingStream()
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=events)), close=AsyncMock())
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}), patch("openai.AsyncOpenAI", return_value=client):
            with self.assertLogs(llm.log.name, level="INFO") as records:
                with self.assertRaises(TimeoutError):
                    async with asyncio.timeout(.01):
                        async for _ in llm.stream_venture_text("synthetic input"):
                            pass
            metric = json.loads(records.output[-1].split("llm_timing ", 1)[1])
            self.assertIsNone(metric["first_text_seconds"])
            events.close.assert_awaited_once()
            client.close.assert_awaited_once()

    async def test_failure_and_early_eof_rejected_for_every_operation(self):
        for terminal in ("response.failed", "response.incomplete", "error", None):
            async def events():
                yield SimpleNamespace(type="response.output_text.delta", delta='{"name":"Partial"}')
                if terminal:
                    yield SimpleNamespace(type=terminal)
            client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=lambda **kw: events())))
            with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}), patch("openai.AsyncOpenAI", return_value=client):
                for make in (
                    lambda: llm.stream_venture_text("synthetic input"),
                    lambda: llm.stream_refinement_text(llm.MOCK_VENTURE, "synthetic context"),
                    lambda: llm.stream_path_text(llm.MOCK_VENTURE, 0),
                ):
                    with self.subTest(terminal=terminal), self.assertRaises(RuntimeError):
                        async for _ in make(): pass

    def test_effort_is_explicit_and_invalid_setting_fails(self):
        for effort in ("low", "medium", "high"):
            with patch.dict(os.environ, {"LLM_REASONING_EFFORT": effort}):
                self.assertEqual(llm.reasoning_options("gpt-5-mini"), {"reasoning": {"effort": effort}})
                self.assertEqual(llm.reasoning_options("gpt-4.1"), {})
        with patch.dict(os.environ, {"LLM_REASONING_EFFORT": "typo"}), self.assertRaises(ValueError):
            llm.reasoning_options("gpt-5-mini")

    def test_invalid_provider_cannot_silently_use_mock(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "typo"}), self.assertRaisesRegex(ValueError, "no provider fallback"):
            llm.text_provider()

    async def test_anthropic_truncation_is_rejected_and_client_is_closed(self):
        class Stream:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            @property
            def text_stream(self):
                async def text():
                    yield '{"name":"Partial"}'
                return text()
            get_final_message = AsyncMock(return_value=SimpleNamespace(stop_reason="max_tokens"))
        client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs: Stream()), close=AsyncMock())
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}), patch("anthropic.AsyncAnthropic", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "could not finish"):
                async for _ in llm.stream_venture_text("synthetic input"): pass
            client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
