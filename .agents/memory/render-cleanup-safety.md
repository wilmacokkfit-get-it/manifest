---
name: Render cleanup safety
description: Why approval and stopping writers are required for render-attempt deletion
---

Keep render-attempt cleanup operator-controlled, with exact venture/attempt
approval and all writers stopped during deletion. Do not turn the inventory
into an automatic scheduled deletion job without a new concurrency design.

**Why:** Storage deletion and database reference checks cannot be committed
atomically across Supabase services. Worker leases bound normal ownership but
do not stop a delayed worker from uploading again. Retention and a fresh scan
alone cannot eliminate that race.

**How to apply:** Preserve explicit maintenance-mode approval for manual
cleanup. Any future automatic collector needs durable attempt retirement
fencing enforced by upload and record-write paths before deleting blobs.