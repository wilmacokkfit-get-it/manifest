---
name: Request task observation
description: Dependency-sensitive task observation for isolated HTTP cancellation tests
---

Audit request descendants through inherited request context rather than filtering solely by coroutine qualified names.

**Why:** Newer AnyIO versions can wrap middleware coroutines, hiding their original qualified names. A name-filtered audit can report that cleanup finished while silently omitting middleware tasks. Async-generator finalizers are also scheduled as awaitables without a qualified name.

**How to apply:** Track the outer request and its context-associated descendants. Check completion before shutting down the disposable server; event-loop shutdown otherwise cancels leaked tasks and conceals the regression. Allow task creation for awaitables without coroutine metadata.
