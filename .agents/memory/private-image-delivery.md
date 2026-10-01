---
name: Private image delivery
description: Why private renders use an owner-checked proxy instead of signed storage URLs
---

Private render URLs must not themselves grant access. Prefer app delivery with a creator check on each request, not short-lived signed storage links.

**Why:** A signed URL is a bearer credential that can be shared and used by an outsider until it expires. That weakens the requirement that knowing a private image URL is insufficient to view it.

**How to apply:** Preserve this property when optimizing image delivery or adding a CDN. Released images can be anonymous, but a private image must still require creator identity; avoid redirects to transferable storage credentials.

Treat bucket privatization as a change that must be checked at the object endpoint, not proof of immediate public-link revocation.

**Why:** A disposable live check observed a brief readable public response after Supabase reported the bucket private. Private buckets also do not override permissive browser-role object policies.

**How to apply:** Probe both an already warmed public URL and a cache-busted URL. Check browser roles separately from requests with no API key, report observed delay, and never promise that previously public images can be recalled.