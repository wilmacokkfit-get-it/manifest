# Generation capacity and recovery

Ordinary `/api/manifest` requests reserve both visitor capacity and global
capacity **before** recent-concept lookup or provider work. SQLite uses
`BEGIN IMMEDIATE`; Supabase uses transaction-scoped PostgreSQL advisory locks
inside backend-only RPCs. Separate workers must use the same database.

## Supabase installation

After the existing `supabase/migration.sql`, apply
`supabase/generation-admission.sql` in the target Supabase project's SQL editor.
This is an additive migration: it does not change or delete existing ventures.
It creates a private reservation table and grants the four RPCs only to
`service_role`, not browser roles. The script reloads PostgREST's schema cache.
It is safe to reapply.

The application does not apply SQL to the remote database at startup. Until
this migration is installed, ordinary generation and refinement fail closed:
there is no fallback to process-local limits or unchecked provider calls.
SQLite creates its reservation table automatically in the existing local file.

## Counting rules

- Pending reservations count against the visitor's rolling hour/day limits.
- Successful generation consumes the reservation in the same transaction that
  inserts the venture. Visitor windows start at successful completion.
- Global capacity includes active generation reservations and today's successful
  generations (UTC). Newly tracked successes are counted by completion time,
  including work crossing midnight. Existing and exempt-owner ventures retain
  their original `created_at` counting. Tracked ventures are not counted twice.
- Refinement reserves the same visitor quota, but not a global generation slot.
  Once a venture's refinement claim succeeds, its visitor reservation is consumed
  before provider work. As before, a failed started refinement retains the usage
  charge, while an unavailable venture does not. The one-refinement claim and
  verified app-owner exemption are unchanged.
- Verified app owners bypass quotas, but not ownership checks or work claims.
  Owner-generated ventures still contribute to the global historical count,
  as before; owners are allowed to generate even when that count exceeds the cap.

## Failure, disconnects, and crashes

- Provider failure, preparation failure, or client cancellation releases a
  **pending** generation reservation with a shielded, token-scoped cleanup.
- Successful database commits are never refunded by cleanup, even if the response
  is lost or the client disconnects before receiving `done`.
- Repeating a successful commit with the same token returns the same venture,
  rather than inserting another result or charging twice.
- Abandoned pending work expires after five minutes. The next admission removes
  expired pending reservations and consumed visitor charges older than 24 hours.
  Generation text/preparation is bounded to two minutes; the commit itself checks
  the lease, so paused/late workers cannot save after capacity has been reclaimed.
- If a cleanup fails, capacity can remain occupied until the lease expires.
  Failures are logged; there is no unsafe refund of uncertain successful work.
- A stream that is never started also recovers by lease expiry.
- Visitor identification is unchanged. Quotas are shared across processes for
  the same caller identity, not a guarantee against changing identities.

## Disposable validation

`python -m unittest discover -s tests -p 'test_generation_admission.py' -v`
checks both actual adapters. SQLite tests use separate spawned processes.
Supabase tests route HTTP RPC requests to production SQL running in a new,
local-only PostgreSQL cluster with independent sessions. No remote credentials,
live provider, existing database, or remote data is used. The cluster shuts down
before its temporary directory is removed; failed shutdown preserves recovery
files. PostgreSQL tools must be on `PATH`.