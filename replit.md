# Running MANIFEST on Replit

This imported project keeps its original FastAPI backend, vanilla HTML/CSS/JS frontend, and existing SQLite/Supabase storage adapters. No frontend build is required. The active backend is Supabase (`STORE=supabase`); existing local SQLite data has not been moved or deleted.

- Click Run to start the `Start application` workflow.
- Command: `python -m uvicorn server:app --host 0.0.0.0 --port 5000`.
- Open the web preview at `/`. Check `/api/health` for storage and provider settings.
- Python dependencies are listed in `requirements.txt`.
- SQLite data and locally generated images live under `data/`, which is ignored by Git. Do not delete this folder if you need to retain workspace data.

## After task merges

The configured hook `scripts/post-merge.sh` installs `requirements.txt` into the project-local Python package directory and runs `python -m unittest discover -s tests -v`. It is non-interactive and stops on failure. Replit handles workflow reconciliation after the hook succeeds. The tests use disposable storage, not workspace data.

## AI configuration

Generated plans are checked before saving for recognizable unsupported approval
claims, refundable-deposit revenue and unsafe review order. These conservative
checks do not certify safety. See `docs/plan-quality.md` for the provider-free
four-case evaluation, human rubric and explicitly bounded opt-in live runner.

Real text and image generation require `OPENAI_API_KEY` in Replit Secrets. The shared settings use `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-5-mini`, `IMAGE_PROVIDER=openai`, and `IMAGE_MODEL=gpt-image-1`. Generation uses the connected OpenAI account's credits. Never put keys in source files or chat.

The OpenAI key has been supplied securely. Real streaming venture generation, private-owner access, SQLite saving, and both product image renders were verified. Explicitly selecting OpenAI avoids the imported app's automatic canned mock response if credentials are removed later.

## Publishing later

The publishing command uses `python -m uvicorn server:app --host 0.0.0.0 --port 5000`, matching the configured port forwarded to external port 80. Changes to publishing settings take effect only after republishing. Supabase is the active durable storage backend; do not assume workspace SQLite files survive deployment restarts or redeployments. Obtain the actual published URL before setting `PUBLIC_URL`; do not invent one.

## Supabase connection

Use `SUPABASE_URL` and `SUPABASE_SECRET_KEY` in Secrets. Legacy `SUPABASE_SERVICE_ROLE_KEY` is also supported. Modern secret keys are sent only as `apikey`, not as JWT bearer tokens. Bare Supabase project hostnames are normalized to HTTPS.

Run the full `supabase/migration.sql` in Supabase's SQL Editor, including its final grants to `service_role`. RLS bypass does not replace SQL table privileges. Database read/write access and image upload/retrieval have been verified with temporary records that were removed after verification.

Generation quotas additionally require `supabase/generation-admission.sql` after the base migration. Apply it explicitly to the target Supabase project; startup does not migrate remote databases. Without these backend-only RPCs, ordinary generation/refinement fail closed. See `docs/generation-admission.md` for reservation counting and crash/cancellation semantics.

Interrupted manifest recovery additionally requires `supabase/manifest-recovery.sql` after those migrations. The browser retains a single-use request UUID and checks a creator-only lookup after disconnect/reload; recovery never starts another generation or refunds a completed save. SQLite installs its request table automatically. Before Supabase setup, the original API without request IDs still generates ventures; the browser explicitly warns that recovery is unavailable and uses it only for brand-new submissions, never to replay an unconfirmed request. See `docs/manifest-recovery.md` for states and disposable checks.

Caller quotas use Uvicorn's trusted-proxy client address, never raw visitor/forwarded headers. `TRUST_VISITOR_HEADER=1` is disabled. Keep proxy trust explicit, never `FORWARDED_ALLOW_IPS=*`; the default trusts only loopback and can group users behind an intermediary until its verified proxy chain is configured. See `docs/caller-identity.md` for the preview audit and deployment validation boundary.

`STORE=sqlite` keeps local data in use even when Supabase secrets exist. Set `STORE=supabase` after verifying the database permissions; missing secrets fail explicitly. The default `STORE=auto` selects Supabase when its settings exist, otherwise SQLite. Switching backends does not migrate or delete existing SQLite ventures or images.

## Image privacy

Render delivery checks venture visibility and the creator cookie on every request, including legacy local `/renders/` URLs. Supabase startup updates the configured bucket to private in place and verifies it before serving; failure stops startup. Run the migration to create the bucket on new installations. Existing images and stored references are retained; views translate legacy Supabase public URLs to protected app URLs. Do not grant browser roles read access to render objects. Previously downloaded images or old public CDN caches cannot be recalled by the app.

## One optional refinement

After the first venture is generated, its creator can add up to 1,000 characters of context and refine it once, or keep the first result. Refinement waits for the original renders to finish, preserves the venture's link and original provenance, and regenerates its content and renders. Failed generation leaves the first result intact and allows a retry. The limit and skip state persist in existing JSON storage; no schema migration is required. Starting a path or releasing a venture closes the unused refinement opportunity.

Interrupted refinement/path claims expire after five minutes and are reconciled when an authorized visitor next loads the venture. Recovery preserves its content, provenance, and any previous path; a failed refinement becomes retryable, while choosing a path still consumes the optional refinement. Completed refinements remain one-time. Their pending renders resume on the next authorized load, with a ten-minute worker lease and attempt-specific storage objects so late workers cannot replace a newer attempt's images. These leases use existing JSON fields; no recovery migration or record deletion is needed.

`python tests/refinement_preview.py` serves isolated, disposable UI fixtures on localhost port 5001 for visual checks. It never uses workspace ventures or Supabase.
