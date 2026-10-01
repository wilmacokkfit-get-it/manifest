---
name: Recovery fencing
description: Why recovery must compare ownership state as well as timestamps
---

Do not use an update timestamp alone as a recovery compare-and-swap guard. Compare the observed ownership state too, and use fresh tokens for replacement attempts.

**Why:** MANIFEST's timestamps have second precision. An abandoned claim can be cleared and replaced in the same second; a delayed recovery write guarded only by status and timestamp can then mistakenly clear the new claim.

**How to apply:** When extending recovery, include the claim-bearing JSON snapshot or equivalent ownership token in conditional writes. Keep expired-claim recovery and worker completion token-scoped even if timestamp precision improves later.