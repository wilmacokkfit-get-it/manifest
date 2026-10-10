---
name: SSE cancellation scope ownership
description: Heartbeats must not move an async generator between tasks
---

Consume an SSE generator containing AnyIO cancellation scopes in one persistent task, even when inserting periodic heartbeat events.

**Why:** Launching a new task for every next-item request makes scopes entered before one yield exit in a different task. This fails after apparently successful text generation, before the venture saves.

**How to apply:** Use a bounded queue fed by one producer task; keep heartbeats in the consumer and cancel/await the producer on disconnect. Shield that join from Starlette's cancelled response scope so it does not cancel the worker again during durable cleanup. Test scopes spanning multiple yielded events and request cancellation against quota/recovery state.

Explicitly close the response body when the response's send loop exits.

**Why:** A disconnect can happen while the iterator is suspended at a yield and the response is sending that event. Relying on the next iterator await or garbage collection then leaves the producer and its durable cleanup running beyond HTTP completion.
