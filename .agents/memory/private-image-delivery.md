---
name: Private image delivery
description: Why private renders use an owner-checked proxy instead of signed storage URLs
---

Private render URLs must not themselves grant access. Prefer app delivery with a creator check on each request, not short-lived signed storage links.

**Why:** A signed URL is a bearer credential that can be shared and used by an outsider until it expires. That weakens the requirement that knowing a private image URL is insufficient to view it.

**How to apply:** Preserve this property when optimizing image delivery or adding a CDN. Released images can be anonymous, but a private image must still require creator identity; avoid redirects to transferable storage credentials.