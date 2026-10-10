"""Run without server.py, credentials, providers, or durable storage."""
import asyncio
import os
import time
import unittest
from unittest.mock import patch

from app.wait_probe import ProbeResponse, enabled


class WaitProbeTests(unittest.IsolatedAsyncioTestCase):
    async def run_probe(self, scenario, *, disconnect_after=None, delay=0.03):
        messages = []
        inbox = asyncio.Queue()
        response = ProbeResponse(scenario, delay, "test")
        task = asyncio.create_task(response({"type": "http"}, inbox.get, lambda message: self.send(messages, message)))
        if disconnect_after is not None:
            await asyncio.sleep(disconnect_after)
            await inbox.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 1)
        return messages

    async def send(self, messages, message):
        messages.append((time.monotonic(), message))

    def test_gate_is_short_lived_and_fail_closed(self):
        for value, expected in [("0", False), ("bad", False),
                                (str(time.time() + 8000), False),
                                (str(time.time() + 60), True)]:
            with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": value}):
                self.assertEqual(enabled(), expected)

    async def test_disabled_response(self):
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": "0"}):
            self.assertEqual((await self.run_probe("first-byte"))[0][1]["status"], 404)

    async def test_delayed_headers_and_json(self):
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 60)}):
            for scenario in ("first-byte", "json"):
                started = time.monotonic()
                messages = await self.run_probe(scenario)
                self.assertGreaterEqual(messages[0][0] - started, 0.025)
                self.assertIn(b"synthetic", messages[-1][1]["body"])
                self.assertFalse(messages[-1][1]["more_body"])

    async def test_partial_then_gap_and_early_eof(self):
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 60)}):
            messages = await self.run_probe("partial-gap")
            self.assertIn(b"event: partial", messages[1][1]["body"])
            self.assertGreaterEqual(messages[2][0] - messages[1][0], 0.025)
            early = await self.run_probe("early-eof")
            self.assertNotIn(b"event: done", b"".join(m.get("body", b"") for _, m in early))

    async def test_disconnect_before_headers_and_during_gap(self):
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 60)}):
            from app import wait_probe
            for scenario in ("first-byte", "partial-gap", "heartbeat"):
                messages = await self.run_probe(scenario, delay=10, disconnect_after=0.01)
                self.assertEqual(wait_probe._active, 0)
                self.assertNotIn(b"event: done", b"".join(m.get("body", b"") for _, m in messages))

    async def test_capacity_is_released(self):
        from app import wait_probe
        with patch.dict(os.environ, {"MANIFEST_WAIT_PROBE_UNTIL": str(time.time() + 60)}):
            with patch.object(wait_probe, "_active", 2):
                self.assertEqual((await self.run_probe("json"))[0][1]["status"], 429)


if __name__ == "__main__":
    unittest.main()
