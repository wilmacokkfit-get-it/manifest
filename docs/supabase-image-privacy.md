# Live Supabase image privacy check

The default unittest suite mocks Supabase HTTP. This opt-in check uses real
PostgREST, Storage, and (for browser-policy coverage) Auth on the configured
Supabase project. It does **not** start or reconfigure the running application.
The app routes run in-process against the disposable Supabase fixtures.

## Safety

- Opt-in only: ordinary test runs skip these tests.
- Each credential mode creates a fresh `manifest-privacy-test-<random UUID>`
  public bucket, two tiny PNG objects, and a uniquely named private venture.
  With browser-policy coverage it also creates one disposable, confirmed Auth
  user with an `example.invalid` email. No email is sent by the check.
- No configured `SUPABASE_RENDERS_BUCKET` is used. Bucket creation never
  upserts, so collisions fail without changing the existing bucket.
- Only objects in the newly created bucket and the exact generated venture
  identity/owner are cleaned up. No migrations, policies, existing ventures,
  existing Auth users, or existing image objects are changed.
- Cleanup runs on failures too. A cleanup failure fails the test and prints
  its disposable bucket name. A killed process or a timed-out create can leave
  fixtures behind; inspect that specific run's fixtures before removing them.
- During its release check the disposable venture briefly becomes public.
  Prefer a separate staging Supabase project with the application's schema.
  The check intentionally exercises current policies; it does not install or
  repair them.

## Credentials

Configure these through workspace Secrets (never paste them into shell commands
or commit them):

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Project API URL |
| `SUPABASE_SECRET_KEY` | Modern backend key, starting with `sb_secret_` |
| `SUPABASE_SERVICE_ROLE_KEY` | Legacy service-role JWT from the same project |
| `SUPABASE_PUBLISHABLE_KEY` or `SUPABASE_ANON_KEY` | Browser API key from that project |

The modern and legacy tests are separate and do not fall back to one another.
Modern authentication sends `apikey` only; legacy authentication also sends
`Authorization: Bearer <service-role JWT>` through the actual `SupabaseStore`.
Backend keys never go in browser requests. Browser checks use the browser key
first as an anonymous role, then with a real access token obtained by signing
in the disposable Auth user. This requires password sign-in and Auth admin
access; disabled password sign-in fails explicitly.

The schema must already exist and the backend role must have its table grants.
Successful Storage access alone does not prove that PostgREST can write ventures.

## Run

Full acceptance check (both backend key formats plus browser policies):

```sh
SUPABASE_PRIVACY_INTEGRATION=1 python -m unittest discover -s tests -p 'test_supabase_image_privacy_live.py' -v
```

Focused modern-key check when only the modern backend key is available:

```sh
SUPABASE_PRIVACY_INTEGRATION=1 SUPABASE_PRIVACY_REQUIRE_BROWSER=0 \
  PYTHONPATH=tests:. python -m unittest test_supabase_image_privacy_live.SupabaseImagePrivacyLiveTests.test_modern_secret -v
```

That focused command is **not** a full acceptance pass: browser-role policies
are explicitly reported as NOT CHECKED when no browser API key is available,
and it does not exercise the legacy backend key. Missing required credentials
fail opted-in full checks rather than silently skipping them.

Normal offline regressions:

```sh
python -m unittest discover -s tests -v
```

## Assertions and interpreting failures

1. Anonymous access to the newly uploaded public fixture succeeds, warming its
   exact old public URL with a 60-second cache lifetime.
2. The real app lifespan calls `SupabaseStore.initialize()`, which updates that
   bucket to private and verifies it. Object listing/metadata, bytes, and the
   venture row are unchanged.
3. Both the warmed public URL and a cache-busted version must become denied.
   The check records elapsed time and safe CDN response headers. It polls for
   up to 90 seconds by default; set `SUPABASE_PRIVACY_CACHE_WAIT_SECONDS` to a
   value from 0 to 600 to change that observation window.
4. Public, authenticated-download, and ordinary object endpoints must deny
   requests without credentials, with an anonymous browser key, and with a
   signed-in browser user. Only 400/401/403/404 count as denial; redirects,
   rate limits, 5xx responses, and network failures do not count as privacy
   evidence.
5. The app proxy denies anonymous and wrong-owner requests before release,
   returns the exact bytes to the owner with `private, no-store` and `nosniff`,
   then allows anonymous delivery after the owner's real release API call.
   Release retains the old stored reference and leaves direct Storage access
   denied.

If a browser-role request succeeds after privatization, inspect SELECT policies
on `storage.objects` for grants to `anon`, `authenticated`, or `public`, including
project-wide policies. Making a bucket private does **not** revoke permissive
object policies. The migration deliberately does not delete arbitrary project
policies; do not change those automatically on a shared project.

A warmed URL that still succeeds while the cache-busted URL fails suggests
cached exposure. Both succeeding may also reflect propagation or configuration
issues. The check fails if exposure outlasts the configured window. Eventual
denial is reported with its measured delay, not presented as instantaneous
revocation. Already downloaded images or browser caches cannot be recalled.
One fixture cannot establish a maximum propagation delay for every CDN region.
The disposable bucket catches project-wide browser-readable policies, but does
not prove that bucket-specific policies on the application's existing renders
bucket are safe. Audit those policies separately without changing user objects.

## Read-only audit of the configured renders bucket

Run this **in addition to**, not instead of, the disposable live tests. A policy
such as `bucket_id = 'renders'` will not match their random fixture buckets.
The app's `SupabaseStore.initialize()` verifies bucket visibility only; it cannot
inspect PostgreSQL policies through the Storage API. A successful startup is
not a policy-audit pass.

### Generate and run the audit

1. Confirm which Supabase project and runtime configuration you are auditing.
   Development and production can use different projects or bucket names; audit
   each independently. Use an operator account with permission to read the
   project's SQL catalogs and bucket metadata. Backend Storage/PostgREST API
   keys are **not** SQL-editor credentials.
2. In the application's environment, generate the query:

   ```sh
   python scripts/render_storage_policy_audit.py
   ```

   This reads only `SUPABASE_RENDERS_BUCKET`, defaulting to `renders` only when
   unset, exactly like the app. It does not read keys, import the app, connect,
   initialize a bucket, or create fixtures. For a different runtime, explicitly
   supply its non-secret bucket name:

   ```sh
   SUPABASE_RENDERS_BUCKET=my-private-renders python scripts/render_storage_policy_audit.py
   ```

   Empty/control-character bucket names fail explicitly. SQL literals are
   escaped, including quotes and backslashes. The source
   `supabase/storage-policy-audit.sql` is a template, not a migration; use the
   generator rather than manually replacing its marker.
3. Paste the generated SQL into **that project's** Supabase SQL editor and run
   the whole script. It runs inside `BEGIN TRANSACTION READ ONLY` and ends with
   `ROLLBACK`. It reads `storage.buckets` metadata for the exact configured ID
   and PostgreSQL catalogs only. It never selects `storage.objects` rows,
   downloads images, evaluates policy functions, or changes grants/policies.
   If execution fails in a persistent SQL session, issue `ROLLBACK` to end the
   failed transaction. Errors or unavailable access mean **NOT AUDITED**, not
   “no policies found.”
4. Save the returned `storage_privacy_audit` JSON securely with the project,
   configured bucket, audit timestamp, operator, review outcome and rationale.
   Policy expressions can contain project-sensitive literals; redact them
   before sharing publicly. No object paths, owner tokens or render bytes are
   needed for this review.

### Interpret every grant and policy

The report deliberately inventories **all** `storage.objects` SELECT and ALL
policies. It does not filter expressions by bucket-name text: rules may be
project-wide, use arrays, prefixes, `OR`, helper functions, subqueries or role
inheritance without spelling the bucket name. Expressions are displayed,
**never executed**, and the output always says `OPERATOR REVIEW REQUIRED`.

| Report field | What to review |
| --- | --- |
| `bucket` | Must exist and have `public: false`. `null` means the configured bucket is missing or invisible to this operator; not a pass. Public buckets expose downloads regardless of object SELECT RLS. |
| `objects_table` | Must exist with `rls_enabled: true`. Record its owner and `rls_forced`; table owners normally bypass RLS unless forced. |
| `browser_roles` | Both `anon` and `authenticated` must exist. Records effective schema usage, table/column SELECT privileges, ownership, superuser and RLS bypass. Missing roles/null fields are incomplete evidence. A bypass or owner role with read privileges defeats normal RLS protection. |
| `table_select_grants` | Lists explicit SELECT ACL entries, including `public` (all roles), `anon`, `authenticated` and custom roles. Effective inherited/public and column-only access is also reported under `browser_roles`. Table SELECT is common in Supabase and alone is not proof of exposure when RLS is enforced. |
| `select_and_all_policies` | Record policy name, command, mode, all granted roles, effective browser roles, and full USING expression. `public` policies apply to both browser roles; inherited roles are included via `pg_has_role(..., 'USAGE')`. `ALL` authorizes SELECT too. |

For each browser-applicable policy, determine whether its **USING** condition
can match any render in this exact bucket, including legacy paths. For SELECT,
`WITH CHECK` does not restrict reading. A missing USING expression on an
applicable permissive policy is unconditional. Permissive policies combine
with **OR**, then applicable restrictive policies combine with **AND**; a
restrictive policy alone does not grant access. Do not assume one restrictive
rule cancels every permissive rule without reviewing role applicability and
the combined expressions.

MANIFEST uses a creator cookie and backend Storage access, not Supabase Auth
ownership for image delivery. An `authenticated` policy permitting “only the
Storage owner” is not automatically equivalent to the app's creator check.
For this app's backend-only bucket, prefer no browser-applicable permissive
SELECT/ALL policy capable of matching renders. For policies scoped exclusively
to other buckets, record why they cannot match the configured bucket.
Inspect referenced helper-function definitions and dependent rules read-only
when needed; if their behavior cannot be established, record **UNRESOLVED**.
Do not run arbitrary helpers on existing object rows as an audit shortcut.

An audit pass requires a private bucket, intact RLS, no browser bypass, and
documented exclusion of the configured renders from every effective browser
read path. Grants/policies may change: rerun after permission or bucket-config
changes. This review does not prove immediate CDN revocation or cover already
downloaded copies, privileged backend access, leaked signed URLs, or unrelated
custom privileged endpoints.

If a rule could expose renders, record it as **EXPOSURE RISK**, keep the privacy
acceptance result incomplete, and have the project owner approve a narrowly
scoped correction. Do not automatically drop shared policies, revoke shared
grants, move objects, or probe existing image URLs. The migration deliberately
does none of those things. Its literal bucket is `renders`; it does not provision
custom `SUPABASE_RENDERS_BUCKET` names.

### Current configured-bucket audit status

2026-10-01: **NOT AUDITED**. Available backend and browser API keys support the
disposable API tests but do not provide SQL-catalog access. No live
`storage.objects` SELECT/ALL policy definitions or anon/authenticated/public
SQL grants have been retrieved for the existing configured renders bucket.
Therefore no claim is made that bucket-specific grants are absent or safe.
The observations below cover only disposable buckets. An operator must run
the read-only audit above and record actual grants, expressions and review
outcome before treating configured-bucket policy protection as verified.

## Observations

2026-10-01, focused modern-secret run against the configured Supabase project:

- Real app initialization made the disposable public bucket private without
  changing the venture, object listing/metadata, or either object's bytes.
- A public response was still observed briefly after the bucket update; both
  the exact warmed URL and cache-busted URL were denied by the next probe,
  2.5 seconds after probing began. This is the polling observation, not a
  precise CDN invalidation duration or proof of the cause. Warm response:
  `cache-control: public, 60`, `cf-cache-status: MISS`; denied response:
  `cf-cache-status: BYPASS`.
- Uncredentialed direct public/authenticated/ordinary object requests were
  denied. Owner-only app delivery worked, anonymous app delivery worked after
  release, and Storage stayed private. All disposable fixtures were removed.
- Browser anon/authenticated policies and legacy service-role authentication
  were **not verified**: their credentials were unavailable. This focused run
  is not a full acceptance pass.

Do not infer live protection from mocked tests or a bucket's `public: false`
metadata alone. Later full runs should record their actual outcomes here.

2026-10-01, full acceptance run after both credential formats and a browser
publishable key were supplied:

| Check | Legacy service-role | Modern secret |
| --- | --- | --- |
| Private bucket, retained venture/object metadata/bytes | Passed | Passed |
| Initial warmed / cache-busted public URL status | 200 / 400 | 200 / 400 |
| First observed denial of both public URL variants | 4.5 seconds | 2.3 seconds |
| Uncredentialed direct object endpoints | Denied | Denied |
| Browser anon and signed-in authenticated object endpoints | Denied | Denied |
| Owner delivery before release / anonymous app delivery after release | Passed | Passed |
| Direct Storage remains denied after release | Passed | Passed |
| Disposable venture, objects, bucket, Auth user cleanup | Passed | Passed |

Both first warmed responses after the bucket update reported
`cf-cache-status: HIT` with `cache-control: public, 60`; the cache-busted
requests were already denied. Final denied responses reported
`cf-cache-status: BYPASS`. This is evidence of a brief cached-public response,
not an immediately revoked URL. No unexpected browser-readable Storage policy
was observed for either disposable bucket.