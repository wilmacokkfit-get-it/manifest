---
name: Disposable database cleanup
description: Why disposable database cleanup must preserve files when shutdown is uncertain.
---

Treat an unconfirmed shutdown as a visible cleanup failure and retain the disposable cluster for recovery. Do not replace this with unconditional temporary-directory cleanup, including automatic finalizers.

**Why:** Deleting a database's files is not a way to stop its server. A failed or timed-out shutdown may leave the process alive; preserving the directory is intentional safety behavior, not a temporary-file leak to fix.

**How to apply:** When changing local database test harnesses, delete their directories only after shutdown is confirmed. Keep the original test/startup error and the cleanup failure independently visible, with subprocess output and server diagnostics.