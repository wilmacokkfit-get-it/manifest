---
name: Browser clock boundaries
description: Keeping browser waiting tests provider-free while observing real Performance entries
---

Use browser-native streaming response objects and fail-closed network interception
for waiting/disconnect checks. Fake elapsed time must not replace Chromium's
actual User Timing buffer.

**Why:** Playwright's fake Performance object discards measures by default, so a
test can paint elapsed feedback correctly yet observe no timing entries. Buffered
route responses also cannot exercise feedback between incremental SSE chunks.

**How to apply:** Preserve native measure/getEntries methods before installing the
clock and retain fake numeric start/end values. Use a secure synthetic origin
because request identity creation depends on secure-context crypto APIs.
Synthetic browser checks do not verify a hosting proxy's idle behavior.
