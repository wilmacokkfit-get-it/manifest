# Recovering interrupted generation

The browser persists a UUID and the original sentence/mix reference before
`POST /api/manifest`. On an interrupted response it independently polls
`GET /api/manifest/requests/{UUID}`. Reloading or reconnecting also checks the
pending request. Recovery only fetches a result; it never automatically submits
another generation. Storage-disabled browsers can recover within the current tab.

The creator cookie must be retained. Verified app owners can also recover
requests belonging to their pre-login browser cookie. Knowing a request UUID
does not grant access: another creator receives the same 404 as a missing request,
even if the resulting venture has been released publicly. Lookup responses are
private and not cached.

## Supabase setup

After `supabase/migration.sql` and `supabase/generation-admission.sql`, explicitly
apply **`supabase/manifest-recovery.sql`** to the target Supabase project. This is
an additive, backend-only table with RLS and no browser-role privileges. Startup
does not migrate a remote database. SQLite creates its table automatically.
Existing ventures are unchanged; older generations without a request record
remain accessible through their existing links, not this lookup.

Until the migration is installed, requests containing a recovery UUID and
lookups return an explicit setup 503 before quota/model work. The original
API without a request UUID still works. For a brand-new request only, the browser
can use that compatibility API after receiving the explicit setup error and
shows that automatic recovery is unavailable. It never downgrades or resubmits a
previously unconfirmed request. Interrupted compatibility requests stay blocked
from resubmission and direct the creator to check their ventures. This preserves
existing generation without falsely promising durable recovery before setup.

## Permanent request identities

Each `(creator, request UUID)` can start work at most once. The insert uses a
database uniqueness constraint, not a process-local lock. The request fixes a
server-generated venture ID before quota reservation or model generation.
An unchanged replay returns its current state/result without reserving quota or
calling a model; changing the sentence or mix yields 409.

The existing atomic venture insert and admission consumption remain the completion
marker. Recovery checks that venture ID first, regardless of whether cleanup marked
the worker failed or its lease expired. A lost save response therefore cannot refund
a completed charge or create a duplicate. Request records are not expired/deleted
with the short-lived quota ledger.

States are `pending`, `completed` (with the creator's venture), `retired`, `failed` (worker
finished without a saved row), or `unconfirmed` (expired/abandoned work without
proof of completion). A lease expiring is not proof that an ambiguous remote
commit failed. Unknown states remain recoverable rather than restarting.

A failed request stays single-use. The browser clears a confirmed failure so the
creator's next explicit submission can use a new UUID. For an unconfirmed request,
submitting the original sentence reuses the UUID safely; changing the sentence is
blocked until the original result is reconciled. The browser restores the pending
sentence and mix reference after reload.

## Explicit retirement after a crash

Once a request's five-minute lease expires, its creator can choose **Retire
abandoned request** and confirm the permanent action. The creator-only
`POST /api/manifest/requests/{UUID}/retire` never generates anything. It shares
the commit transaction lock, checks for a saved venture first, and otherwise
sets a permanent `retired` tombstone. A database insert trigger rejects late
saves for that venture ID, both with quota reservations and for unlimited
app-owner generation. Lease expiry merely enables this explicit action; it is
not itself evidence of failure. No admission is refunded by retirement.

If the save wins the race, the original result is recovered. If retirement wins,
the original UUID remains single-use and the browser may clear it only after
confirming `retired`. Lost/offline retirement responses preserve the ID and are
resolved by lookup, including after reload. Live work returns `pending`, while
missing and other creators' requests return identical 404s. Already-saved
ventures remain recoverable even after quota ledger cleanup.

**Existing Supabase installations must reapply `supabase/manifest-recovery.sql`**
to install the retirement column, RPC and atomic insert fence together. This
additive transaction changes no ventures and removes no data. Before it is
applied, the retirement endpoint fails explicitly and retains the pending
request; existing generation and saved-result recovery still work. Do not
install the retirement RPC without its commit trigger.

For an approved operator check on the connected project, run the entire
`supabase/manifest-retirement-check.sql` script in Supabase's SQL Editor. It checks
the insert trigger, RLS and both browser roles' effective table, column and
function privileges. Its isolated fixtures exercise live/expired requests,
creator scoping, repeat retirement, reserved/unlimited late-save rejection and
saved-result precedence using `service_role`. All fixture writes roll back.
Do not split the script or substitute an existing request ID. This needs SQL
Editor access because the application role intentionally has no request DELETE
grant; do not add that grant just to clean up a live test.

## Checks

`python -m unittest discover -s tests -p 'test_manifest_recovery.py' -v` covers
disconnects before text, during streaming and immediately after atomic save,
ambiguous save responses, replay/concurrency, quota preservation, creator privacy,
login continuity, lease expiry and durable store reopening. Fixtures are disposable
and image generation is disabled.

`node tests/test_manifest_recovery.cjs` checks lookup-only recovery, offline/404
handling, storage persistence, independent request deadlines and ID-scoped clearing.
`node tests/test_manifest_submission.cjs` exercises actual submission and lost-stream
reconciliation, explicit pre-migration compatibility, and the prohibition on
downgrading or resubmitting unconfirmed requests.
These deterministic checks do not substitute for the separately planned real-browser
long-wait/disconnect checks.
