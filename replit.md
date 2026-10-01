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

Real text and image generation require `OPENAI_API_KEY` in Replit Secrets. The shared settings use `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-5-mini`, `IMAGE_PROVIDER=openai`, and `IMAGE_MODEL=gpt-image-1`. Generation uses the connected OpenAI account's credits. Never put keys in source files or chat.

The OpenAI key has been supplied securely. Real streaming venture generation, private-owner access, SQLite saving, and both product image renders were verified. Explicitly selecting OpenAI avoids the imported app's automatic canned mock response if credentials are removed later.

## Publishing later

The publishing command uses `python -m uvicorn server:app --host 0.0.0.0 --port 5000`, matching the configured port forwarded to external port 80. Changes to publishing settings take effect only after republishing. Supabase is the active durable storage backend; do not assume workspace SQLite files survive deployment restarts or redeployments. Obtain the actual published URL before setting `PUBLIC_URL`; do not invent one.

## Supabase connection

Use `SUPABASE_URL` and `SUPABASE_SECRET_KEY` in Secrets. Legacy `SUPABASE_SERVICE_ROLE_KEY` is also supported. Modern secret keys are sent only as `apikey`, not as JWT bearer tokens. Bare Supabase project hostnames are normalized to HTTPS.

Run the full `supabase/migration.sql` in Supabase's SQL Editor, including its final grants to `service_role`. RLS bypass does not replace SQL table privileges. Database read/write access and image upload/retrieval have been verified with temporary records that were removed after verification.

`STORE=sqlite` keeps local data in use even when Supabase secrets exist. Set `STORE=supabase` after verifying the database permissions; missing secrets fail explicitly. The default `STORE=auto` selects Supabase when its settings exist, otherwise SQLite. Switching backends does not migrate or delete existing SQLite ventures or images.

## Image privacy

Render delivery checks venture visibility and the creator cookie on every request, including legacy local `/renders/` URLs. Supabase startup updates the configured bucket to private in place and verifies it before serving; failure stops startup. Run the migration to create the bucket on new installations. Existing images and stored references are retained; views translate legacy Supabase public URLs to protected app URLs. Do not grant browser roles read access to render objects. Previously downloaded images or old public CDN caches cannot be recalled by the app.
