---
name: Quota time and provenance
description: Why operational generation counting must be separate from provenance timestamps
---

Keep the original minting timestamp when generation crosses UTC midnight.
Treat newly tracked successful generation capacity as completion-day usage,
without counting the saved venture and its consumed reservation twice.

**Why:** Counting only by the original timestamp can drop work from today's
capacity as soon as an overnight reservation completes. Moving that timestamp
to completion time would instead change the input to the original provenance
proof. These are different concepts, not interchangeable clocks.

**How to apply:** When changing global daily counting, rollover, or recovery,
use operational admission/completion data for quotas and leave the venture's
original provenance intact. Retain historical counting for records that predate
durable admission tracking.