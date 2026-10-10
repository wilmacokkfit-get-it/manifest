"""Keep quiet provider/save periods from looking like a dead SSE connection."""
import asyncio
import anyio
from starlette.responses import StreamingResponse


class HeartbeatStreamingResponse(StreamingResponse):
    async def stream_response(self, send):
        try:
            await super().stream_response(send)
        finally:
            # A disconnect can interrupt send() while the body is suspended at
            # yield, rather than inside its next-item await. Close it explicitly
            # so the producer is joined before the HTTP request is finished.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


async def with_heartbeats(source, interval=10):
    # Drive every yield in one task. AnyIO cancel scopes inside a source must
    # enter and exit in the same task, even across multiple yielded events.
    queue = asyncio.Queue(maxsize=8)

    async def produce():
        try:
            async for item in source:
                await queue.put(("item", item))
            await queue.put(("end", None))
        except Exception as error:
            await queue.put(("error", error))
        finally:
            await source.aclose()

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(queue.get(), timeout=interval)
            except TimeoutError:
                yield ": still-working\n\n"
                continue
            if kind == "end":
                return
            if kind == "error":
                raise value
            yield value
    finally:
        # Starlette cancels its whole response scope on disconnect. Shield the
        # join so that cancellation isn't propagated into the worker twice,
        # interrupting its quota/recovery cleanup after the first cancellation.
        with anyio.CancelScope(shield=True):
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
