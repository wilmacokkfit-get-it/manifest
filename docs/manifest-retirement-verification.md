# Connected Supabase retirement verification

Verified on October 11, 2026, against the Supabase database already configured
for this application. This was not the unrelated Replit-managed database.

## Authorization and installation

The user explicitly approved the complete retirement migration and temporary
fixture tests on the connected Supabase database. The existing application keys
do not provide SQL administration access, so the user applied the complete
`supabase/manifest-recovery.sql` transaction through Supabase's SQL Editor.
The user subsequently confirmed running the complete
`supabase/manifest-retirement-check.sql` script successfully.

The SQL Editor results were reported by the user, not directly observed by the
agent. The operator script checks the enabled insert trigger, RLS, and effective
table, column and function privileges for both `anon` and `authenticated`.
It exercises creator scoping, missing/live/expired requests, repeat retirement,
reserved and unlimited late-save rejection, unchanged admission consumption,
and saved-result precedence as `service_role`. It rolls back all fixture writes.

## Independently observed connected-database checks

- The `retired` column is now available through the backend REST API.
- The retirement RPC appears in the backend API schema and returns `null` for
  a random nonexistent creator/request pair, rather than a migration setup
  error. No existing request was retired through this probe.
- Anonymous table access and retirement RPC calls are denied with SQL
  permission error `42501`.
- All 4 requests and 34 ventures from the before-migration comparison remain
  present and unchanged. Comparison excludes the newly added `retired` column.
- No temporary `retirement-check-` requests, ventures or admissions remain
  after the user-reported SQL Editor check.

The application role cannot delete request records. No persistent live fixture
was therefore created through REST, and no additional DELETE privilege was
granted for testing. Operator fixtures stayed inside a rolled-back transaction.
No generation, image creation, quota refund or existing-request replay occurred.

## Disposable validation

`python -m unittest discover -s tests -p 'test_manifest_recovery.py' -v`
passed all 24 tests, including real disposable PostgreSQL and a regression
ensuring the operator script rolls back every fixture.

This verification does not claim published-host long-wait, two-tab, iPhone
backgrounding, or small-stream-fragment testing. Those are separate planned
checks. No application startup migration or publishing configuration changed.
