---
name: Manifest request identities
description: Why recovery identities outlive generation quota reservations and leases
---

Keep manifest request identities single-use and durable beyond the quota ledger's
retention window. Recover completion from the atomic saved venture, not a worker's
terminal flag or request lease alone.

**Why:** A save can commit while its response is lost or cancellation is being
handled. Deleting or reclaiming its identity on lease expiry can then start a
second successful venture; trusting cleanup as failure can incorrectly imply
that the completed quota charge was released.

**How to apply:** Expired requests without a saved row remain unconfirmed, never
automatically restarted. Check creator ownership and the saved result before
interpreting failure flags. Explicit creator retirement may resolve abandonment
only with a durable fence serialized against every save path, including work
exempt from quotas. Keep the identity single-use after retirement; a completed
save still wins and remains recoverable. Any retention/cleanup design must
preserve permanent deduplication for saved requests and these fences.

Preserve the original unidentified generation API while an external recovery
migration is pending. Any browser compatibility submission must be explicitly
labelled as lacking recovery, be restricted to a brand-new request after a
definite pre-generation setup error, and never replay an unconfirmed request.

**Why:** Requiring a new external table before a manually applied migration
otherwise disables every existing generation. Silently downgrading an existing
request would instead risk generating a second venture after a lost save response.

**How to apply:** Keep identified requests fail-closed and test both pre-migration
generation and interruption paths without model/image calls or live records.

An old, untracked compatibility attempt may be dismissed only through an explicit
user confirmation of possible duplicate completion and another allowance charge.
Dismissal is not proof of failure or cancellation, and must not submit a new attempt.
Do not extend this browser-only escape to identified requests.

**Why:** Enabling database recovery cannot retroactively identify untracked work.
Permanently locking the browser strands creators, but silently retrying risks a
second saved venture. An informed, separate manual attempt preserves that distinction.

**How to apply:** Preserve creator access and saved ventures; remove only the
acknowledged legacy pending marker. Tracked ambiguous work still needs server-side
ownership checks and retirement fencing before allowing a replacement.
