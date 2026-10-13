---
name: Path work fencing
description: Compact path revision fences for private automated deliverables
---

Use unique compact revisions for persisted path work, not whole JSON documents
in REST query filters or second-resolution timestamps. Stamp regenerated paths
as well as edits; a null legacy revision must not match a reset/null path.

**Why:** Prepared deliverables can make a whole-path equality filter exceed HTTP
URL limits after the first successful task. Small mock fixtures conceal that
problem. Timestamps can also collide during concurrent work or regeneration.

**How to apply:** Require a non-null path and its prior revision when committing,
then assign a new unique revision. Retry unrelated edits from fresh state while
checking the worker's claim identity. Keep revisions internal and never restore
a reset path from an old work snapshot.
