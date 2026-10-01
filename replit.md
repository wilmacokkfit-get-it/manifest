# Running MANIFEST on Replit

This imported project keeps its original FastAPI backend, vanilla HTML/CSS/JS frontend, and SQLite storage. No frontend build is required.

- Click Run to start the `Start application` workflow.
- Command: `python -m uvicorn server:app --host 0.0.0.0 --port 5000`.
- Open the web preview at `/`. Check `/api/health` for storage and provider settings.
- Python dependencies are listed in `requirements.txt`.
- SQLite data and locally generated images live under `data/`, which is ignored by Git. Do not delete this folder if you need to retain workspace data.

## AI configuration

Real text and image generation require `OPENAI_API_KEY` in Replit Secrets. The shared settings use `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-5-mini`, `IMAGE_PROVIDER=openai`, and `IMAGE_MODEL=gpt-image-1`. Generation uses the connected OpenAI account's credits. Never put keys in source files or chat.

Until the key is supplied, the page and non-AI routes can run, but generation cannot be verified. Explicitly selecting OpenAI avoids the imported app's automatic canned mock response when no credentials exist.

## Publishing later

The imported deployment command still serves port 8000. This setup only configures the workspace preview; it does not publish the app. Review durable storage before publishing: do not assume workspace SQLite files survive deployment restarts or redeployments. The existing Supabase backend is available if chosen explicitly. Obtain the actual published URL before setting `PUBLIC_URL`; do not invent one.