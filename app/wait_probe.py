"""Opt-in, bounded, provider/store-free HTTP proxy diagnostics.

Enable only for a short window with MANIFEST_WAIT_PROBE_UNTIL (Unix seconds).
There is deliberately no database, image, generation, or owner-auth dependency.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Query
from starlette.responses import Response

from app.streaming import with_heartbeats

router = APIRouter()
log = logging.getLogger("uvicorn.error")
_active = 0
VERSION = "wait-probe-1"


def enabled():
    try:
        remaining = float(os.environ.get("MANIFEST_WAIT_PROBE_UNTIL", "0")) - time.time()
        # Fail closed for accidental permanent or excessively long enablement.
        return 0 < remaining <= 7200
    except ValueError:
        return False


def event(name, data):
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


class ProbeResponse(Response):
    """Listen for upstream disconnects, including before response headers."""

    def __init__(self, scenario, delay, probe_id):
        self.scenario, self.delay, self.probe_id = scenario, delay, probe_id
        super().__init__(media_type="application/json" if scenario == "json" else "text/event-stream")
        del self.headers["content-length"]
        self.headers.update({
            "cache-control": "no-store",
            "x-accel-buffering": "no",
            "x-manifest-wait-probe": VERSION,
            "x-probe-id": probe_id,
        })

    async def __call__(self, scope, receive, send):
        global _active
        if not enabled():
            await Response(status_code=404)(scope, receive, send)
            return
        if _active >= 2:
            await Response("Two probes are already running.", status_code=429)(scope, receive, send)
            return
        _active += 1
        started = time.monotonic()
        outcome = "cancelled"
        log.info("[wait-probe] id=%s scenario=%s delay=%s started", self.probe_id, self.scenario, self.delay)

        async def disconnect():
            while True:
                if (await receive())["type"] == "http.disconnect":
                    return

        async def body():
            if self.scenario == "partial-gap":
                yield event("partial", {"data": {
                    "name": "Synthetic proxy probe", "tagline": "Not a saved venture",
                }, "streaming": "tagline"})
            if self.scenario == "early-eof":
                yield event("partial", {"data": {"name": "Synthetic proxy probe"}, "streaming": "name"})
                return
            await asyncio.sleep(self.delay)
            yield event("done", {"synthetic": True, "delay": self.delay})

        async def respond():
            if self.scenario in ("first-byte", "json"):
                await asyncio.sleep(self.delay)
            await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
            if self.scenario == "json":
                await send({"type": "http.response.body", "body": b'{"synthetic":true}', "more_body": False})
                return
            source = body()
            # first-byte already waited before headers; don't wait twice.
            if self.scenario == "first-byte":
                await send({"type": "http.response.body", "body": event("done", {"synthetic": True}), "more_body": False})
                return
            stream = with_heartbeats(source) if self.scenario == "heartbeat" else source
            try:
                async for chunk in stream:
                    await send({"type": "http.response.body", "body": chunk.encode() if isinstance(chunk, str) else chunk,
                                "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            finally:
                await stream.aclose()

        worker = asyncio.create_task(respond())
        listener = asyncio.create_task(disconnect())
        try:
            done, _ = await asyncio.wait((worker, listener), return_when=asyncio.FIRST_COMPLETED)
            if worker in done:
                await worker
                outcome = "completed"
            else:
                outcome = "disconnected"
        finally:
            worker.cancel()
            listener.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(worker, listener, return_exceptions=True)
                _active -= 1
            log.info("[wait-probe] id=%s scenario=%s outcome=%s elapsed=%.3f",
                     self.probe_id, self.scenario, outcome, time.monotonic() - started)


@router.get("/api/diagnostics/wait-probe")
async def wait_probe(
    scenario: Literal["first-byte", "partial-gap", "json", "heartbeat", "early-eof"] = "first-byte",
    delay: int = Query(65, ge=1, le=310),
    probe_id: uuid.UUID | None = None,
):
    if not enabled():
        raise HTTPException(404)
    return ProbeResponse(scenario, delay, str(probe_id or uuid.uuid4()))
