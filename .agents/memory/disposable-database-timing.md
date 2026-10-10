---
name: Disposable database setup timing
description: Why merge checks use UTC and memory-backed temporary fixture storage
---

Use explicit UTC and memory-backed temporary storage for disposable merge-test
databases when available. Keep application storage and durability settings unchanged.

**Why:** In this environment, PostgreSQL initialization exhausted its deadline
detecting the host time zone and syncing its disposable data directory. SQLite
fixture syncs also stretched the full verification past the merge timeout.
Increasing the timeout alone did not fix the PostgreSQL startup failure.

**How to apply:** Honor the test temporary-directory setting, retain all actual
database assertions, and preserve files after uncertain shutdown. Only remove an
empty temporary parent directory; never recursively clean up a potentially live
cluster. Memory-backed fixtures verify process-level recovery, not host power-loss
durability.
