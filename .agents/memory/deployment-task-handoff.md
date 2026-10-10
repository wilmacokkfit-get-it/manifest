---
name: Deployment-dependent task handoff
description: Apply isolated task changes before using republish to verify new production routes
---

For work inside an isolated task, confirm its changes are applied to the main
project before asking the user to republish for production verification.

**Why:** A successful republish can still omit a task's new backend route.
Matching an unchanged frontend asset does not prove the backend changes were
published. Repeating republish before applying the task does not resolve this.

**How to apply:** Consult deployment metadata and verify a diagnostic version
header or equivalent backend marker, not just build success. If live testing
depends on first completing and applying the setup, get the user's agreement
to split the setup from live verification, document the unverified scope, and
queue it explicitly. Do not report local or preview results as production
observations; start any short-lived diagnostic enablement only after the code
is ready to be published.
