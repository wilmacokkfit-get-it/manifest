# Render-attempt cleanup (operator-only)

This command inventories one exact render attempt and, only after explicit
approval, removes its unreferenced render files/objects. It does not change
venture records, migrate a database, initialize a store, or change bucket
privacy. Run it manually; it has no UI or automatic schedule.

## Dry run

From the repository root, choose the backend explicitly and provide both exact
identifiers:

```sh
python scripts/render_attempt_cleanup.py \
  --backend sqlite \
  --data-dir ./data \
  --venture-id venture-id \
  --attempt attempt-token
```

SQLite requires `--data-dir`; it opens only the existing `manifest.db` read-only
and inspects the existing `renders` directory. It does not create a directory
or database and does not run SQLite migrations.

For Supabase, use the same environment configuration as the application:
`SUPABASE_URL` and either `SUPABASE_SECRET_KEY` or the legacy
`SUPABASE_SERVICE_ROLE_KEY`; `SUPABASE_RENDERS_BUCKET` is optional and defaults
to `renders`. Keep credentials in the environment/secret manager and never
include them in command arguments. The CLI does not print credentials.

Dry run is the default. It prints a JSON inventory plan to stdout, including
candidate files/objects, protected entries with reasons, the exact backend
scope, and an `approval` SHA-256 digest. `--output FILE` also writes that plan
as JSON to a new file (stdout is still printed); existing files are never
overwritten. Review the complete plan and retain
the file unchanged; the digest approves that exact inventory, not a general
venture or bucket.

An optional `--retention-seconds` can raise the age threshold, but cannot lower
the implementation's minimum (at least 24 hours). For each candidate, the
render file/object change time, the venture's `updated_at`, and each known
worker lease deadline must all be at least the full retention period in the
past. Unknown or recent timestamps/leases prevent deletion.

## Approval and deletion

Stop all application workers and any other process that can write venture
records or render files/objects. Keep them stopped for the entire deletion
operation. Then rerun the CLI with the same explicit backend and identifiers,
the saved plan, its exact `approval` digest, and the required operator
assertion:

```sh
python scripts/render_attempt_cleanup.py \
  --backend sqlite \
  --data-dir ./data \
  --venture-id venture-id \
  --attempt attempt-token \
  --delete-plan ./reviewed-plan.json \
  --approve DIGEST_FROM_PLAN \
  --workers-stopped
```

Deletion performs a fresh inventory immediately before removing anything. The
plan must match the CLI venture ID, attempt token, and backend scope, and its
digest must match `--approve`; any inventory difference aborts deletion. If it
aborts, keep writers stopped, make and review a new dry run, and approve only
that new plan. The command deletes only exact approved candidate filenames or
object keys and prints the removed paths.

## What is protected and what is in scope

The target must be an existing venture whose completed replacement attempt
establishes that the requested attempt is abandoned. The inventory is
deliberately conservative:

- Missing ventures abort because ownership cannot be proved.
- Current attempts, legacy attempts without a completed replacement marker,
  pending/unknown venture work, and attempts referenced anywhere in venture
  records are protected.
- Objects referenced by records are protected. SQLite hard-linked files,
  symlinks, and non-regular files are not candidates. Storage objects with
  unknown creation/modification age are protected.
- Only files matching the exact venture/attempt filename pattern in the
  existing local `renders` directory, or image objects in the exact
  `venture-id/attempt-token` Storage prefix, are considered. For Storage, only
  numeric `.png` and `.webp` object names are in scope.

This is not a bucket cleanup: it never clears a bucket, deletes a broad
prefix/folder, or removes venture records. Do not build a recurring job around
it. A new dry run and an independent explicit approval are required for every
deletion.