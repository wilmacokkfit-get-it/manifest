---
name: Proxy identity trust
description: Replit preview observations are not an authenticated identity or production proxy contract
---

Do not treat a Replit preview observation as a production trust guarantee, or
infer a trusted proxy range from private addresses.

**Why:** A preview probe on 2026-10-03 replaced a forged forwarded address but
passed a forged visitor ID unchanged. Replit's documentation search did not
establish a visitor-ID credential, stable proxy allowlist, or a forwarding
sanitization contract. Provider credential injection also does not reliably
distinguish Replit preview from production.

**How to apply:** Use the server's explicit trusted-proxy boundary rather than
re-parsing raw headers. Verify the target environment's routing before widening
its allowlist; a wildcard restores first-forwarded-value spoofing. Treat
conservative intermediary grouping as an operational limitation to validate,
not a reason to trust attacker-supplied identity.